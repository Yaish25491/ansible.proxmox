# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import (
    ProxmoxPower,
    ProxmoxPowerstateActionModule,
    ProxmoxPowerstateInfoModule,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


def make_power(**params):
    """Create a power handler backed by a mocked API.

    Args:
        **params: Overrides for the default module parameters.

    Returns:
        A pair containing the power handler and its mocked API.
    """
    module = SimpleNamespace(
        params=dict({"node": "pve01", "vmid": 100, "task_timeout": None}, **params),
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    api = Mock()
    client = ProxmoxClient(module)
    client._api = api
    return ProxmoxPower(module, client=client), api


def test_lxc_power_waits_and_verifies_the_lxc_endpoint():
    """Use shared task waiting and state verification for LXC guests."""
    existing, api = make_power()
    power = ProxmoxPower(existing.module, client=existing.client, guest_type="lxc")
    container = api.nodes.return_value.lxc.return_value
    container.status.start.post.return_value = "UPID:pve01:start"
    container.status.current.get.return_value = {"status": "running"}
    api.nodes.return_value.tasks.return_value.status.get.return_value = {"status": "stopped", "exitstatus": "OK"}

    assert power.set_state("start") == ("UPID:pve01:start", "running")
    container.status.start.post.assert_called_once_with()
    api.nodes.return_value.tasks.assert_called_once_with("UPID:pve01:start")
    api.nodes.return_value.qemu.assert_not_called()


def test_lxc_power_waits_and_verifies_the_lxc_endpoint():
    """Use shared task waiting and state verification for LXC guests."""
    existing, api = make_power()
    power = ProxmoxPower(existing.module, client=existing.client, guest_type="lxc")
    container = api.nodes.return_value.lxc.return_value
    container.status.start.post.return_value = "UPID:pve01:start"
    container.status.current.get.return_value = {"status": "running"}
    api.nodes.return_value.tasks.return_value.status.get.return_value = {"status": "stopped", "exitstatus": "OK"}

    assert power.set_state("start") == ("UPID:pve01:start", "running")
    container.status.start.post.assert_called_once_with()
    api.nodes.return_value.tasks.assert_called_once_with("UPID:pve01:start")
    api.nodes.return_value.qemu.assert_not_called()


def test_get_status_uses_current_qemu_endpoint():
    """Verify status retrieval uses the selected VM's current QEMU endpoint."""
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    qemu.status.current.get.return_value = {"status": "running"}

    assert power.get_status() == {"status": "running"}
    api.nodes.assert_called_with("pve01")
    api.nodes.return_value.qemu.assert_called_once_with(100)
    qemu.status.current.get.assert_called_once_with()


@pytest.mark.parametrize("state,policy,valid", [
    ("present", "on", True), ("present", "hotplug", True),
    ("absent", "off", True), ("absent", "fail", True),
    ("absent", "on", False), ("absent", "hotplug", False),
])
def test_validate_policy(state, policy, valid):
    """Verify lifecycle policies reject unsupported deletion combinations.

    Args:
        state: Desired VM lifecycle state.
        policy: Power-cycle policy to validate.
        valid: Whether the combination should be accepted.
    """
    power, api = make_power()

    if valid:
        power.validate_policy(state, policy)
    else:
        with pytest.raises(RuntimeError, match="module failed"):
            power.validate_policy(state, policy)
        assert "power_cycle must be off or fail" in power.module.fail_json.call_args.kwargs["msg"]
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy,expected", [
    ("hotplug", {}), ("on", {"current": 1}),
    ("off", {"current": 1}), ("fail", {"current": 1}),
])
def test_configuration_options(policy, expected):
    """Verify configuration options match the selected power policy.

    Args:
        policy: Power-cycle policy under test.
        expected: Expected configuration retrieval options.
    """
    assert ProxmoxPower.configuration_options(policy) == expected


def test_require_known_rejects_missing_status():
    """Verify operations fail closed when the VM status is unknown."""
    power, api = make_power()

    with pytest.raises(RuntimeError, match="module failed"):
        power.require_known(None, "change VM power state")

    assert "Unable to verify VM power state" in power.module.fail_json.call_args.kwargs["msg"]
    power.require_known("stopped", "change VM power state")
    api.nodes.assert_not_called()


