# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_migrate
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_migrate import ProxmoxLxcMigrateModule


def make_migrate_module(params=None, check_mode=False, preconditions=None):
    """Create an LXC migration handler with a mocked Proxmox API."""
    module_params = {
        "node": "pve01",
        "vmid": 100,
        "target": "pve02",
        "bwlimit": None,
        "restart": None,
        "target_storage": None,
        "timeout": 180,
        "task_timeout": None,
    }
    module_params.update(params or {})
    module = SimpleNamespace(
        params=module_params,
        check_mode=check_mode,
        fail_json=Mock(side_effect=RuntimeError("module failed")),
        exit_json=Mock(side_effect=SystemExit),
    )
    client = ProxmoxClient(module)
    client._api = Mock()
    client.validate_pve_permissions = Mock()
    endpoint = client.api.nodes.return_value.lxc.return_value.migrate
    endpoint.get.return_value = preconditions if preconditions is not None else {
        "running": False,
        "allowed_nodes": ["pve02"],
        "not_allowed_nodes": {},
    }
    endpoint.post.return_value = "UPID:pve01:lxc-migrate"
    client.api.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    return ProxmoxLxcMigrateModule(module, client=client), endpoint


@pytest.mark.parametrize("check_mode", [False, True])
def test_migrate_reads_preconditions_in_both_modes(check_mode):
    handler, endpoint = make_migrate_module(check_mode=check_mode)

    assert handler.is_action_needed() is True

    endpoint.get.assert_called_once_with(target="pve02")
    handler.client.validate_pve_permissions.assert_called_once_with(handler.api, "/vms")
    endpoint.post.assert_not_called()


def test_migrate_check_mode_returns_preconditions_without_post():
    handler, endpoint = make_migrate_module(check_mode=True)

    with pytest.raises(SystemExit):
        handler.execute_action_module()

    result = handler.module.exit_json.call_args.kwargs
    assert result["changed"] is True
    assert result["migration"] == {"node": "pve01", "target": "pve02", "vmid": 100}
    assert result["preconditions"] == endpoint.get.return_value
    assert "task_upid" not in result
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()
    assert handler.client._changed is False


def test_migrate_posts_options_and_waits(monkeypatch):
    handler, endpoint = make_migrate_module(
        params={
            "bwlimit": 1024.5,
            "restart": True,
            "target_storage": "local-lvm",
            "timeout": 90,
        },
        preconditions={"running": True, "allowed_nodes": ["pve02"], "not_allowed_nodes": {}},
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_action_module()

    endpoint.post.assert_called_once_with(
        target="pve02",
        bwlimit=1024.5,
        restart=1,
        **{"target-storage": "local-lvm", "timeout": 90},
    )
    wait.assert_called_once_with("UPID:pve01:lxc-migrate", include_log_on_failure=True)
    result = handler.module.exit_json.call_args.kwargs
    assert result["changed"] is True
    assert result["task_upid"] == "UPID:pve01:lxc-migrate"
    assert result["preconditions"] == endpoint.get.return_value


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("running", [True, 1, "1"])
def test_migrate_rejects_running_container_without_restart(check_mode, running):
    handler, endpoint = make_migrate_module(
        check_mode=check_mode,
        preconditions={"running": running, "allowed_nodes": ["pve02"]},
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_action_module()

    handler.module.fail_json.assert_called_once_with(
        msg="LXC container is running; set restart=true to migrate it."
    )
    handler.module.exit_json.assert_not_called()
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("field_name", ["allowed_nodes", "allowed-nodes"])
def test_migrate_checks_optional_allowed_nodes(field_name):
    handler, endpoint = make_migrate_module(
        preconditions={"running": False, field_name: ["pve02"]},
    )

    assert handler.is_action_needed() is True
    endpoint.get.assert_called_once_with(target="pve02")


@pytest.mark.parametrize("check_mode", [False, True])
def test_migrate_rejects_target_missing_from_allowed_nodes(check_mode):
    handler, endpoint = make_migrate_module(
        check_mode=check_mode,
        preconditions={"running": False, "allowed_nodes": ["pve03"]},
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(
        msg="Target node pve02 is not allowed for LXC migration.",
        not_allowed_nodes=None,
    )
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("field_name", ["not_allowed_nodes", "not-allowed-nodes"])
def test_migrate_rejects_target_with_precondition_blockers(check_mode, field_name):
    blockers = {"ha-resources": ["ct:101"]}
    handler, endpoint = make_migrate_module(
        check_mode=check_mode,
        preconditions={"running": False, field_name: {"pve02": blockers}},
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(
        msg="Target node pve02 is not allowed for LXC migration.",
        not_allowed_nodes=blockers,
    )
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize(
    ("preconditions", "message"),
    [
        ({}, "Unable to determine whether the LXC container is running."),
        ({"running": "unknown"}, "Unable to determine whether the LXC container is running."),
        ({"running": False, "allowed_nodes": "pve02"}, "Unable to determine nodes allowed"),
        ({"running": False, "not_allowed_nodes": []}, "Unable to determine nodes disallowed"),
    ],
)
def test_migrate_rejects_invalid_preconditions(preconditions, message):
    handler, endpoint = make_migrate_module(preconditions=preconditions)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    assert message in handler.module.fail_json.call_args.kwargs["msg"]
    endpoint.post.assert_not_called()


def test_migrate_accepts_missing_optional_allowed_nodes():
    handler, endpoint = make_migrate_module(preconditions={"running": False})

    assert handler.is_action_needed() is True
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_migrate_rejects_invalid_vmids(vmid):
    handler, endpoint = make_migrate_module(params={"vmid": vmid})

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg="VMID must be between 100 and 999999999.")
    handler.client.validate_pve_permissions.assert_not_called()
    endpoint.get.assert_not_called()


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"node": "pve02"}, "target must be different from the source node."),
        ({"bwlimit": -1}, "bwlimit must be zero or greater."),
    ],
)
def test_migrate_rejects_invalid_parameters(params, message):
    handler, endpoint = make_migrate_module(params=params)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg=message)
    endpoint.get.assert_not_called()


def test_migrate_main_registers_arguments(monkeypatch):
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(proxmox_lxc_migrate, "AnsibleModule", constructor)
    monkeypatch.setattr(proxmox_lxc_migrate, "ProxmoxLxcMigrateModule", handler_constructor)

    proxmox_lxc_migrate.main()

    arguments = constructor.call_args.kwargs
    spec = arguments["argument_spec"]
    assert spec["node"] == {"type": "str", "required": True}
    assert spec["vmid"] == {"type": "int", "required": True}
    assert spec["target"] == {"type": "str", "required": True}
    assert spec["bwlimit"] == {"type": "float"}
    assert "online" not in spec
    assert spec["restart"] == {"type": "bool"}
    assert spec["target_storage"] == {"type": "str"}
    assert spec["timeout"] == {"type": "int", "default": 180}
    assert arguments["supports_check_mode"] is True
    handler_constructor.assert_called_once_with(module)
    handler.execute_action_module.assert_called_once_with()
