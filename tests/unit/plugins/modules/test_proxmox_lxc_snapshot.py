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

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_snapshot, proxmox_lxc_snapshot_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_snapshot import ProxmoxLxcSnapshotModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_snapshot_info import ProxmoxLxcSnapshotInfoModule


@pytest.fixture
def snapshot_environment():
    """Create snapshot handlers backed by the real shared lifecycle helpers."""
    def create(handler_class=ProxmoxLxcSnapshotModule, check_mode=False, snapshots=None, **parameters):
        params = {
            "node": "pve01",
            "vmid": 100,
            "snapname": None if handler_class is ProxmoxLxcSnapshotInfoModule else "before_upgrade",
            "state": "present",
            "start": False,
            "description": None,
            "task_timeout": None,
        }
        params.update(parameters)
        module = SimpleNamespace(
            params=params,
            check_mode=check_mode,
            fail_json=Mock(side_effect=RuntimeError("module failed")),
            exit_json=Mock(side_effect=SystemExit),
        )
        client = ProxmoxClient(module)
        client._api = Mock()
        client.validate_pve_permissions = Mock()
        client.api.nodes.get.return_value = [{"node": params["node"]}]
        client.api.nodes.return_value.lxc.get.return_value = [{"vmid": params["vmid"]}]
        endpoint = client.api.nodes.return_value.lxc.return_value.snapshot
        endpoint.get.return_value = snapshots if snapshots is not None else [{"name": "current"}]
        endpoint.post.return_value = "UPID:pve01:snapshot"
        endpoint.return_value.config.put.return_value = None
        endpoint.return_value.delete.return_value = "UPID:pve01:snapshot-delete"
        endpoint.return_value.rollback.post.return_value = "UPID:pve01:snapshot-rollback"
        return handler_class(module, client=client), endpoint

    return create


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_snapshot_present_is_idempotent(snapshot_environment, monkeypatch, check_mode, exists):
    snapshots = [{"name": "current"}]
    if exists:
        snapshots.append({"name": "before_upgrade", "description": "Original"})
    handler, endpoint = snapshot_environment(check_mode=check_mode, snapshots=snapshots, description="Original")
    if exists:
        endpoint.return_value.config.get.return_value = {"description": "Original"}
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    expected = {"changed": not exists, "snapshot": snapshots[-1] if exists else {"name": "before_upgrade"}}
    if not exists and not check_mode:
        expected["task_upid"] = "UPID:pve01:snapshot"
    handler.module.exit_json.assert_called_once_with(**expected)
    if not exists and not check_mode:
        endpoint.post.assert_called_once_with(snapname="before_upgrade", description="Original")
        wait.assert_called_once_with("UPID:pve01:snapshot")
    else:
        endpoint.post.assert_not_called()
        wait.assert_not_called()


@pytest.mark.parametrize(
    "current",
    [
        {"name": "before_upgrade"},
        {"name": "before_upgrade", "description": ""},
        {"name": "before_upgrade", "description": "stale summary"},
    ],
)
def test_snapshot_present_uses_config_description_for_idempotency(snapshot_environment, current):
    handler, endpoint = snapshot_environment(
        snapshots=[current], description="Original",
    )
    endpoint.return_value.config.get.return_value = {"description": "Original"}

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(changed=False, snapshot=dict(current, description="Original"))
    endpoint.return_value.config.put.assert_not_called()
    endpoint.post.assert_not_called()


