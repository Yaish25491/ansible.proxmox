# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_clone
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_clone import ProxmoxLxcCloneModule


class ModuleFailure(Exception):
    pass


class FakeClient:
    def __init__(self, api):
        self.api = api
        self.validate_pve_permissions = Mock()
        self.mark_changed = Mock()


def make_clone_module(params=None):
    """Create an LXC clone handler with isolated module and API dependencies."""
    module_params = {
        "node": "pve01",
        "vmid": 100,
        "newid": 101,
        "task_timeout": None,
        "description": None,
        "full": None,
        "hostname": None,
        "pool": None,
        "snapname": None,
        "storage": None,
        "target": None,
    }
    module_params.update(params or {})
    module = SimpleNamespace(
        params=module_params,
        check_mode=False,
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    client = FakeClient(Mock())
    resource = SimpleNamespace(clone=SimpleNamespace(post=Mock()), feature=SimpleNamespace(get=Mock()))
    handler = ProxmoxLxcCloneModule(module, client=client)
    handler.call_api = Mock(return_value=[])
    handler.vm_resource = Mock(return_value=resource)
    return handler


def test_clone_action_is_non_idempotent_and_checks_permissions():
    handler = make_clone_module()

    assert handler.is_action_needed() is True
    assert handler.is_action_needed() is True

    handler.client.validate_pve_permissions.assert_called_with(handler.api, "/vms")
    assert handler.client.validate_pve_permissions.call_count == 2


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("vmid", 99), ("vmid", 1000000000), ("newid", 99), ("newid", 1000000000)],
)
def test_clone_rejects_vmids_outside_supported_range(parameter, value):
    handler = make_clone_module({parameter: value})

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    assert parameter in handler.module.fail_json.call_args.kwargs["msg"]
    handler.client.validate_pve_permissions.assert_not_called()


def test_clone_rejects_same_source_and_destination_vmid():
    handler = make_clone_module({"newid": 100})

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    assert "newid must be different from vmid" in handler.module.fail_json.call_args.kwargs["msg"]


def test_clone_rejects_destination_vmid_in_use():
    handler = make_clone_module()
    handler.call_api.return_value = [{"vmid": "101"}]

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg="VMID 101 is already in use.")
    handler.vm_resource.return_value.clone.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_clone_rejects_storage_for_explicit_linked_clone(check_mode):
    handler = make_clone_module({"full": False, "storage": "local-lvm"})
    handler.module.check_mode = check_mode

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    assert "storage cannot be used with full=false" in handler.module.fail_json.call_args.kwargs["msg"]
    handler.vm_resource.return_value.clone.post.assert_not_called()


@pytest.mark.parametrize("source_containers", [None, [], [{"vmid": 102}]])
def test_clone_check_mode_rejects_missing_source_container(source_containers):
    handler = make_clone_module()
    handler.module.check_mode = True
    handler.call_api.side_effect = [[], source_containers]

    with pytest.raises(RuntimeError, match="module failed"):
        handler.is_action_needed()

    handler.module.fail_json.assert_called_once_with(msg="LXC container 100 is not available on node pve01.")


def test_source_validation_reads_lxc_inventory():
    handler = make_clone_module()
    source = {"vmid": "100", "template": 1}
    handler.call_api.return_value = [{"vmid": 99}, source]

    assert handler._validate_source_lxc() is source

    handler.call_api.assert_called_once_with(
        "Retrieving LXC containers from node pve01",
        handler.api.nodes("pve01").lxc.get,
    )


@pytest.mark.parametrize(
    ("full", "source", "features_requested"),
    [
        (True, {"template": 1}, False),
        (None, {"template": 0}, False),
        (None, {"template": 1}, True),
        (False, {"template": 0}, True),
    ],
)
def test_linked_clone_feature_check_depends_on_full_and_source_template(full, source, features_requested):
    handler = make_clone_module({"full": full, "snapname": "before-upgrade"})
    handler.call_api.return_value = {"hasFeature": 1}

    handler._validate_linked_clone_support(source)

    if features_requested:
        handler.call_api.assert_called_once_with(
            "Checking linked clone support for LXC container 100 on node pve01",
            handler.vm_resource.return_value.feature.get,
            feature="clone",
            snapname="before-upgrade",
        )
    else:
        handler.call_api.assert_not_called()


@pytest.mark.parametrize("has_feature", [0, "0", False])
def test_linked_clone_rejects_unsupported_storage_feature(has_feature):
    handler = make_clone_module({"full": False})
    handler.call_api.return_value = {"hasFeature": has_feature}

    with pytest.raises(RuntimeError, match="module failed"):
        handler._validate_linked_clone_support({"template": 1})

    assert "Linked clone feature is not supported" in handler.module.fail_json.call_args.kwargs["msg"]


def test_linked_clone_rejects_unknown_storage_feature():
    handler = make_clone_module({"full": False})
    handler.call_api.return_value = None

    with pytest.raises(RuntimeError, match="module failed"):
        handler._validate_linked_clone_support({"template": 1})

    assert "Unable to determine linked clone support" in handler.module.fail_json.call_args.kwargs["msg"]