@pytest.mark.parametrize("operation", ["delete", "update"])
@pytest.mark.parametrize("status", ["running", None])
def test_require_stopped_rejects_powered_on_or_unknown_vm(status, operation):
    """Verify offline operations reject powered-on or unknown VM states.

    Args:
        status: Observed VM power status.
        operation: Offline operation requiring a stopped VM.
    """
    power, api = make_power()

    power.require_stopped("stopped", operation)
    with pytest.raises(RuntimeError, match="module failed"):
        power.require_stopped(status, operation)

    assert ("Unable to verify VM power state" if status is None else "power it off") in (
        power.module.fail_json.call_args.kwargs["msg"]
    )
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy", ["on", "off", "hotplug", "fail"])
def test_validate_update_respects_policy_and_fails_closed(policy):
    """Verify update validation respects the policy and rejects unknown status.

    Args:
        policy: Power-cycle policy used for configuration changes.
    """
    power, api = make_power()

    power.validate_update("stopped", policy)
    if policy == "fail":
        with pytest.raises(RuntimeError, match="module failed"):
            power.validate_update("running", policy)
    else:
        power.validate_update("running", policy)
    with pytest.raises(RuntimeError, match="module failed"):
        power.validate_update(None, policy)
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy,status,allowed", [
    ("off", "running", True), ("off", "stopped", True),
    ("fail", "stopped", True), ("fail", "running", False),
    ("off", None, False), ("fail", None, False),
])
def test_validate_delete_respects_policy(policy, status, allowed):
    """Verify deletion validation accepts only safe policy and status pairs.

    Args:
        policy: Power-cycle policy used for deletion.
        status: Observed VM power status.
        allowed: Whether deletion should pass validation.
    """
    power, api = make_power()

    if allowed:
        power.validate_delete(status, policy)
    else:
        with pytest.raises(RuntimeError, match="module failed"):
            power.validate_delete(status, policy)
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy,status,expected", [
    ("on", "stopped", True), ("on", "running", False),
    ("off", "running", True), ("off", "stopped", False),
    ("fail", "running", False), ("hotplug", "running", False),
])
def test_needs_change(policy, status, expected):
    """Verify on/off policies detect required power transitions.

    Args:
        policy: Requested power-cycle policy.
        status: Observed VM power status.
        expected: Whether a transition should be required.
    """
    power, api = make_power()

    assert power.needs_change(status, policy) is expected
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy", ["on", "off"])
def test_needs_change_rejects_unknown_status(policy):
    """Verify transition decisions reject unknown VM status.

    Args:
        policy: Requested on/off power-cycle policy.
    """
    power, api = make_power()

    with pytest.raises(RuntimeError, match="module failed"):
        power.needs_change(None, policy)
    api.nodes.assert_not_called()


@pytest.mark.parametrize("status,policy,has_updates,stop,start", [
    ("running", "on", True, True, False),
    ("running", "on", False, False, False),
    ("stopped", "on", True, False, True),
    ("running", "off", False, True, False),
    ("stopped", "off", True, False, False),
    ("running", "hotplug", True, False, False),
    ("running", "fail", True, False, False),
    ("paused", "on", False, True, True),
])
def test_power_transition_decisions(status, policy, has_updates, stop, start):
    """Verify stop/start decisions for configuration updates.

    Args:
        status: Observed VM power status.
        policy: Requested power-cycle policy.
        has_updates: Whether configuration updates are pending.
        stop: Expected decision to stop the VM.
        start: Expected decision to start the VM.
    """
    assert ProxmoxPower.should_stop(status, policy, has_updates) is stop
    assert ProxmoxPower.should_start(status, policy) is start


