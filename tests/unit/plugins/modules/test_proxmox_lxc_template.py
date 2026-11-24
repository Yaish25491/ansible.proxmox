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
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_template
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_template_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_template import ProxmoxLxcTemplateModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_template import ProxmoxLxcTemplateTask
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_template_info import ProxmoxLxcTemplateInfoModule

CONVERSION_UPID = "UPID:pve01:00000001:00000002:00000003:vztemplate:100:root@pam:"
OLD_CONVERSION_UPID = "UPID:pve01:00000001:00000002:00000001:vztemplate:100:root@pam:"
STOP_UPID = "UPID:pve01:00000001:00000002:00000003:vzstop:100:root@pam:"


class ModuleFailure(Exception):
    def __init__(self, **result):
        self.result = result


class FakeClient:
    def __init__(self, api, module):
        self.api = api
        self.module = module
        self.validate_pve_permissions = Mock()
        self.connection_options = Mock(return_value={"user": "root@pam"})
        self.mark_changed = Mock()

    def fail_request(self, operation, **details):
        self.module.fail_json(msg=operation, **details)


def make_template_module(params=None):
    """Create an LXC template handler with isolated module and API dependencies.

    Args:
        params: Optional module parameters to override the defaults.

    Returns:
        The template handler with its fake module and API resource.
    """
    module_params = {"vmid": 100, "power_cycle": "fail"}
    module_params.update(params or {})
    module = SimpleNamespace(
        params=module_params,
        check_mode=False,
        fail_json=Mock(side_effect=ModuleFailure),
    )
    client = FakeClient(Mock(), module)
    client.api.nodes.return_value.tasks.get.side_effect = [[], [{"upid": CONVERSION_UPID}]]
    resource = SimpleNamespace(template=SimpleNamespace(post=Mock()))
    handler = ProxmoxLxcTemplateModule(module, client=client)
    handler.call_api = Mock()
    handler.get_status = Mock()
    handler.node = Mock(return_value="pve01")
    handler.set_state = Mock(return_value=(STOP_UPID, "stopped"))
    handler.vm_resource = Mock(return_value=resource)
    handler._needs_stop = False
    return handler


def make_template_info_module(params=None):
    """Create an LXC template-info handler with mocked API boundaries.

    Args:
        params: Optional module parameters to override the defaults.

    Returns:
        The info handler with its fake module and client dependencies.
    """
    module_params = {"node": "pve01", "vmid": None}
    module_params.update(params or {})
    module = SimpleNamespace(
        params=module_params,
        check_mode=False,
        fail_json=Mock(side_effect=ModuleFailure),
    )
    client = FakeClient(Mock(), module)
    handler = ProxmoxLxcTemplateInfoModule(module, client=client)
    handler.call_api = Mock()
    handler.validate_proxmox_resource_exist = Mock()
    return handler


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"template": 1}, True),
        ({"template": "1"}, True),
        ({"template": True}, True),
        ({"template": 0}, False),
        ({"status": "stopped"}, False),
        (None, False),
    ],
)
def test_template_status_recognizes_supported_values(status, expected):
    """Recognize Proxmox template flags and reject other status values."""
    assert ProxmoxLxcTemplateModule._is_template_status(status) is expected


def test_existing_template_needs_no_action():
    """Leave an already converted container unchanged after checking permissions."""
    handler = make_template_module()
    handler.get_status.return_value = {"template": 1, "status": "stopped"}

    assert handler.is_action_needed() is False

    handler.client.validate_pve_permissions.assert_called_once_with(handler.api, "/vms")


@pytest.mark.parametrize("status", [None, {"status": "paused"}, {"status": "suspended"}])
def test_action_rejects_unknown_container_status(status):
    """Fail closed when the API cannot establish a supported LXC state."""
    handler = make_template_module()
    handler.get_status.return_value = status

    with pytest.raises(ModuleFailure):
        handler.is_action_needed()

    assert handler.module.fail_json.call_count == 1


def test_action_requires_power_cycle_policy_for_running_container():
    """Reject conversion of a running container under the default fail policy."""
    handler = make_template_module()
    handler.get_status.return_value = {"status": "running"}

    with pytest.raises(ModuleFailure):
        handler.is_action_needed()

    assert "power_cycle=off" in handler.module.fail_json.call_args.kwargs["msg"]
    handler.set_state.assert_not_called()
    handler.call_api.assert_not_called()
    handler.api.nodes.return_value.tasks.get.assert_not_called()


