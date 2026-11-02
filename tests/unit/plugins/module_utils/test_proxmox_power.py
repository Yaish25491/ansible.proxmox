# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


def make_power(**params):
    module = SimpleNamespace(
        params=dict({"node": "pve01", "vmid": 100, "task_timeout": None}, **params),
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    api = Mock()
    return ProxmoxPower(module, client=SimpleNamespace(api=api)), api


def test_get_status_uses_current_qemu_endpoint():
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
    assert ProxmoxPower.configuration_options(policy) == expected


def test_require_known_rejects_missing_status():
    power, api = make_power()

    with pytest.raises(RuntimeError, match="module failed"):
        power.require_known(None, "change VM power state")

    assert "Unable to verify VM power state" in power.module.fail_json.call_args.kwargs["msg"]
    power.require_known("stopped", "change VM power state")
    api.nodes.assert_not_called()


@pytest.mark.parametrize("operation", ["delete", "update"])
@pytest.mark.parametrize("status", ["running", None])
def test_require_stopped_rejects_powered_on_or_unknown_vm(status, operation):
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
    power, api = make_power()

    assert power.needs_change(status, policy) is expected
    api.nodes.assert_not_called()


@pytest.mark.parametrize("policy", ["on", "off"])
def test_needs_change_rejects_unknown_status(policy):
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
    assert ProxmoxPower.should_stop(status, policy, has_updates) is stop
    assert ProxmoxPower.should_start(status, policy) is start


@pytest.mark.parametrize("action,expected", [("start", "running"), ("stop", "stopped")])
@pytest.mark.parametrize("timeout", [None, 42])
def test_set_state_waits_for_upid_and_verifies_status(action, expected, timeout, monkeypatch):
    power, api = make_power(task_timeout=timeout)
    qemu = api.nodes.return_value.qemu.return_value
    getattr(qemu.status, action).post.return_value = "UPID:pve01:{0}".format(action)
    qemu.status.current.get.return_value = {"status": expected}
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    assert power.set_state(action) == ("UPID:pve01:{0}".format(action), expected)

    wait.assert_called_once_with("UPID:pve01:{0}".format(action), **({} if timeout is None else {"timeout": timeout}))
    qemu.status.current.get.assert_called_once_with()


@pytest.mark.parametrize("action", ["start", "stop"])
def test_set_state_requires_successful_upid_and_expected_status(action, monkeypatch):
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)
    endpoint = getattr(qemu.status, action).post

    endpoint.return_value = None
    with pytest.raises(RuntimeError, match="module failed"):
        power.set_state(action)
    wait.assert_not_called()

    endpoint.return_value = "UPID:pve01:{0}".format(action)
    qemu.status.current.get.return_value = {"status": "stopped" if action == "start" else "running"}
    with pytest.raises(RuntimeError, match="module failed"):
        power.set_state(action)
    wait.assert_called_once_with(endpoint.return_value)
    assert "did not reach" in power.module.fail_json.call_args.kwargs["msg"]


def test_set_state_does_not_check_status_when_task_fails(monkeypatch):
    power, api = make_power()
    qemu = api.nodes.return_value.qemu.return_value
    qemu.status.stop.post.return_value = "UPID:pve01:stop"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock(side_effect=RuntimeError("task failed")))

    with pytest.raises(RuntimeError, match="task failed"):
        power.set_state("stop")

    qemu.status.current.get.assert_not_called()


def test_set_state_rejects_invalid_action_before_api_call():
    power, api = make_power()

    with pytest.raises(ValueError, match="Unsupported VM power operation"):
        power.set_state("reset")

    api.nodes.assert_not_called()