@pytest.mark.parametrize("action,expected", [("start", "running"), ("stop", "stopped")])
@pytest.mark.parametrize("timeout", [None, 42])
def test_set_state_waits_for_upid_and_verifies_status(action, expected, timeout, monkeypatch):
    """Verify start/stop delegate timeout handling and confirm the final status.

    Args:
        action: Power action to execute.
        expected: Expected final power state.
        timeout: Optional timeout for waiting on the task.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    power, api = make_power(task_timeout=timeout)
    qemu = api.nodes.return_value.qemu.return_value
    getattr(qemu.status, action).post.return_value = "UPID:pve01:{0}".format(action)
    qemu.status.current.get.return_value = {"status": expected}
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    assert power.set_state(action) == ("UPID:pve01:{0}".format(action), expected)

    wait.assert_called_once_with("UPID:pve01:{0}".format(action), include_log_on_failure=True)
    qemu.status.current.get.assert_called_once_with()


@pytest.mark.parametrize("action,endpoint,wrong_status", [
    ("start", "start", {"status": "stopped"}),
    ("stop", "stop", {"status": "running"}),
    ("shutdown", "shutdown", {"status": "running"}),
    ("reset", "reset", {"status": "stopped"}),
    ("reboot", "reboot", {"status": "stopped"}),
    ("resume", "resume", {"status": "stopped"}),
    ("pause", "suspend", {"status": "running", "qmpstatus": "running"}),
    ("hibernate", "suspend", {"status": "stopped", "qmpstatus": "stopped"}),
])
def test_set_state_requires_successful_upid_and_expected_status(action, endpoint, wrong_status, monkeypatch):
    """Verify every power action rejects missing UPIDs and incorrect final states.

    Args:
        action: Power action to execute.
        endpoint: Corresponding QEMU API endpoint.
        wrong_status: Valid status mapping that does not match the action target.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)
    post = getattr(qemu.status, endpoint).post

    post.return_value = None
    with pytest.raises(RuntimeError, match="module failed"):
        power.set_state(action)
    wait.assert_not_called()

    post.return_value = "UPID:pve01:{0}".format(action)
    qemu.status.current.get.return_value = wrong_status
    with pytest.raises(RuntimeError, match="module failed"):
        power.set_state(action)
    wait.assert_called_once_with(post.return_value, include_log_on_failure=True)
    assert "did not reach" in power.module.fail_json.call_args.kwargs["msg"]


def test_set_state_does_not_check_status_when_task_fails(monkeypatch):
    """Verify failed power tasks prevent final-status retrieval.

    Args:
        monkeypatch: Pytest fixture for making task waiting fail.
    """
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    qemu.status.stop.post.return_value = "UPID:pve01:stop"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock(side_effect=RuntimeError("task failed")))

    with pytest.raises(RuntimeError, match="task failed"):
        power.set_state("stop")

    qemu.status.current.get.assert_not_called()


def test_set_state_rejects_invalid_action_before_api_call():
    """Verify unsupported power actions fail before reaching the VM API."""
    power, api = make_power()

    with pytest.raises(ValueError, match="Unsupported VM power operation"):
        power.set_state("invalid")

    api.nodes.assert_not_called()


@pytest.mark.parametrize("status,expected", [
    ({"status": "running", "qmpstatus": "running"}, "running"),
    ({"status": "running", "qmpstatus": "paused"}, "paused"),
    ({"status": "stopped", "qmpstatus": "stopped"}, "stopped"),
    ({"status": "stopped", "qmpstatus": "stopped", "lock": "suspended"}, "suspended"),
])
def test_get_powerstate(status, expected):
    """Verify current API status maps to the correct normalized power state.

    Args:
        status: Current VM status mapping returned by the API.
        expected: Expected normalized power state.
    """
    power, api = make_power()
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = status

    assert power.get_powerstate() == expected


@pytest.mark.parametrize("status", [None, {}, {"status": "unknown"}, {"status": "running", "qmpstatus": "io-error"}])
def test_get_powerstate_rejects_unknown_status(status):
    """Verify unrecognized status responses fail rather than guessing a state.

    Args:
        status: Missing or unsupported VM status response.
    """
    power, api = make_power()
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = status

    with pytest.raises(RuntimeError, match="module failed"):
        power.get_powerstate()