@pytest.mark.parametrize("check_mode,status,expected_changed", [
    (True, {"status": "stopped"}, True),
    (True, {"status": "running"}, True),
    (False, {"status": "stopped", "template": 1}, False),
    (True, {"status": "stopped", "template": 1}, False),
])
def test_check_mode_and_existing_templates_do_not_create_tasks(check_mode, status, expected_changed):
    handler = make_template_module({"power_cycle": "off"})
    handler.module.check_mode = check_mode
    handler.get_status.return_value = status
    handler.module.exit_json = Mock(side_effect=RuntimeError("exited"))

    with pytest.raises(RuntimeError, match="exited"):
        handler.execute_action_module()

    assert handler.module.exit_json.call_args.kwargs == {"changed": expected_changed, "template": True}
    handler.set_state.assert_not_called()
    handler.call_api.assert_not_called()
    handler.api.nodes.return_value.tasks.get.assert_not_called()


@pytest.mark.parametrize(
    ("status", "expected_stop"),
    [({"status": "stopped"}, False), ({"status": "running"}, True)],
)
def test_action_records_whether_container_must_be_stopped(status, expected_stop):
    """Accept convertible states and record required power transitions."""
    handler = make_template_module({"power_cycle": "off"})
    handler.get_status.return_value = status

    assert handler.is_action_needed() is True

    assert handler._needs_stop is expected_stop


def test_perform_action_waits_and_verifies_template_conversion(monkeypatch):
    """Submit conversion, wait for its task, and verify the resulting status."""
    handler = make_template_module()
    handler.call_api.return_value = CONVERSION_UPID
    handler.get_status.return_value = {"template": 1}
    wait_for_task = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait_for_task)

    assert handler.perform_action() is True

    handler.call_api.assert_called_once_with(
        "Converting LXC container 100 on node pve01 to a template",
        handler.vm_resource.return_value.template.post,
    )
    assert wait_for_task.call_args.args == (CONVERSION_UPID,)
    assert wait_for_task.call_args.kwargs["include_log_on_failure"] is True
    handler.get_status.assert_called_once_with()
    handler.client.mark_changed.assert_called_once_with()
    assert handler.prepare_result(True, True)["task_upid"] == CONVERSION_UPID


@pytest.mark.parametrize("running,policy", [(False, "fail"), (False, "off"), (True, "off")])
def test_null_response_returns_conversion_upid_for_each_power_policy(monkeypatch, running, policy):
    """Recover the actual conversion worker regardless of preparatory stopping."""
    handler = make_template_module({"power_cycle": policy})
    handler.get_status.side_effect = [
        {"status": "running" if running else "stopped"}, {"template": 1, "status": "stopped"},
    ]
    handler.call_api.return_value = None
    events = Mock()
    events.attach_mock(handler.set_state, "stop")
    events.attach_mock(handler.api.nodes.return_value.tasks.get, "list_tasks")
    events.attach_mock(handler.call_api, "convert")
    wait_for_task = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait_for_task)

    assert handler.is_action_needed() is True
    assert handler.perform_action() is True

    assert [event[0] for event in events.mock_calls] == (
        (["stop"] if running else []) + ["list_tasks", "convert", "list_tasks"]
    )
    assert handler.prepare_result(True, True)["task_upid"] == CONVERSION_UPID
    assert wait_for_task.call_args.args == (CONVERSION_UPID,)
    assert handler._task_upid != STOP_UPID


def test_direct_conversion_upid_never_returns_stop_upid(monkeypatch):
    handler = make_template_module()
    handler._needs_stop = True
    handler.call_api.return_value = CONVERSION_UPID
    handler.get_status.return_value = {"template": 1}
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    assert handler.perform_action() is True

    assert handler.prepare_result(True, True)["task_upid"] == CONVERSION_UPID
    handler.api.nodes.return_value.tasks.get.assert_called_once()