def test_snapshot_present_ignores_api_trailing_line_ending(snapshot_environment):
    handler, endpoint = snapshot_environment(
        snapshots=[{"name": "before_upgrade", "description": "Before the container upgrade1"}],
        description="Before the container upgrade1",
    )
    endpoint.return_value.config.get.return_value = {"description": "Before the container upgrade1\r\n"}

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(
        changed=False,
        snapshot={"name": "before_upgrade", "description": "Before the container upgrade1"},
    )
    endpoint.return_value.config.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_present_updates_different_description(snapshot_environment, check_mode):
    current = {"name": "before_upgrade", "description": "Old description"}
    handler, endpoint = snapshot_environment(check_mode=check_mode, snapshots=[current], description="New description")
    endpoint.return_value.config.get.return_value = {"description": "Old description"}

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(
        changed=True, snapshot=dict(current, description="New description"),
    )
    if check_mode:
        endpoint.return_value.config.put.assert_not_called()
    else:
        endpoint.return_value.config.put.assert_called_once_with(description="New description")


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_absent_is_idempotent(snapshot_environment, monkeypatch, check_mode):
    handler, endpoint = snapshot_environment(state="absent", check_mode=check_mode, snapshots=[{"name": "before_upgrade"}])
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    expected = {"changed": True, "snapshot": None}
    if not check_mode:
        expected["task_upid"] = "UPID:pve01:snapshot-delete"
    handler.module.exit_json.assert_called_once_with(**expected)
    if check_mode:
        endpoint.return_value.delete.assert_not_called()
        wait.assert_not_called()
    else:
        endpoint.return_value.delete.assert_called_once_with()
        wait.assert_called_once_with("UPID:pve01:snapshot-delete")


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("start", [False, True])
def test_snapshot_rollback_is_non_idempotent(snapshot_environment, monkeypatch, check_mode, start):
    snapshot = {"name": "before_upgrade"}
    handler, endpoint = snapshot_environment(
        state="rollback", check_mode=check_mode, start=start, snapshots=[snapshot],
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    for _attempt in range(2):
        with pytest.raises(SystemExit):
            handler.execute_resource_module()

    assert handler.module.exit_json.call_count == 2
    expected = {"changed": True, "snapshot": snapshot}
    if not check_mode:
        expected["task_upid"] = "UPID:pve01:snapshot-rollback"
    handler.module.exit_json.assert_called_with(**expected)
    if check_mode:
        endpoint.return_value.rollback.post.assert_not_called()
        wait.assert_not_called()
    else:
        assert endpoint.return_value.rollback.post.call_count == 2
        endpoint.return_value.rollback.post.assert_called_with(start=int(start))
        assert wait.call_count == 2


def test_snapshot_rollback_rejects_missing_snapshot(snapshot_environment):
    handler, endpoint = snapshot_environment(state="rollback")

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    handler.module.fail_json.assert_called_once_with(msg="Snapshot before_upgrade does not exist on LXC container 100.")
    endpoint.return_value.rollback.post.assert_not_called()


def test_snapshot_documentation_warns_running_rollback_leaves_container_stopped():
    documentation = yaml.safe_load(proxmox_lxc_snapshot.DOCUMENTATION)

    assert (
        "Rolling back a running container stops it and leaves it stopped unless C(start=true) is requested."
        in documentation["notes"]
    )


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_snapshot_modules_reject_invalid_vmids(snapshot_environment, vmid):
    for handler_class in (ProxmoxLxcSnapshotModule, ProxmoxLxcSnapshotInfoModule):
        handler, endpoint = snapshot_environment(handler_class=handler_class, vmid=vmid)
        with pytest.raises(RuntimeError, match="module failed"):
            handler.execute()
        handler.module.fail_json.assert_called_once_with(msg="VMID must be between 100 and 999999999.")
        endpoint.get.assert_not_called()


@pytest.mark.parametrize("snapname", ["", "a", "1a", "_aa", "has space", "aa\n", "éa", "a" * 41])
def test_snapshot_rejects_invalid_names(snapshot_environment, snapname):
    handler, endpoint = snapshot_environment(snapname=snapname)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.get_resource()

    assert "snapname must be 2-40 ASCII characters" in handler.module.fail_json.call_args.kwargs["msg"]
    endpoint.get.assert_not_called()


@pytest.mark.parametrize("snapname", ["current", "vzdump", "pending", "PENDING"])
def test_snapshot_rejects_reserved_names(snapshot_environment, snapname):
    handler, endpoint = snapshot_environment(snapname=snapname)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.get_resource()

    handler.module.fail_json.assert_called_once_with(msg="snapname must not be 'current', 'vzdump', or 'pending'.")
    endpoint.get.assert_not_called()


@pytest.mark.parametrize("snapshots", [None, []])
def test_snapshot_info_returns_empty_list(snapshot_environment, snapshots):
    handler, endpoint = snapshot_environment(handler_class=ProxmoxLxcSnapshotInfoModule, snapshots=snapshots)
    endpoint.get.return_value = snapshots

    assert handler.get_info() == []


def test_snapshot_info_returns_all_records(snapshot_environment):
    records = [
        {"name": "before_upgrade", "description": "Initial", "snaptime": 1700000000},
        {"name": "current", "parent": "before_upgrade"},
    ]
    handler, endpoint = snapshot_environment(handler_class=ProxmoxLxcSnapshotInfoModule, snapshots=records)

    assert handler.get_info() == records
    handler.api.nodes.get.assert_called_once_with()
    handler.api.nodes.return_value.lxc.get.assert_called_once_with()
    endpoint.get.assert_called_once_with()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_info_fetches_selected_configuration(snapshot_environment, check_mode):
    configuration = {"description": "Saved snapshot", "hostname": "test-container", "memory": 512}
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxLxcSnapshotInfoModule,
        check_mode=check_mode,
        snapname="before_upgrade",
        snapshots=[{"name": "before_upgrade"}, {"name": "current"}],
    )
    endpoint.return_value.config.get.return_value = configuration

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    handler.module.exit_json.assert_called_once_with(
        changed=False,
        snapshots=[dict(configuration, name="before_upgrade")],
    )
    assert configuration == {"description": "Saved snapshot", "hostname": "test-container", "memory": 512}
    handler.api.nodes.get.assert_called_once_with()
    handler.api.nodes.return_value.lxc.get.assert_called_once_with()
    endpoint.get.assert_called_once_with()
    endpoint.assert_called_once_with("before_upgrade")
    endpoint.return_value.config.get.assert_called_once_with()