def test_resolve_node_is_cached_and_used_for_status():
    """Verify node discovery selects QEMU VMs and caches the matching node."""
    power, api = make_power(node=None)
    api.cluster.resources.get.return_value = [
        {"vmid": 100, "type": "lxc", "node": "other"},
        {"vmid": "100", "type": "qemu", "node": "pve02"},
    ]
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = {"status": "running"}

    assert power.get_powerstate() == "running"
    assert power.resolve_node() == "pve02"
    api.cluster.resources.get.assert_called_once_with(type="vm")
    api.nodes.assert_called_with("pve02")


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_resolve_node_validates_vmid(vmid):
    """Verify invalid VM identifiers are rejected before discovery.

    Args:
        vmid: VM identifier outside the supported range.
    """
    power, api = make_power(node=None, vmid=vmid)
    with pytest.raises(RuntimeError, match="module failed"):
        power.resolve_node()
    api.cluster.resources.get.assert_not_called()


def test_resolve_node_rejects_missing_qemu_vm():
    """Verify missing or invisible QEMU VMs fail node discovery."""
    power, api = make_power(node=None)
    api.cluster.resources.get.return_value = []
    with pytest.raises(RuntimeError, match="module failed"):
        power.resolve_node()
    api.nodes.assert_not_called()


@pytest.mark.parametrize("action", list(ProxmoxPower.POWER_STATES))
@pytest.mark.parametrize("current", ["running", "stopped", "paused", "suspended"])
def test_is_powerstate_action_needed(action, current):
    """Verify power actions are only skipped when their target is satisfied.

    Args:
        action: Requested power action.
        current: Normalized current power state.
    """
    power, api = make_power(powerstate=action)
    expected = ProxmoxPower.POWER_STATES[action]
    changed = action in ("reset", "reboot") or current != expected

    assert power.is_powerstate_action_needed(action, current) is changed
    api.nodes.assert_not_called()


def test_perform_powerstate_action_resumes_paused_vm_for_start():
    """Verify start dispatches to resume when the VM is paused."""
    power, api = make_power()
    power.set_state = Mock(return_value=("UPID:pve01:resume", "running"))

    assert power.perform_powerstate_action("start", "paused") == ("UPID:pve01:resume", "running")
    power.set_state.assert_called_once_with("resume")
    api.nodes.assert_not_called()


@pytest.mark.parametrize("action,endpoint,options,status", [
    ("shutdown", "shutdown", {}, {"status": "stopped"}),
    ("reset", "reset", {}, {"status": "running"}),
    ("reboot", "reboot", {}, {"status": "running"}),
    ("resume", "resume", {}, {"status": "running"}),
    ("pause", "suspend", {}, {"status": "running", "qmpstatus": "paused"}),
    ("hibernate", "suspend", {"todisk": 1}, {"status": "stopped", "lock": "suspended"}),
])
def test_set_state_power_actions(action, endpoint, options, status, monkeypatch):
    """Verify power endpoints, payloads, task handling, and final states.

    Args:
        action: Requested power action.
        endpoint: Expected QEMU status endpoint name.
        options: Expected API request parameters.
        status: Status mapping returned after the action.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    upid = "UPID:pve01:power"
    getattr(qemu.status, endpoint).post.return_value = upid
    qemu.status.current.get.return_value = status
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    assert power.set_state(action) == (upid, ProxmoxPower.POWER_STATES[action])
    getattr(qemu.status, endpoint).post.assert_called_once_with(**options)
    wait.assert_called_once_with(upid, include_log_on_failure=True)


@pytest.mark.parametrize("action,expected_status", [
    ("start", "running"),
    ("stop", "stopped"),
    ("shutdown", "stopped"),
    ("reboot", "running"),
])
def test_lxc_set_state_supports_lxc_power_actions(action, expected_status, monkeypatch):
    """Use the LXC endpoint and verify each supported LXC action.

    Args:
        action: LXC power action to execute.
        expected_status: Status expected after the task completes.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    qemu_power, api = make_power()
    power = ProxmoxPower(qemu_power.module, client=qemu_power.client, guest_type="lxc")
    container = api.nodes.return_value.lxc.return_value
    upid = "UPID:pve01:{0}".format(action)
    getattr(container.status, action).post.return_value = upid
    container.status.current.get.return_value = {"status": expected_status}
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    assert power.set_state(action) == (upid, expected_status)

    getattr(container.status, action).post.assert_called_once_with()
    wait.assert_called_once_with(upid, include_log_on_failure=True)
    api.nodes.return_value.qemu.assert_not_called()