def test_conversion_discovery_waits_for_task_visibility(monkeypatch):
    handler = make_template_module()
    handler.call_api.return_value = None
    handler.get_status.return_value = {"template": 1}
    handler.api.nodes.return_value.tasks.get.side_effect = [[], [], [{"upid": CONVERSION_UPID}]]
    sleep = Mock()
    monkeypatch.setattr(proxmox_lxc_template.time, "sleep", sleep)
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    assert handler.perform_action() is True

    assert handler._task_upid == CONVERSION_UPID
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("exit_status", ["OK", "WARNINGS: 1", "conversion failed"])
def test_discovered_task_uses_shared_completion_and_error_handling(exit_status):
    handler = make_template_module()
    handler.call_api.return_value = None
    handler.get_status.return_value = {"template": 1}
    handler.module.warn = Mock()
    endpoint = handler.api.nodes.return_value.tasks.return_value
    endpoint.status.get.return_value = {"status": "stopped", "exitstatus": exit_status}
    endpoint.log.get.return_value = [{"t": "WARN: conversion warning"}]

    if exit_status == "conversion failed":
        with pytest.raises(ModuleFailure):
            handler.perform_action()
        failure = handler.module.fail_json.call_args.kwargs
        assert failure["task"] == CONVERSION_UPID
        assert failure["task_exit_status"] == exit_status
        handler.get_status.assert_not_called()
    else:
        assert handler.perform_action() is True
        assert handler.prepare_result(True, True)["task_upid"] == CONVERSION_UPID
        if exit_status.startswith("WARNINGS:"):
            handler.module.warn.assert_called_once_with("conversion warning")


def test_failed_stop_prevents_conversion_submission():
    handler = make_template_module({"power_cycle": "off"})
    handler._needs_stop = True
    handler.set_state.side_effect = ModuleFailure

    with pytest.raises(ModuleFailure):
        handler.perform_action()

    handler.call_api.assert_not_called()
    handler.api.nodes.return_value.tasks.get.assert_not_called()


def test_conversion_discovery_does_not_select_old_or_stop_tasks(monkeypatch):
    handler = make_template_module()
    handler.call_api.return_value = None
    handler.get_status.return_value = {"template": 1}
    old = {"upid": OLD_CONVERSION_UPID}
    handler.api.nodes.return_value.tasks.get.side_effect = [
        [old], [old, {"upid": STOP_UPID}, {"upid": CONVERSION_UPID}],
    ]
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    assert handler.perform_action() is True
    assert handler._task_upid == CONVERSION_UPID


def test_conversion_discovery_refuses_ambiguous_new_tasks(monkeypatch):
    handler = make_template_module()
    handler.call_api.return_value = None
    handler.api.nodes.return_value.tasks.get.side_effect = [
        [], [{"upid": OLD_CONVERSION_UPID}, {"upid": CONVERSION_UPID}],
    ]
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(ModuleFailure):
        handler.perform_action()

    failure = handler.module.fail_json.call_args.kwargs
    assert "Multiple new LXC template tasks" in failure["msg"]
    assert failure["changed"] is True
    wait.assert_not_called()


def test_conversion_discovery_timeout_never_reports_stop_as_conversion(monkeypatch):
    handler = make_template_module({"task_timeout": 1})
    handler._needs_stop = True
    handler.call_api.return_value = None
    handler.api.nodes.return_value.tasks.get.side_effect = [[], [{"upid": STOP_UPID}]]
    monkeypatch.setattr(proxmox_lxc_template.time, "monotonic", Mock(side_effect=[0, 2]))

    with pytest.raises(ModuleFailure):
        handler.perform_action()

    failure = handler.module.fail_json.call_args.kwargs
    assert "Timed out discovering" in failure["msg"]
    assert failure["changed"] is True
    handler.get_status.assert_not_called()


def test_perform_action_fails_if_conversion_does_not_reach_template(monkeypatch):
    """Fail after task completion if the container is not reported as a template."""
    handler = make_template_module({"task_timeout": 1})
    handler.call_api.return_value = CONVERSION_UPID
    handler.get_status.return_value = {"template": 0}
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    with pytest.raises(ModuleFailure):
        handler.perform_action()

    assert "did not reach the template state" in handler.module.fail_json.call_args.kwargs["msg"]


@pytest.mark.parametrize("timeout,clock_values,expected", [(None, [10, 11], 299), (5, [10, 12], 3), (0, [], 0)])
def test_conversion_reuses_shared_waiter_with_remaining_timeout(monkeypatch, timeout, clock_values, expected):
    handler = make_template_module({"task_timeout": timeout})
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    clock = Mock(side_effect=clock_values)
    monkeypatch.setattr(proxmox_lxc_template.time, "monotonic", clock)
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    assert task.wait_for_conversion(CONVERSION_UPID, "pve01", 100, set()) == CONVERSION_UPID

    wait.assert_called_once_with(CONVERSION_UPID, node="pve01", timeout=expected, include_log_on_failure=True)
    if timeout == 0:
        clock.assert_not_called()