@pytest.mark.parametrize("shared", [1, "1", True])
def test_cross_node_clone_accepts_shared_target_storage(shared):
    handler = make_clone_module({"full": True, "storage": "shared-lvm", "target": "pve02"})
    handler.call_api.return_value = {"shared": shared}

    handler._validate_target_storage()

    handler.call_api.assert_called_once_with(
        "Retrieving configuration for storage shared-lvm",
        handler.api.storage.return_value.get,
    )


def test_cross_node_clone_rejects_non_shared_target_storage():
    handler = make_clone_module({"full": True, "storage": "local-lvm", "target": "pve02"})
    handler.call_api.return_value = {"shared": 0}

    with pytest.raises(RuntimeError, match="module failed"):
        handler._validate_target_storage()

    assert "can't clone to non-shared storage" in handler.module.fail_json.call_args.kwargs["msg"]


@pytest.mark.parametrize("check_mode", [False, True])
def test_check_mode_and_normal_mode_are_non_idempotent_actions(check_mode):
    handler = make_clone_module()
    handler.module.check_mode = check_mode
    handler.call_api.side_effect = [[], [{"vmid": 100, "template": 0}]] if check_mode else [[]]

    assert handler.is_action_needed() is True

    assert handler.call_api.call_count == (2 if check_mode else 1)
    handler.client.mark_changed.assert_not_called()
    handler.vm_resource.return_value.clone.post.assert_not_called()


def test_clone_submits_only_required_options_and_waits(monkeypatch):
    handler = make_clone_module()
    handler.call_api.return_value = "UPID:pve01:vzclone"
    wait_for_task = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait_for_task)

    assert handler.perform_action() == {"vmid": 101}

    handler.call_api.assert_called_once_with(
        "Cloning LXC container 100 on node pve01 as LXC container 101",
        handler.vm_resource.return_value.clone.post,
        newid=101,
    )
    handler.client.mark_changed.assert_called_once_with()
    wait_for_task.assert_called_once_with("UPID:pve01:vzclone")


def test_clone_forwards_hostname_and_all_supported_options(monkeypatch):
    handler = make_clone_module(
        {
            "description": "Clone for staging",
            "full": True,
            "hostname": "staging-copy",
            "pool": "staging",
            "snapname": "before-upgrade",
            "storage": "local-lvm",
            "target": "pve02",
        }
    )
    handler.call_api.return_value = "UPID:pve01:vzclone"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    handler.perform_action()

    assert handler.call_api.call_args.kwargs == {
        "newid": 101,
        "description": "Clone for staging",
        "full": 1,
        "pool": "staging",
        "snapname": "before-upgrade",
        "storage": "local-lvm",
        "target": "pve02",
        "hostname": "staging-copy",
    }


@pytest.mark.parametrize(("full", "expected"), [(True, 1), (False, 0)])
def test_clone_encodes_full_as_proxmox_boolean(monkeypatch, full, expected):
    handler = make_clone_module({"full": full})
    handler.call_api.return_value = "UPID:pve01:vzclone"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    handler.perform_action()

    assert handler.call_api.call_args.kwargs["full"] == expected


@pytest.mark.parametrize("task_upid", [None, "not-a-task"])
def test_clone_rejects_response_without_task_upid(monkeypatch, task_upid):
    handler = make_clone_module()
    handler.call_api.return_value = task_upid
    wait_for_task = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait_for_task)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.perform_action()

    assert "did not return a task UPID" in handler.module.fail_json.call_args.kwargs["msg"]
    wait_for_task.assert_not_called()


def test_result_contains_destination_and_task_upid_only_after_clone():
    handler = make_clone_module()

    assert handler.get_action_result() == {"vmid": 101}
    assert handler.prepare_result(True, handler.get_action_result()) == {
        "changed": True,
        "clone": {"vmid": 101},
    }

    handler._task_upid = "UPID:pve01:vzclone"
    assert handler.prepare_result(True, handler.get_action_result()) == {
        "changed": True,
        "clone": {"vmid": 101},
        "task_upid": "UPID:pve01:vzclone",
    }


def test_main_registers_lxc_clone_arguments_and_runtime_group(monkeypatch):
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(proxmox_lxc_clone, "AnsibleModule", constructor)
    monkeypatch.setattr(proxmox_lxc_clone, "ProxmoxLxcCloneModule", handler_constructor)

    proxmox_lxc_clone.main()

    args = constructor.call_args.kwargs
    spec = args["argument_spec"]
    assert args["supports_check_mode"] is True
    assert spec["node"] == {"type": "str", "required": True}
    assert spec["vmid"] == {"type": "int", "required": True}
    assert spec["newid"] == {"type": "int", "required": True}
    assert spec["hostname"] == {"type": "str"}
    assert "name" not in spec
    assert spec["snapname"] == {"type": "str"}
    assert "format" not in spec
    handler_constructor.assert_called_once_with(module)
    handler.execute_action_module.assert_called_once_with()

    runtime_path = Path(__file__).resolve().parents[4] / "meta" / "runtime.yml"
    metadata = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    assert "proxmox_lxc_clone" in metadata["action_groups"]["proxmox"]