@pytest.mark.parametrize("configuration", [None, {}])
def test_snapshot_info_handles_optional_selected_configuration(snapshot_environment, configuration):
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxLxcSnapshotInfoModule,
        snapname="before_upgrade",
        snapshots=[{"name": "before_upgrade"}],
    )
    endpoint.return_value.config.get.return_value = configuration

    assert handler.get_info() == ([] if configuration is None else [{"name": "before_upgrade"}])


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_info_returns_empty_for_missing_snapshot(snapshot_environment, check_mode):
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxLxcSnapshotInfoModule,
        check_mode=check_mode,
        snapname="missing_snapshot",
        snapshots=[{"name": "current"}],
    )

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    handler.module.exit_json.assert_called_once_with(changed=False, snapshots=[])
    handler.module.fail_json.assert_not_called()
    handler.api.nodes.get.assert_called_once_with()
    handler.api.nodes.return_value.lxc.get.assert_called_once_with()
    endpoint.get.assert_called_once_with()
    endpoint.assert_not_called()
    endpoint.return_value.config.get.assert_not_called()


@pytest.mark.parametrize("snapname", [None, "before_upgrade"])
def test_snapshot_info_returns_empty_for_missing_container(snapshot_environment, snapname):
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxLxcSnapshotInfoModule,
        snapname=snapname,
    )
    handler.api.nodes.return_value.lxc.get.return_value = []

    assert handler.get_info() == []
    endpoint.get.assert_not_called()


@pytest.mark.parametrize(
    ("module_source", "class_name", "entrypoint"),
    [
        (proxmox_lxc_snapshot, "ProxmoxLxcSnapshotModule", "execute_resource_module"),
        (proxmox_lxc_snapshot_info, "ProxmoxLxcSnapshotInfoModule", "execute_info_module"),
    ],
)
def test_snapshot_main_registers_arguments_and_runtime_group(monkeypatch, module_source, class_name, entrypoint):
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(module_source, "AnsibleModule", constructor)
    monkeypatch.setattr(module_source, class_name, handler_constructor)

    module_source.main()

    arguments = constructor.call_args.kwargs
    spec = arguments["argument_spec"]
    assert spec["node"] == {"type": "str", "required": True}
    assert spec["vmid"] == {"type": "int", "required": True}
    assert arguments["supports_check_mode"] is True
    if module_source is proxmox_lxc_snapshot:
        assert spec["snapname"] == {"type": "str", "required": True}
        assert spec["description"] == {"type": "str"}
        assert spec["start"] == {"type": "bool", "default": False}
        assert spec["state"] == {"type": "str", "choices": ["present", "absent", "rollback"], "default": "present"}
    else:
        assert spec["snapname"] == {"type": "str"}
    handler_constructor.assert_called_once_with(module)
    getattr(handler, entrypoint).assert_called_once_with()

    runtime_path = Path(__file__).resolve().parents[4] / "meta" / "runtime.yml"
    metadata = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    assert "proxmox_lxc_snapshot" in metadata["action_groups"]["proxmox"]
    assert "proxmox_lxc_snapshot_info" in metadata["action_groups"]["proxmox"]