def test_conversion_discovery_does_not_pass_expired_timeout_as_unlimited(monkeypatch):
    handler = make_template_module({"task_timeout": 1})
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    monkeypatch.setattr(proxmox_lxc_template.time, "monotonic", Mock(side_effect=[0, 2]))
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(ModuleFailure):
        task.wait_for_conversion(CONVERSION_UPID, "pve01", 100, set())

    wait.assert_not_called()


def test_conversion_inventory_paginates_and_filters_identity():
    handler = make_template_module()
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    task.PAGE_SIZE = 2
    wrong_owner = CONVERSION_UPID.replace("root@pam", "other@pve")
    handler.api.nodes.return_value.tasks.get.side_effect = [
        [{"upid": OLD_CONVERSION_UPID}, {"upid": wrong_owner}], [{"upid": CONVERSION_UPID}],
    ]

    assert task.conversion_upids("pve01", 100) == {OLD_CONVERSION_UPID, CONVERSION_UPID}
    calls = handler.api.nodes.return_value.tasks.get.call_args_list
    assert [call.kwargs["start"] for call in calls] == [0, 2]
    assert all(call.kwargs["source"] == "all" and call.kwargs["typefilter"] == "vztemplate" for call in calls)


def test_conversion_inventory_rejects_malformed_response():
    handler = make_template_module()
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    handler.api.nodes.return_value.tasks.get.side_effect = [None]

    with pytest.raises(ModuleFailure):
        task.conversion_upids("pve01", 100)

    assert "Unexpected response" in handler.module.fail_json.call_args.kwargs["msg"]


def test_conversion_identity_includes_token_name():
    handler = make_template_module()
    handler.client.connection_options.return_value = {"user": "ansible@pve", "token_name": "ci"}
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    token_upid = CONVERSION_UPID.replace("root@pam", "ansible@pve!ci")

    assert task.matches_conversion(token_upid, "pve01", 100)
    assert not task.matches_conversion(token_upid.replace("!ci", "!other"), "pve01", 100)


@pytest.mark.parametrize("upid", [None, "UPID:pve01:task", STOP_UPID,
                                  CONVERSION_UPID.replace("pve01", "pve02"),
                                  CONVERSION_UPID.replace(":100:", ":101:"),
                                  CONVERSION_UPID.replace("UPID:", "OTHER:")])
def test_conversion_identity_rejects_unrelated_tasks(upid):
    handler = make_template_module()
    task = ProxmoxLxcTemplateTask(handler.module, client=handler.client)
    assert not task.matches_conversion(upid, "pve01", 100)


def test_action_result_and_task_result_shaping():
    """Return the template state and include a task UPID only when available."""
    handler = make_template_module()

    assert handler.get_action_result() is True
    assert handler.prepare_result(changed=False, resource=True) == {"changed": False, "template": True}

    handler._task_upid = "UPID:pve01:lxc-template"
    assert handler.prepare_result(changed=True, resource=True) == {
        "changed": True,
        "template": True,
        "task_upid": "UPID:pve01:lxc-template",
    }


def test_main_registers_template_arguments_and_executes_handler(monkeypatch):
    """Register module arguments and delegate to the action handler."""
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(proxmox_lxc_template, "AnsibleModule", constructor)
    monkeypatch.setattr(proxmox_lxc_template, "ProxmoxLxcTemplateModule", handler_constructor)

    proxmox_lxc_template.main()

    arguments = constructor.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert arguments["argument_spec"]["vmid"] == {"type": "int", "required": True}
    assert arguments["argument_spec"]["power_cycle"] == {
        "type": "str",
        "choices": ["fail", "off"],
        "default": "fail",
    }
    assert arguments["argument_spec"]["task_timeout"] == {"type": "int"}
    handler_constructor.assert_called_once_with(module)
    handler.execute_action_module.assert_called_once_with()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"template": 1}, True),
        ({"template": "1"}, True),
        ({"template": True}, True),
        ({"template": 0}, False),
        ({"status": "stopped"}, False),
        (None, False),
    ],
)
def test_template_info_status_recognizes_supported_values(status, expected):
    """Recognize template flags in LXC status responses."""
    assert ProxmoxLxcTemplateInfoModule._is_template_status(status) is expected


