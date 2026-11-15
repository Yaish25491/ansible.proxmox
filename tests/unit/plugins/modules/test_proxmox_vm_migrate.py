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
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_vm_migrate
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm_migrate import ProxmoxVmMigrateModule


def make_migrate_module(params=None, check_mode=False, preconditions=None):
    """Create a migration handler with a mocked Proxmox API.

    Args:
        params (dict, optional): Module parameter overrides.
        check_mode (bool): Whether the operation should only be predicted.
        preconditions (dict, optional): Migration preconditions API response.

    Returns:
        tuple: Migration handler and migrate endpoint.
    """
    module_params = {
        "api_user": "root@pam",
        "node": "pve01",
        "vmid": 100,
        "target": "pve02",
        "task_timeout": None,
        "bwlimit": None,
        "force": None,
        "migration_network": None,
        "migration_type": None,
        "online": None,
        "targetstorage": None,
        "with_conntrack_state": None,
        "with_local_disks": None,
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
    endpoint = client.api.nodes.return_value.qemu.return_value.migrate
    endpoint.get.return_value = preconditions or {
        "running": False,
        "allowed_nodes": ["pve02"],
        "not_allowed_nodes": {},
        "local_disks": [],
        "local_resources": [],
        "mapped-resource-info": {},
        "mapped-resources": [],
        "has-dbus-vmstate": True,
    }
    endpoint.post.return_value = "UPID:pve01:migrate"
    client.api.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    return ProxmoxVmMigrateModule(module, client=client), endpoint


@pytest.mark.parametrize("check_mode", [False, True])
def test_migrate_reads_preconditions_in_both_modes(check_mode):
    """Check target eligibility before predicting or performing migration.

    Args:
        check_mode (bool): Whether the migration is only predicted.
    """
    handler, endpoint = make_migrate_module(check_mode=check_mode)

    assert handler.is_action_needed() is True

    endpoint.get.assert_called_once_with(target="pve02")
    handler.client.validate_pve_permissions.assert_called_once_with(handler.api, "/vms")
    endpoint.post.assert_not_called()


def test_migrate_check_mode_returns_preconditions_without_post():
    """Predict a migration and return Proxmox's precondition details."""
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


@pytest.mark.parametrize("check_mode", [False, True])
def test_migrate_rejects_negative_bwlimit(check_mode):
    """Reject negative bandwidth limits before API access in both modes.

    Args:
        check_mode (bool): Whether the request is only being predicted.
    """
    handler, endpoint = make_migrate_module(params={"bwlimit": -1}, check_mode=check_mode)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_action_module()

    handler.module.fail_json.assert_called_once_with(msg="bwlimit must be zero or greater.")
    handler.client.validate_pve_permissions.assert_not_called()
    endpoint.get.assert_not_called()
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    ("api_user", "api_token_id"),
    [
        ("operator@pam", None),
        ("root@pve", None),
        ("root@pam", "root@pam!integration"),
    ],
)
@pytest.mark.parametrize(
    ("option", "value", "label"),
    [
        ("force", True, "force=true"),
        ("migration_network", "10.46.29.128/25", "migration_network"),
        ("migration_type", "secure", "migration_type"),
        ("migration_type", "insecure", "migration_type"),
    ],
)
def test_migrate_root_options_require_password_root(check_mode, api_user, api_token_id, option, value, label):
    """Reject root-only options unless using password-authenticated root@pam.

    Args:
        check_mode (bool): Whether the request is only being predicted.
        api_user (str): Configured API user.
        api_token_id (str, optional): Configured API token identifier.
        option (str): Root-only migration parameter.
        value: Requested parameter value.
        label (str): Parameter label included in the error.
    """
    handler, endpoint = make_migrate_module(
        params={"api_user": api_user, "api_token_id": api_token_id, option: value},
        check_mode=check_mode,
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_action_module()

    handler.module.fail_json.assert_called_once_with(
        msg="Migration options {0} require password authentication as api_user=root@pam; API tokens are not accepted.".format(label),
        required_api_user="root@pam",
        required_authentication="api_password",
    )
    handler.client.validate_pve_permissions.assert_not_called()
    endpoint.get.assert_not_called()
    endpoint.post.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        {"force": False},
    ],
)
def test_migrate_other_options_do_not_require_root(options):
    """Do not apply the root guard when force is false.

    Args:
        options (dict): Migration options that do not trigger the secure root guard.
    """
    handler, endpoint = make_migrate_module(params=dict({"api_user": "operator@pam"}, **options))

    assert handler.is_action_needed() is True

    handler.module.fail_json.assert_not_called()
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("migration_type", ["secure", "insecure"])
def test_migrate_check_mode_accepts_root_options_with_password_auth(migration_type):
    """Allow root-only options for password-authenticated root@pam."""
    handler, endpoint = make_migrate_module(
        params={
            "api_user": "root@pam",
            "api_password": "test-password",
            "force": True,
            "migration_network": "10.46.29.128/25",
            "migration_type": migration_type,
        },
        check_mode=True,
    )

    with pytest.raises(SystemExit):
        handler.execute_action_module()

    assert handler.module.exit_json.call_args.kwargs["changed"] is True
    handler.module.fail_json.assert_not_called()
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