def test_lxc_set_state_rejects_qemu_only_actions():
    """Reject QEMU-only operations before accessing a container endpoint."""
    qemu_power, api = make_power()
    power = ProxmoxPower(qemu_power.module, client=qemu_power.client, guest_type="lxc")

    with pytest.raises(RuntimeError, match="module failed"):
        power.set_state("pause")

    assert "Unsupported LXC container power operation" in power.module.fail_json.call_args.kwargs["msg"]
    api.nodes.assert_not_called()


@pytest.mark.parametrize(
    "check_mode,current_status,expected_changed,executes_action,expected_result",
    [
        (False, {"status": "running", "qmpstatus": "running"}, False, False, "running"),
        (True, {"status": "stopped"}, True, False, "stopped"),
        (False, {"status": "stopped"}, True, True, "running"),
    ],
)
def test_shared_powerstate_action_lifecycle(
    check_mode, current_status, expected_changed, executes_action, expected_result, monkeypatch,
):
    """Cover shared no-op, check-mode, and real action lifecycles.

    Args:
        check_mode: Whether the lifecycle should predict without writing.
        current_status: Initial QEMU status response.
        expected_changed: Expected changed flag from the lifecycle.
        executes_action: Whether the API power endpoint should be called.
        expected_result: Expected powerstate in the module result.
        monkeypatch: Pytest fixture for replacing task waiting.
    """
    module = SimpleNamespace(
        params={"node": "pve01", "vmid": 100, "task_timeout": None, "powerstate": "start"},
        check_mode=check_mode,
        exit_json=Mock(side_effect=SystemExit),
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    api = Mock()
    qemu = api.nodes.return_value.qemu.return_value
    statuses = [current_status]
    if executes_action:
        statuses.append({"status": "running", "qmpstatus": "running"})
    qemu.status.current.get.side_effect = statuses
    upid = "UPID:pve01:start"
    qemu.status.start.post.return_value = upid
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)
    handler = ProxmoxPowerstateActionModule(module, client=SimpleNamespace(api=api, mark_changed=Mock()))

    with pytest.raises(SystemExit):
        handler.execute_action_module()

    result = module.exit_json.call_args.kwargs
    assert result["changed"] is expected_changed
    assert result["powerstate"] == expected_result
    if executes_action:
        assert result["task_upid"] == upid
        qemu.status.start.post.assert_called_once_with()
        wait.assert_called_once_with(upid, include_log_on_failure=True)
    else:
        assert "task_upid" not in result
        qemu.status.start.post.assert_not_called()
        wait.assert_not_called()


@pytest.mark.parametrize(
    "status,expected",
    [({"status": "running", "qmpstatus": "running"}, "running"), ({"status": "stopped"}, "stopped")],
)
def test_shared_powerstate_info_lifecycle(status, expected):
    """Return normalized power information through the shared read-only wrapper.

    Args:
        status: Current QEMU status response.
        expected: Expected normalized power state.
    """
    module = SimpleNamespace(
        params={"node": "pve01", "vmid": 100},
        check_mode=True,
        exit_json=Mock(side_effect=SystemExit),
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    api = Mock()
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = status
    handler = ProxmoxPowerstateInfoModule(module, client=SimpleNamespace(api=api))

    with pytest.raises(SystemExit):
        handler.execute_info_module()

    module.exit_json.assert_called_once_with(changed=False, powerstate=expected)


def test_power_rejects_unknown_guest_type():
    """Reject unsupported guest families during power helper initialization."""
    power, api = make_power()

    with pytest.raises(RuntimeError, match="module failed"):
        ProxmoxPower(power.module, client=power.client, guest_type="invalid")

    assert power.module.fail_json.call_args.kwargs["msg"] == "guest_type must be qemu or lxc."
    api.nodes.assert_not_called()