def test_template_info_lists_templates_from_lxc_index_without_extra_status_calls():
    """Filter template records from the LXC index without per-container reads."""
    handler = make_template_info_module()
    containers = [
        {"vmid": 100, "template": 1, "status": "stopped"},
        {"vmid": "101", "template": "1", "status": "stopped"},
        {"vmid": 102, "template": 0, "status": "stopped"},
        {"vmid": 103, "status": "stopped"},
        {"vmid": None, "template": 1},
        {"name": "missing-vmid", "template": 1},
        "invalid-resource",
        None,
    ]
    handler.call_api.return_value = containers

    result = handler.get_info()

    assert result == [
        {"vmid": 100, "template": 1, "status": "stopped"},
        {"vmid": "101", "template": "1", "status": "stopped"},
    ]
    assert result[0] is not containers[0]
    assert containers[0] == {"vmid": 100, "template": 1, "status": "stopped"}

    handler.client.validate_pve_permissions.assert_called_once_with(handler.api, "/vms")
    handler.validate_proxmox_resource_exist.assert_called_once_with(
        "node",
        "pve01",
        handler.api.nodes.get,
        resource_key="node",
    )
    handler.call_api.assert_called_once_with(
        "Retrieving LXC containers from node pve01",
        handler.api.nodes("pve01").lxc.get,
    )


def test_template_info_returns_only_requested_vmid_when_template():
    """Filter the LXC index by requested VMID while preserving its value type."""
    handler = make_template_info_module({"vmid": 101})
    handler.call_api.return_value = [
        {"vmid": "101", "template": "1", "status": "stopped"},
        {"vmid": 100, "template": 1, "status": "stopped"},
    ]

    assert handler.get_info() == [{"template": "1", "status": "stopped", "vmid": "101"}]
    handler.call_api.assert_called_once_with(
        "Retrieving LXC containers from node pve01",
        handler.api.nodes("pve01").lxc.get,
    )


def test_template_info_returns_empty_for_requested_non_template():
    """Return an empty list when a requested container is not a template."""
    handler = make_template_info_module({"vmid": 100})
    handler.call_api.return_value = [{"vmid": 100, "template": 0, "status": "stopped"}]

    assert handler.get_info() == []
    handler.call_api.assert_called_once()


def test_template_info_returns_empty_when_requested_vmid_is_missing():
    """Return no matches for a VMID absent from the node inventory."""
    handler = make_template_info_module({"vmid": 107})
    handler.call_api.return_value = [{"vmid": 100, "template": 1, "status": "stopped"}]

    assert handler.get_info() == []
    handler.call_api.assert_called_once()


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_template_info_rejects_vmid_outside_supported_range(vmid):
    """Reject an out-of-range VMID before making API requests."""
    handler = make_template_info_module({"vmid": vmid})

    with pytest.raises(ModuleFailure):
        handler.get_info()

    assert "VMID must be between 100 and 999999999" in handler.module.fail_json.call_args.kwargs["msg"]
    handler.client.validate_pve_permissions.assert_not_called()
    handler.call_api.assert_not_called()


def test_info_main_registers_arguments_and_executes_handler(monkeypatch):
    """Register info arguments and delegate to the read-only lifecycle."""
    module = SimpleNamespace()
    constructor = Mock(return_value=module)
    handler = Mock()
    handler_constructor = Mock(return_value=handler)
    monkeypatch.setattr(proxmox_lxc_template_info, "AnsibleModule", constructor)
    monkeypatch.setattr(ProxmoxLxcTemplateInfoModule, "execute_info_module", Mock())
    monkeypatch.setattr(proxmox_lxc_template_info, "ProxmoxLxcTemplateInfoModule", handler_constructor)

    proxmox_lxc_template_info.main()

    arguments = constructor.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert arguments["argument_spec"]["node"] == {"type": "str", "required": True}
    assert arguments["argument_spec"]["vmid"] == {"type": "int"}
    documentation = yaml.safe_load(proxmox_lxc_template_info.DOCUMENTATION)
    seealso_modules = [item["module"] for item in documentation["seealso"]]
    assert "ansible.proxmox.proxmox_lxc" in seealso_modules
    handler_constructor.assert_called_once_with(module)
    handler.execute_info_module.assert_called_once_with()


@pytest.mark.parametrize("module_name", ["proxmox_lxc_template", "proxmox_lxc_template_info"])
def test_lxc_template_modules_are_registered_for_group_defaults(module_name):
    """Keep both LXC template modules in the shared Proxmox action group."""
    runtime_path = Path(__file__).resolve().parents[4] / "meta" / "runtime.yml"
    metadata = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    assert module_name in metadata["action_groups"]["proxmox"]
