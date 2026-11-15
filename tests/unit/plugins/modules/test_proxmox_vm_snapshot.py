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
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_vm_snapshot, proxmox_vm_snapshot_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm_snapshot import ProxmoxVmSnapshotModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm_snapshot_info import ProxmoxVmSnapshotInfoModule


@pytest.fixture
def snapshot_environment():
    """Provide isolated snapshot handlers with real shared lifecycle helpers.

    Returns:
        callable: Factory returning a handler and its snapshot API endpoint.
    """
    def create(handler_class=ProxmoxVmSnapshotModule, check_mode=False, snapshots=None, **parameters):
        """Create one handler with mocked API reads and writes.

        Args:
            handler_class (type): Action or information handler to construct.
            check_mode (bool): Whether mutations should be predicted only.
            snapshots (list, optional): API snapshot records to return.
            **parameters: Module argument overrides.

        Returns:
            tuple: Handler and snapshot API endpoint.
        """
        params = {
            "node": "pve01",
            "vmid": 100,
            "snapname": None if handler_class is ProxmoxVmSnapshotInfoModule else "before_upgrade",
            "state": "present",
            "description": None,
            "vmstate": False,
            "task_timeout": None,
        }
        params.update(parameters)
        module = SimpleNamespace(
            params=params, check_mode=check_mode,
            fail_json=Mock(side_effect=RuntimeError("module failed")),
            exit_json=Mock(side_effect=SystemExit),
        )
        client = ProxmoxClient(module)
        client._api = Mock()
        client.validate_pve_permissions = Mock()
        client.api.nodes.get.return_value = [{"node": "pve01"}]
        client.api.get.return_value = [{"vmid": params["vmid"]}]
        endpoint = client.api.nodes.return_value.qemu.return_value.snapshot
        default_snapshots = (
            [{"name": params["snapname"]}]
            if handler_class is ProxmoxVmSnapshotInfoModule and params["snapname"] is not None
            else [{"name": "current"}]
        )
        endpoint.get.return_value = snapshots if snapshots is not None else default_snapshots
        endpoint.post.return_value = "UPID:pve01:snapshot"
        endpoint.return_value.delete.return_value = "UPID:pve01:snapshot-delete"
        endpoint.return_value.rollback.post.return_value = "UPID:pve01:snapshot-rollback"
        endpoint.return_value.config.put.return_value = None
        return handler_class(module, client=client), endpoint

    return create


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_snapshot_present_is_idempotent_and_check_mode_safe(snapshot_environment, monkeypatch, check_mode, exists):
    """Create only missing snapshots and never write in check mode.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        check_mode (bool): Whether the action is only predicted.
        exists (bool): Whether the snapshot already exists.
    """
    snapshots = [{"name": "current"}]
    if exists:
        snapshots.append({"name": "before_upgrade", "description": "Original description", "vmstate": 0})
    handler, endpoint = snapshot_environment(
        check_mode=check_mode, snapshots=snapshots,
        description="Original description" if exists else "Requested description", vmstate=True,
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(
        changed=not exists, snapshot=snapshots[-1] if exists else {"name": "before_upgrade"},
    )
    endpoint.get.assert_called_once_with()
    handler.client.validate_pve_permissions.assert_called_once_with(handler.api, "/vms")
    if not exists and not check_mode:
        endpoint.post.assert_called_once_with(
            snapname="before_upgrade", description="Requested description", vmstate=1,
        )
        wait.assert_called_once_with("UPID:pve01:snapshot")
        assert handler.client._changed is True
    else:
        endpoint.post.assert_not_called()
        wait.assert_not_called()
        assert handler.client._changed is False
    endpoint.return_value.config.put.assert_not_called()
    endpoint.return_value.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_updates_descriptions(snapshot_environment, monkeypatch, check_mode):
    """Apply or predict description changes without recreating the snapshot.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        check_mode (bool): Whether the update is only predicted.
    """
    current = {"name": "before_upgrade", "description": "Original", "vmstate": 1, "snaptime": 1700000000}
    handler, endpoint = snapshot_environment(
        check_mode=check_mode, snapshots=[current], description="Updated",
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(
        changed=True, snapshot=dict(current, description="Updated"),
    )
    assert current["description"] == "Original"
    if check_mode:
        endpoint.return_value.config.put.assert_not_called()
        assert handler.client._changed is False
    else:
        endpoint.assert_called_once_with("before_upgrade")
        endpoint.return_value.config.put.assert_called_once_with(description="Updated")
        assert handler.client._changed is True
    endpoint.post.assert_not_called()
    endpoint.return_value.delete.assert_not_called()
    wait.assert_not_called()


@pytest.mark.parametrize(
    ("resource", "description", "expected"),
    [
        ({"description": "Original"}, None, {}),
        ({"description": "Original"}, "Original", {}),
        ({}, "", {}),
        ({"description": ""}, "", {}),
        ({"description": "Original"}, "", {"description": ""}),
        ({"description": "Original"}, "Updated", {"description": "Updated"}),
    ],
)
def test_snapshot_compares_only_requested_descriptions(snapshot_environment, resource, description, expected):
    """Preserve omitted descriptions and treat an empty string as a clear request.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        resource (dict): Existing snapshot fields.
        description (str, optional): Desired description.
        expected (dict): Required update parameters.
    """
    handler, endpoint = snapshot_environment(description=description, vmstate=True)

    assert handler.get_update_parameters(resource) == expected

    endpoint.post.assert_not_called()
    endpoint.return_value.config.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_snapshot_absent_is_idempotent_and_check_mode_safe(snapshot_environment, monkeypatch, check_mode, exists):
    """Delete only existing snapshots and predict deletion without writing.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        check_mode (bool): Whether deletion is only predicted.
        exists (bool): Whether the snapshot exists.
    """
    snapshots = [{"name": "before_upgrade"}] if exists else [{"name": "current"}]
    handler, endpoint = snapshot_environment(state="absent", check_mode=check_mode, snapshots=snapshots)
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        handler.execute_resource_module()

    handler.module.exit_json.assert_called_once_with(changed=exists, snapshot=None)
    endpoint.get.assert_called_once_with()
    if exists and not check_mode:
        endpoint.assert_called_once_with("before_upgrade")
        endpoint.return_value.delete.assert_called_once_with()
        wait.assert_called_once_with("UPID:pve01:snapshot-delete")
        assert handler.client._changed is True
    else:
        endpoint.return_value.delete.assert_not_called()
        wait.assert_not_called()
        assert handler.client._changed is False
    endpoint.post.assert_not_called()
    endpoint.return_value.config.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_rollback_is_explicit_and_check_mode_safe(snapshot_environment, monkeypatch, check_mode):
    """Always roll back existing snapshots, without mutating anything in check mode.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        check_mode (bool): Whether rollback should only be predicted.
    """
    snapshot = {"name": "before_upgrade", "description": "Original"}
    handler, endpoint = snapshot_environment(
        state="rollback", check_mode=check_mode,
        snapshots=[snapshot, {"name": "current", "parent": "before_upgrade"}],
        description="This must not update the snapshot",
    )
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    for _attempt in range(2):
        with pytest.raises(SystemExit):
            handler.execute_resource_module()

    assert handler.module.exit_json.call_count == 2
    handler.module.exit_json.assert_called_with(changed=True, snapshot=snapshot)
    assert endpoint.get.call_count == 2
    if check_mode:
        endpoint.return_value.rollback.post.assert_not_called()
        wait.assert_not_called()
        assert handler.client._changed is False
    else:
        assert endpoint.return_value.rollback.post.call_count == 2
        endpoint.return_value.rollback.post.assert_called_with()
        assert wait.call_count == 2
        wait.assert_called_with("UPID:pve01:snapshot-rollback")
        assert handler.client._changed is True
    endpoint.post.assert_not_called()
    endpoint.return_value.config.put.assert_not_called()
    endpoint.return_value.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_rollback_rejects_missing_snapshots(snapshot_environment, monkeypatch, check_mode):
    """Reject missing rollback targets in normal mode and check mode.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        check_mode (bool): Whether rollback should only be predicted.
    """
    handler, endpoint = snapshot_environment(state="rollback", check_mode=check_mode)
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    handler.module.fail_json.assert_called_once_with(msg="Snapshot before_upgrade does not exist on VM 100.")
    endpoint.return_value.rollback.post.assert_not_called()
    endpoint.post.assert_not_called()
    endpoint.return_value.config.put.assert_not_called()
    endpoint.return_value.delete.assert_not_called()
    wait.assert_not_called()
    assert handler.client._changed is False


@pytest.mark.parametrize("handler_class", [ProxmoxVmSnapshotModule, ProxmoxVmSnapshotInfoModule])
@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_snapshot_modules_reject_invalid_vmids(snapshot_environment, handler_class, vmid):
    """Reject VMIDs outside the supported range before accessing the API.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        handler_class (type): Handler whose arguments are being validated.
        vmid (int): Invalid VM identifier.
    """
    handler, endpoint = snapshot_environment(handler_class=handler_class, vmid=vmid)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute()

    handler.module.fail_json.assert_called_once_with(msg="VMID must be between 100 and 999999999.")
    handler.client.validate_pve_permissions.assert_not_called()
    endpoint.get.assert_not_called()


@pytest.mark.parametrize("snapname", ["", "a", "1a", "_aa", "has space", "aa\n", "éa", "a" * 41])
def test_snapshot_rejects_invalid_names(snapshot_environment, snapname):
    """Enforce the length and ASCII snapshot-name character rules.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        snapname (str): Invalid snapshot name.
    """
    handler, endpoint = snapshot_environment(snapname=snapname)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.get_resource()

    assert "snapname must be 2-40 ASCII characters" in handler.module.fail_json.call_args.kwargs["msg"]
    endpoint.get.assert_not_called()


@pytest.mark.parametrize("snapname", ["current", "pending", "PENDING"])
def test_snapshot_rejects_reserved_names(snapshot_environment, snapname):
    """Reject reserved names before the current pseudo-snapshot can match.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        snapname (str): Reserved Proxmox snapshot name.
    """
    handler, endpoint = snapshot_environment(snapname=snapname)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.get_resource()

    handler.module.fail_json.assert_called_once_with(msg="snapname must not be 'current' or 'pending'.")
    endpoint.get.assert_not_called()


@pytest.mark.parametrize(
    "snapname",
    ["ab", "A1", "Aa_2", "Current", "before-upgrade", "snap_2026-11-10", "a" * 40],
)
def test_snapshot_accepts_valid_names(snapshot_environment, snapname):
    """Accept names satisfying the API character and length requirements.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        snapname (str): Valid snapshot name.
    """
    handler, endpoint = snapshot_environment(snapname=snapname)

    assert handler.get_resource() is None

    endpoint.get.assert_called_once_with()


@pytest.mark.parametrize(("vmstate", "description"), [(False, None), (True, ""), (True, "Before upgrade")])
def test_snapshot_forwards_creation_options(snapshot_environment, monkeypatch, vmstate, description):
    """Preserve descriptions and encode RAM settings as Proxmox booleans.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        vmstate (bool): Requested RAM inclusion.
        description (str, optional): Snapshot description to forward.
    """
    handler, endpoint = snapshot_environment(vmstate=vmstate, description=description)
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    handler.get_resource()
    assert handler.create_resource() == {"name": "before_upgrade"}

    options = {"snapname": "before_upgrade", "vmstate": int(vmstate)}
    if description is not None:
        options["description"] = description
    endpoint.post.assert_called_once_with(**options)
    vmstate_argument = endpoint.post.call_args.kwargs["vmstate"]
    assert isinstance(vmstate_argument, int)
    assert not isinstance(vmstate_argument, bool)


@pytest.mark.parametrize("handler_class", [ProxmoxVmSnapshotModule, ProxmoxVmSnapshotInfoModule])
def test_snapshot_modules_preserve_read_errors(snapshot_environment, handler_class):
    """Use the shared API error handling for failed snapshot listing requests.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        handler_class (type): Handler whose listing request should fail.
    """
    handler, endpoint = snapshot_environment(handler_class=handler_class)
    endpoint.get.side_effect = RuntimeError("snapshot read failed")

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute()

    assert handler.module.fail_json.call_args.kwargs["api_error"] == "snapshot read failed"
    endpoint.post.assert_not_called()


def test_snapshot_preserves_creation_errors(snapshot_environment, monkeypatch):
    """Reject failed writes without marking the VM as changed.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    handler, endpoint = snapshot_environment()
    endpoint.post.side_effect = RuntimeError("snapshot rejected")
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    assert handler.module.fail_json.call_args.kwargs["api_error"] == "snapshot rejected"
    assert handler.client._changed is False
    wait.assert_not_called()


@pytest.mark.parametrize("state", ["present", "absent", "rollback"])
@pytest.mark.parametrize("task_upid", [None, "not-a-task"])
def test_snapshot_rejects_invalid_task_responses(snapshot_environment, monkeypatch, task_upid, state):
    """Do not attempt task polling without an asynchronous task identifier.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        task_upid (str, optional): Invalid task response.
        state (str): Requested creation, deletion, or rollback state.
    """
    snapshots = [] if state == "present" else [{"name": "before_upgrade"}]
    handler, endpoint = snapshot_environment(state=state, snapshots=snapshots)
    mutation = {
        "present": endpoint.post,
        "absent": endpoint.return_value.delete,
        "rollback": endpoint.return_value.rollback.post,
    }[state]
    mutation.return_value = task_upid
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    handler.module.fail_json.assert_called_once_with(msg="Snapshot operation did not return a task UPID.", changed=True)
    wait.assert_not_called()


def test_snapshot_task_read_errors_preserve_changed_state(snapshot_environment):
    """Keep mutation tracking when task polling fails after a successful POST.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
    """
    handler, endpoint = snapshot_environment()
    handler.api.nodes.return_value.tasks.return_value.status.get.side_effect = RuntimeError("task read failed")

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    endpoint.post.assert_called_once()
    failure = handler.module.fail_json.call_args.kwargs
    assert failure["changed"] is True
    assert failure["api_error"] == "task read failed"


@pytest.mark.parametrize("state", ["present", "absent", "rollback"])
def test_snapshot_preserves_existing_mutation_errors(snapshot_environment, monkeypatch, state):
    """Use shared error handling without marking rejected mutations as changed.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        monkeypatch: Pytest fixture for replacing task waiting.
        state (str): Requested update, deletion, or rollback state.
    """
    handler, endpoint = snapshot_environment(
        state=state, snapshots=[{"name": "before_upgrade", "description": "Original"}], description="Updated",
    )
    mutation = {
        "present": endpoint.return_value.config.put,
        "absent": endpoint.return_value.delete,
        "rollback": endpoint.return_value.rollback.post,
    }[state]
    mutation.side_effect = RuntimeError("snapshot mutation rejected")
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(RuntimeError, match="module failed"):
        handler.execute_resource_module()

    assert handler.module.fail_json.call_args.kwargs["api_error"] == "snapshot mutation rejected"
    assert handler.client._changed is False
    wait.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_info_returns_all_records(snapshot_environment, check_mode):
    """Return all metadata, including the current entry and boolean RAM flags.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        check_mode (bool): Whether the read runs in check mode.
    """
    records = [
        {"name": "first_snapshot", "description": "Initial", "snaptime": 1700000000, "vmstate": "1"},
        {"name": "second_snapshot", "parent": "first_snapshot", "snaptime": 1700000100, "vmstate": 0},
        {"name": "current", "parent": "second_snapshot", "description": "You are here!"},
    ]
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxVmSnapshotInfoModule, check_mode=check_mode, snapshots=records,
    )

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    result = handler.module.exit_json.call_args.kwargs
    assert result["changed"] is False
    assert len(result["snapshots"]) == 3
    assert result["snapshots"][0]["vmstate"] is True
    assert result["snapshots"][1]["vmstate"] is False
    assert result["snapshots"][1]["parent"] == "first_snapshot"
    assert result["snapshots"][0]["description"] == "Initial"
    assert result["snapshots"][0]["snaptime"] == 1700000000
    assert result["snapshots"][2] == records[2]
    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    endpoint.get.assert_called_once_with()
    endpoint.assert_not_called()
    endpoint.return_value.config.get.assert_not_called()
    endpoint.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    ("vmstate", "expected_vmstate"),
    [(0, False), ("0", False), (1, True), ("1", True), ("local-lvm:vm-100-state-before_upgrade", True)],
)
def test_snapshot_info_validates_then_fetches_requested_configuration(snapshot_environment, check_mode, vmstate, expected_vmstate):
    """Validate VM and snapshot existence before fetching one saved configuration.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        check_mode (bool): Whether the information request runs in check mode.
        vmstate: RAM flag or saved-memory volume identifier returned by Proxmox.
        expected_vmstate (bool): Normalized RAM inclusion flag.
    """
    configuration = {"description": "Saved snapshot", "snaptime": 1700000000, "parent": "older_snapshot",
                     "memory": 512, "vmstate": vmstate}
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxVmSnapshotInfoModule, check_mode=check_mode, snapname="before_upgrade",
    )
    endpoint.return_value.config.get.return_value = configuration

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    result = handler.module.exit_json.call_args.kwargs
    assert result["changed"] is False
    assert result["snapshots"] == [dict(configuration, name="before_upgrade", vmstate=expected_vmstate)]
    assert result["snapshots"][0]["vmstate"] is expected_vmstate
    assert configuration["vmstate"] == vmstate
    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    endpoint.assert_called_once_with("before_upgrade")
    endpoint.return_value.config.get.assert_called_once_with()
    endpoint.get.assert_called_once_with()
    endpoint.post.assert_not_called()
    endpoint.return_value.config.put.assert_not_called()


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [(None, []), ({}, [{"name": "before_upgrade"}]), ({"memory": 512}, [{"name": "before_upgrade", "memory": 512}])],
)
def test_snapshot_info_preserves_optional_config_fields(snapshot_environment, configuration, expected):
    """Keep optional fields optional without falling back to the snapshot list.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        configuration (dict, optional): Saved configuration response.
        expected (list): Selected snapshot result.
    """
    handler, endpoint = snapshot_environment(handler_class=ProxmoxVmSnapshotInfoModule, snapname="before_upgrade")
    endpoint.return_value.config.get.return_value = configuration

    assert handler.get_info() == expected

    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    endpoint.get.assert_called_once_with()
    endpoint.return_value.config.get.assert_called_once_with()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("snapname", [None, "before_upgrade"])
def test_snapshot_info_returns_empty_for_missing_vm(snapshot_environment, check_mode, snapname):
    """Return an empty list when the VM is absent from the selected node.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        check_mode (bool): Whether the information request runs in check mode.
        snapname (str, optional): Snapshot selector, or None to list all snapshots.
    """
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxVmSnapshotInfoModule, check_mode=check_mode, vmid=1001, snapname=snapname,
    )
    handler.api.get.return_value = []

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    handler.module.exit_json.assert_called_once_with(changed=False, snapshots=[])
    handler.module.fail_json.assert_not_called()
    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    endpoint.get.assert_not_called()
    endpoint.return_value.config.get.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_snapshot_info_returns_empty_for_missing_snapshot(snapshot_environment, check_mode):
    """Return an empty list when the snapshot is absent from the VM index."""
    handler, endpoint = snapshot_environment(
        handler_class=ProxmoxVmSnapshotInfoModule, check_mode=check_mode, snapname="missing_snapshot",
    )
    endpoint.get.return_value = [{"name": "current"}]

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    handler.module.exit_json.assert_called_once_with(changed=False, snapshots=[])
    handler.module.fail_json.assert_not_called()
    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    endpoint.get.assert_called_once_with()
    endpoint.assert_not_called()
    endpoint.return_value.config.get.assert_not_called()


@pytest.mark.parametrize("snapname", [None, "missing_snapshot"])
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (404, "snapshot not found"),
        (500, "snapshot not found"),
        (403, "snapshot 'missing_snapshot' does not exist"),
        (500, "snapshot 'another_snapshot' does not exist"),
        (500, "Configuration file 'nodes/pve01/qemu-server/1001.conf' does not exist"),
        (500, "Configuration file 'nodes/pve02/qemu-server/100.conf' does not exist"),
        (403, "Configuration file 'nodes/pve01/qemu-server/100.conf' does not exist"),
        (500, "snapshot 'None' does not exist"),
    ],
)
def test_snapshot_info_preserves_unrelated_errors(snapshot_environment, snapname, status, message):
    """Preserve unrelated failures in both lookup modes without fallback reads.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        snapname (str, optional): Snapshot selector, or None to list all snapshots.
        status (int): HTTP error status returned by the selected endpoint.
        message (str): Error text that must not be treated as the requested snapshot being absent.
    """
    handler, endpoint = snapshot_environment(handler_class=ProxmoxVmSnapshotInfoModule, snapname=snapname)
    error = RuntimeError(message)
    error.status = status
    getter = endpoint.return_value.config.get if snapname is not None else endpoint.get
    getter.side_effect = error

    with pytest.raises(RuntimeError, match="module failed"):
        handler.get_info()

    failure = handler.module.fail_json.call_args.kwargs
    assert failure["api_error"] == message
    assert failure["status_code"] == status
    handler.api.nodes.get.assert_called_once_with()
    handler.api.get.assert_called_once_with("nodes/pve01/qemu")
    if snapname is not None:
        endpoint.get.assert_called_once_with()
        endpoint.assert_called_once_with(snapname)
        getter.assert_called_once_with()
    else:
        endpoint.get.assert_called_once_with()
        endpoint.return_value.config.get.assert_not_called()


@pytest.mark.parametrize("snapshots", [None, []])
def test_snapshot_info_returns_an_empty_list(snapshot_environment, snapshots):
    """Represent an empty API response as a list rather than null.

    Args:
        snapshot_environment (callable): Snapshot handler factory.
        snapshots (list, optional): Empty endpoint response.
    """
    handler, endpoint = snapshot_environment(handler_class=ProxmoxVmSnapshotInfoModule)
    endpoint.get.return_value = snapshots

    assert handler.get_info() == []


@pytest.mark.parametrize(
    ("module_source", "class_name", "entrypoint"),
    [
        (proxmox_vm_snapshot, "ProxmoxVmSnapshotModule", "execute_resource_module"),
        (proxmox_vm_snapshot_info, "ProxmoxVmSnapshotInfoModule", "execute_info_module"),
    ],
)
def test_snapshot_main_registers_required_arguments(monkeypatch, module_source, class_name, entrypoint):
    """Wire required arguments and shared authentication to the correct lifecycle.

    Args:
        monkeypatch: Pytest fixture for replacing module construction.
        module_source: Snapshot module being exercised.
        class_name (str): Handler class to replace.
        entrypoint (str): Expected shared lifecycle entrypoint.
    """
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
    assert arguments["required_together"] == [["api_token_id", "api_token_secret"]]
    assert arguments["mutually_exclusive"] == [["api_password", "api_token_id"]]
    if module_source is proxmox_vm_snapshot:
        assert spec["snapname"] == {"type": "str", "required": True}
        assert spec["vmstate"] == {"type": "bool", "default": False}
        assert spec["description"] == {"type": "str"}
        assert spec["task_timeout"] == {"type": "int"}
        assert spec["state"] == {"type": "str", "choices": ["present", "absent", "rollback"], "default": "present"}
    else:
        assert spec["snapname"] == {"type": "str"}
    handler_constructor.assert_called_once_with(module)
    getattr(handler, entrypoint).assert_called_once_with()