def test_migrate_posts_options_and_waits(monkeypatch):
    """Forward migration options using the Proxmox API parameter names.

    Args:
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    handler, endpoint = make_migrate_module(
        params={
            "bwlimit": 0,
            "force": True,
            "migration_network": "10.0.0.0/24",
            "migration_type": "insecure",
            "online": True,
            "targetstorage": "local-lvm:shared-lvm",
            "with_conntrack_state": True,
            "with_local_disks": True,
        },
        preconditions={
            "running": True,
            "allowed_nodes": ["pve02"],
            "not_allowed_nodes": {},
            "local_resources": ["hostpci0"],
            "has-dbus-vmstate": True,
        },
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_action_module()

    endpoint.post.assert_called_once_with(
        target="pve02",
        bwlimit=0,
        force=1,
        migration_network="10.0.0.0/24",
        migration_type="insecure",
        online=1,
        targetstorage="local-lvm:shared-lvm",
        **{"with-conntrack-state": 1, "with-local-disks": 1},
    )
    wait.assert_called_once_with("UPID:pve01:migrate", include_log_on_failure=True)
    result = handler.module.exit_json.call_args.kwargs
    assert result["changed"] is True
    assert result["task_upid"] == "UPID:pve01:migrate"
    assert result["preconditions"] == endpoint.get.return_value
    assert handler.client._changed is True


@pytest.mark.parametrize("check_mode", [False, True])
def test_migrate_rejects_target_with_precondition_blockers(check_mode):
    """Fail in both modes when Proxmox reports blockers for the target.

    Args:
        check_mode (bool): Whether migration is only being predicted.
    """
    handler, endpoint = make_migrate_module(
        check_mode=check_mode,
        preconditions={
            "running": False,
            "allowed_nodes": ["pve03"],
            "not_allowed_nodes": {"pve02": {"blocking-ha-resources": ["vm:101"]}},
            "local_resources": [],
        },
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    failure = handler.module.fail_json.call_args.kwargs
    assert failure["msg"] == "Target node pve02 is not allowed for VM migration."
    assert failure["not_allowed_nodes"] == {"blocking-ha-resources": ["vm:101"]}
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("cdrom", [True, 1, "1"])
def test_migrate_rejects_local_cdroms(check_mode, cdrom):
    """Reject local CD/DVD devices before posting in normal and check mode.

    Args:
        check_mode (bool): Whether migration is only being predicted.
        cdrom (bool or int or str): Proxmox's CD-ROM flag representation.
    """
    disk = {"volid": "local:iso/installer.iso", "cdrom": cdrom}
    handler, endpoint = make_migrate_module(
        check_mode=check_mode,
        preconditions={
            "running": False,
            "allowed_nodes": ["pve02"],
            "not_allowed_nodes": {},
            "local_disks": [disk],
            "local_resources": [],
        },
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(
        msg="Cannot migrate VM with local CD/DVD devices.",
        local_disks=[disk],
    )
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


def test_migrate_allows_target_listed_by_preconditions_in_both_modes():
    """Accept a target returned in allowed_nodes for normal and check mode."""
    for check_mode in (False, True):
        handler, endpoint = make_migrate_module(check_mode=check_mode)

        assert handler.is_action_needed() is True

        endpoint.get.assert_called_once_with(target="pve02")
        endpoint.post.assert_not_called()


def test_migrate_rejects_target_not_allowed_even_with_storage_overrides():
    """Require target membership in allowed_nodes before any migration override.

    Storage options affect the migration request, but do not replace the
    precondition endpoint's allowed-target decision.
    """
    handler, endpoint = make_migrate_module(
        params={"targetstorage": "local-lvm:shared-lvm", "with_local_disks": True},
        preconditions={
            "running": False,
            "allowed_nodes": ["pve03"],
            "not_allowed_nodes": {"pve02": {"unavailable_storages": ["local-lvm"]}},
            "local_resources": [],
        },
    )

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(
        msg="Target node pve02 is not allowed for VM migration.",
        not_allowed_nodes={"unavailable_storages": ["local-lvm"]},
    )
    endpoint.get.assert_called_once_with(target="pve02")
    endpoint.post.assert_not_called()


@pytest.mark.parametrize(
    ("params", "preconditions", "message"),
    [
        ({}, {"running": True, "allowed_nodes": ["pve02"], "local_resources": []}, "set online=true"),
        ({}, {"running": False, "allowed_nodes": ["pve02"], "local_resources": ["hostpci0"]}, "local resources"),
        (
            {"with_conntrack_state": True, "online": True},
            {"running": True, "allowed_nodes": ["pve02"], "local_resources": [], "has-dbus-vmstate": False},
            "does not support migrating conntrack state",
        ),
    ],
)
def test_migrate_rejects_unmet_preconditions(params, preconditions, message):
    """Fail before POST when a requested migration cannot satisfy preconditions.

    Args:
        params (dict): Migration option overrides.
        preconditions (dict): Precondition response returned by Proxmox.
        message (str): Expected actionable failure message fragment.
    """
    preconditions.setdefault("not_allowed_nodes", {})
    handler, endpoint = make_migrate_module(params=params, preconditions=preconditions)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    assert message in handler.module.fail_json.call_args.kwargs["msg"]
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_migrate_rejects_vmid_outside_supported_range(vmid):
    """Reject VM identifiers outside the supported range before API access.

    Args:
        vmid (int): Invalid VM identifier.
    """
    handler, endpoint = make_migrate_module(params={"vmid": vmid})

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg="VMID must be between 100 and 999999999.")
    handler.client.validate_pve_permissions.assert_not_called()
    endpoint.get.assert_not_called()


def test_migrate_rejects_same_source_and_target_node():
    """Reject a migration targeting the VM's current node."""
    handler, endpoint = make_migrate_module(params={"target": "pve01"})

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg="target must be different from the source node.")
    endpoint.get.assert_not_called()


def test_migrate_main_registers_arguments(monkeypatch):
    """Expose the documented options and dispatch to the shared action lifecycle.

    Args:
        monkeypatch: Pytest fixture for replacing Ansible module construction.
    """
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(proxmox_vm_migrate, "AnsibleModule", constructor)
    monkeypatch.setattr(proxmox_vm_migrate, "ProxmoxVmMigrateModule", handler_constructor)

    proxmox_vm_migrate.main()

    arguments = constructor.call_args.kwargs
    spec = arguments["argument_spec"]
    assert spec["node"] == {"type": "str", "required": True}
    assert spec["vmid"] == {"type": "int", "required": True}
    assert spec["target"] == {"type": "str", "required": True}
    assert spec["bwlimit"] == {"type": "int"}
    assert spec["migration_type"]["choices"] == ["secure", "insecure"]
    assert spec["task_timeout"] == {"type": "int"}
    assert arguments["supports_check_mode"] is True
    handler_constructor.assert_called_once_with(module)
    handler.execute_action_module.assert_called_once_with()
