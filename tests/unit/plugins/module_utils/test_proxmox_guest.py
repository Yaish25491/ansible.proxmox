# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise both guest lifecycles with real Ansible result and failure handling."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

try:
    from ansible.module_utils import _internal
except ImportError:
    _internal = None

from ansible.module_utils import basic

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_guest import ProxmoxGuestModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc import ProxmoxLxcModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm import ProxmoxVmModule


@pytest.fixture
def guest_environment(monkeypatch):
    """Build a real Ansible module with a stateful mocked Proxmox API.

    Args:
        monkeypatch: Fixture used only to supply serialized Ansible arguments.

    Returns:
        A factory producing a guest handler, mutable state, and API events.
    """

    def create(guest_type="lxc", exists=True, status="stopped", check_mode=False, config=None, **parameters):
        """Configure a guest and an API that records writes and task completion.

        Args:
            guest_type: Guest API family.
            exists: Whether the guest exists before reconciliation.
            status: Initial power state.
            check_mode: Whether writes must be suppressed.
            config: Optional initial guest configuration.
            **parameters: Module parameter overrides.

        Returns:
            The guest handler, real module, API, state, events, and client.
        """
        arguments = dict(node="pve01", vmid=1003, storage="local-lvm", ostemplate="fedora.tar.xz")
        arguments.update(parameters)
        arguments["_ansible_check_mode"] = check_mode
        monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": arguments}).encode())
        monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy", raising=False)
        monkeypatch.setattr(basic, "_ANSIBLE_PARAMS", None, raising=False)
        if _internal is not None and hasattr(_internal, "is_controller"):
            monkeypatch.setattr(_internal, "is_controller", False)
        module = basic.AnsibleModule(
            argument_spec={
                "node": {"type": "str"},
                "vmid": {"type": "int"},
                "state": {"type": "str", "default": "present"},
                "power_cycle": {"type": "str", "default": "fail"},
                "memory": {"type": "int"},
                "cores": {"type": "int"},
                "cpulimit": {"type": "float"},
                "cpuunits": {"type": "int"},
                "swap": {"type": "int"},
                "password": {"type": "str", "no_log": True},
                "storage": {"type": "str"},
                "ostemplate": {"type": "str"},
                "rootfs": {"type": "str"},
            },
            supports_check_mode=True,
        )
        assert module.fail_json.__func__ is basic.AnsibleModule.fail_json
        events = []
        state = {"exists": exists, "status": status, "config": dict(config if config is not None else {"memory": 512})}
        api = Mock()
        api.nodes.get.return_value = [{"node": "pve01"}]
        node = api.nodes.return_value
        collection = getattr(node, guest_type)
        collection.get.side_effect = lambda: [{"vmid": 1003}] if state["exists"] else []
        container = collection.return_value
        container.status.current.get.side_effect = lambda: {"status": state["status"]}
        container.config.get.side_effect = lambda **_kwargs: dict(state["config"])
        node.storage.get.return_value = [{"storage": "local-lvm"}]
        node.storage.return_value.content.get.return_value = [{"volid": "local:vztmpl/fedora.tar.xz"}]

        def create_guest(**updates):
            """Record creation and expose a server-resolved root volume.

            Args:
                **updates: API creation parameters.

            Returns:
                The creation task identifier.
            """
            events.append("create")
            state["exists"] = True
            state["config"] = {"memory": updates.get("memory", 512)}
            if guest_type == "lxc":
                state["config"]["rootfs"] = "local-lvm:vm-1003-disk-0,size=4G"
            return "UPID:pve01:create"

        def update_guest(**updates):
            """Apply predicted changes while preserving LXC's synchronous PUT.

            Args:
                **updates: Changed API values and deletion directives.

            Returns:
                A QEMU task identifier, or None for an LXC config update.
            """
            events.append("update")
            state["config"] = ProxmoxGuestModule.apply_parameter_updates(state["config"], updates)
            return "UPID:pve01:update" if guest_type == "qemu" else None

        def delete_guest():
            """Record removal and return its asynchronous task identifier."""
            events.append("delete")
            state["exists"] = False
            return "UPID:pve01:delete"

        def power_action(action):
            """Record a power action and expose its target status.

            Args:
                action: Start or stop operation.

            Returns:
                The power task identifier.
            """
            events.append(action)
            state["status"] = "running" if action == "start" else "stopped"
            return "UPID:pve01:{0}".format(action)

        def task_endpoint(upid):
            """Produce a completed task response and record the actual wait.

            Args:
                upid: Task identifier being polled.

            Returns:
                An endpoint exposing the task status getter.
            """

            def get_status():
                """Record task completion without waiting on wall-clock time."""
                events.append("wait:{0}".format(upid.rsplit(":", 1)[-1]))
                return {"status": "stopped", "exitstatus": "OK"}

            return SimpleNamespace(status=SimpleNamespace(get=get_status))

        collection.create.side_effect = create_guest
        container.config.post.side_effect = update_guest
        container.config.put.side_effect = update_guest
        container.delete.side_effect = delete_guest
        container.status.start.post.side_effect = lambda: power_action("start")
        container.status.stop.post.side_effect = lambda: power_action("stop")
        node.tasks.side_effect = task_endpoint
        client = ProxmoxClient(module)
        client._api = api
        client.validate_pve_permissions = Mock()
        guest_class = ProxmoxLxcModule if guest_type == "lxc" else ProxmoxVmModule
        return SimpleNamespace(
            handler=guest_class(module, client=client),
            module=module,
            api=api,
            client=client,
            state=state,
            events=events,
            container=container,
        )

    return create


def reconcile(environment, capsys):
    """Capture JSON written by the real Ansible exit_json or fail_json.

    Args:
        environment: Guest test environment.
        capsys: Pytest output capture fixture.

    Returns:
        The module's serialized result mapping.
    """
    with pytest.raises(SystemExit):
        environment.handler.execute_resource_module()
    return json.loads(capsys.readouterr().out)


def warning_messages(result):
    """Extract warning text from Ansible's serialized warning events."""
    return [
        warning.get("event", {}).get("msg", "") if isinstance(warning, dict) else warning
        for warning in result.get("warnings", [])
    ]


@pytest.mark.parametrize("guest_class", [ProxmoxVmModule, ProxmoxLxcModule])
def test_guest_classes_share_lifecycle_implementation(guest_class):
    """Prevent guest modules from introducing separate lifecycle copies.

    Args:
        guest_class: Concrete guest module under test.
    """
    for method in (
        "execute_resource_module",
        "_wait_for_task",
        "_apply_power_state",
        "update_resource",
        "delete_resource",
        "get_check_mode_resource",
    ):
        assert getattr(guest_class, method) is getattr(ProxmoxGuestModule, method)


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "policy,status,memory,actions",
    [
        ("on", "running", 1024, ["stop", "update", "start"]),
        ("on", "stopped", 1024, ["update", "start"]),
        ("on", "stopped", 512, ["start"]),
        ("on", "running", 512, []),
        ("off", "running", 1024, ["stop", "update"]),
        ("off", "stopped", 512, []),
        ("off", "running", 512, ["stop"]),
        ("hotplug", "running", 1024, ["update"]),
        ("hotplug", "running", 512, []),
        ("fail", "stopped", 1024, ["update"]),
        ("fail", "running", 512, []),
    ],
)
def test_shared_guest_policy_matrix(guest_environment, capsys, guest_type, check_mode, policy, status, memory, actions):
    """Keep power ordering, check mode, and idempotency identical across guests.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        guest_type: QEMU or LXC API family.
        check_mode: Whether to predict without writing.
        policy: Requested power policy.
        status: Initial guest power status.
        memory: Requested RAM size.
        actions: Expected mutation ordering outside check mode.
    """
    environment = guest_environment(guest_type, status=status, check_mode=check_mode, power_cycle=policy, memory=memory)
    result = reconcile(environment, capsys)
    assert not result.get("failed")
    assert result["changed"] is bool(actions)
    expected_events = []
    for action in actions if not check_mode else []:
        expected_events.append(action)
        if action != "update" or guest_type == "qemu":
            expected_events.append("wait:{0}".format(action))
    assert environment.events == expected_events
    if check_mode:
        assert environment.state["status"] == status
        assert environment.state["config"] == {"memory": 512}
        assert "task_upid" not in result
    else:
        environment.events.clear()
        environment.handler = type(environment.handler)(environment.module, client=environment.client)
        second = reconcile(environment, capsys)
        assert second["changed"] is False
        assert environment.events == []


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("policy", ["on", "off", "fail", "hotplug"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_shared_guest_creation(guest_environment, capsys, guest_type, policy, check_mode):
    """Create either guest without writing in check mode or starting by default.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        guest_type: QEMU or LXC API family.
        policy: Requested initial power policy.
        check_mode: Whether to suppress mutations.
    """
    environment = guest_environment(guest_type, exists=False, power_cycle=policy, check_mode=check_mode)
    result = reconcile(environment, capsys)
    assert result["changed"] is True
    assert environment.events == ([] if check_mode else ["create", "wait:create"] + (["start", "wait:start"] if policy == "on" else []))


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
def test_creation_read_failure_reports_confirmed_mutation(guest_environment, capsys, guest_type):
    """Retain changed=true when creation succeeds but reading config fails.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        guest_type: QEMU or LXC API family.
    """
    environment = guest_environment(guest_type, exists=False)
    environment.container.config.get.side_effect = RuntimeError("configuration read failed")

    result = reconcile(environment, capsys)

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["api_error"] == "configuration read failed"
    assert environment.state["exists"] is True
    assert environment.events == ["create", "wait:create"]


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_shared_guest_deletion(guest_environment, capsys, guest_type, check_mode):
    """Stop and wait before deletion while predicting writes in check mode.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        guest_type: QEMU or LXC API family.
        check_mode: Whether to suppress mutations.
    """
    environment = guest_environment(guest_type, status="running", state="absent", power_cycle="off", check_mode=check_mode)
    result = reconcile(environment, capsys)
    assert result["changed"] is True
    assert environment.events == ([] if check_mode else ["stop", "wait:stop", "delete", "wait:delete"])
    assert environment.state["exists"] is check_mode


@pytest.mark.parametrize("response", [None, "not-a-task", {}, 42])
def test_lxc_power_requires_upid(guest_environment, capsys, response):
    """Reject malformed power responses through the real Ansible fail_json.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        response: Invalid response returned by the power endpoint.
    """
    environment = guest_environment(power_cycle="on")
    environment.container.status.start.post.side_effect = None
    environment.container.status.start.post.return_value = response
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert "did not return a task UPID" in result["msg"]
    assert environment.events == []


@pytest.mark.parametrize(
    "rootfs,current,message",
    [
        ("local-lvm:8", "local-lvm:vm-1003-disk-0,size=4G", "Changing rootfs size"),
        ("other:4", "local-lvm:vm-1003-disk-0,size=4G", "Changing rootfs storage"),
        ("local-lvm:4", "local-lvm:vm-1003-disk-0", "Unable to verify"),
        ("local-lvm:vm-1003-disk-0,size=8G", "local-lvm:vm-1003-disk-0,size=4G", "Changing rootfs size"),
    ],
)
def test_lxc_rejects_unsupported_rootfs_changes(guest_environment, capsys, rootfs, current, message):
    """Fail before writes when allocation cannot safely describe the existing disk.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        rootfs: Requested root filesystem specification.
        current: Existing root filesystem property string.
        message: Expected validation failure text.
    """
    environment = guest_environment(rootfs=rootfs, config={"rootfs": current})
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert message in result["msg"]
    assert environment.events == []


def test_lxc_repeated_creation_settings_are_idempotent(guest_environment, capsys):
    """Ignore the write-only password and retain the server-allocated root disk.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
    """
    environment = guest_environment(
        password="test-only-password",
        rootfs="local-lvm:4",
        config={"memory": 512, "rootfs": "local-lvm:vm-1003-disk-0,size=4G"},
    )
    result = reconcile(environment, capsys)
    assert result["changed"] is False
    assert environment.events == []


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("cores", 0),
        ("cores", 8193),
        ("cpulimit", -1),
        ("cpulimit", 8193),
        ("cpuunits", -1),
        ("cpuunits", 500001),
        ("memory", 15),
        ("swap", -1),
    ],
)
def test_lxc_numeric_limits_fail_before_api_calls(guest_environment, capsys, parameter, value):
    """Validate LXC ranges without unsupported Ansible argument-spec keys.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
        parameter: Numeric module option under test.
        value: Value outside the supported range.
    """
    environment = guest_environment(**{parameter: value})
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert parameter in result["msg"]
    environment.api.nodes.assert_not_called()


def test_lxc_synchronous_update_retains_last_power_task(guest_environment, capsys):
    """Keep the completed stop UPID when the following config PUT is synchronous.

    Args:
        guest_environment: Stateful API factory.
        capsys: Ansible JSON output capture.
    """
    environment = guest_environment(status="running", power_cycle="off", memory=1024)
    result = reconcile(environment, capsys)
    assert result["task_upid"] == "UPID:pve01:stop"


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
def test_guest_requires_vmid_before_lookup(guest_environment, capsys, guest_type):
    """Reject missing identifiers in the shared guest executor.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for real Ansible JSON results.
        guest_type: Guest API family under test.
    """
    environment = guest_environment(guest_type, vmid=None)
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert "VMID" in result["msg"]
    environment.api.nodes.assert_not_called()


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_guest_executor_enforces_update_policy(guest_environment, capsys, monkeypatch, guest_type, check_mode):
    """Enforce fail policy even when an override only computes the API delta.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for real Ansible JSON results.
        monkeypatch: Fixture replacing only the update-comparison hook.
        guest_type: Guest API family under test.
        check_mode: Whether the request should predict rather than apply changes.
    """
    environment = guest_environment(guest_type, status="running", check_mode=check_mode)
    monkeypatch.setattr(environment.handler, "get_update_parameters", lambda resource: {"memory": 1024})
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert "power it off" in result["msg"]
    assert environment.events == []


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
def test_guest_deletion_fails_cleanly_without_observed_status(guest_environment, capsys, monkeypatch, guest_type):
    """Report an Ansible failure rather than accessing an unset private field.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for real Ansible JSON results.
        monkeypatch: Fixture replacing only the resource lookup hook.
        guest_type: Guest API family under test.
    """
    environment = guest_environment(guest_type, state="absent")
    monkeypatch.setattr(environment.handler, "get_resource", lambda: {"memory": 512})
    result = reconcile(environment, capsys)
    assert result["failed"] is True
    assert "Unable to verify" in result["msg"]
    assert environment.events == []


@pytest.mark.parametrize("action", ["reset", "pause", "hibernate", "resume", "current", "invalid"])
def test_lxc_power_rejects_unsupported_actions_before_lookup(guest_environment, capsys, action):
    """Restrict LXC power operations without patching Ansible fail_json.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for real Ansible JSON failures.
        action: An action outside the supported LXC command set.
    """
    environment = guest_environment()
    environment.module.params["node"] = None
    with pytest.raises(SystemExit):
        environment.handler.power.set_state(action)
    result = json.loads(capsys.readouterr().out)
    assert result["failed"] is True
    assert "Unsupported LXC container power operation" in result["msg"]
    environment.api.nodes.assert_not_called()
    environment.api.cluster.resources.get.assert_not_called()


@pytest.mark.parametrize(
    "action,expected",
    [
        ("start", "running"),
        ("reboot", "running"),
        ("shutdown", "stopped"),
        ("stop", "stopped"),
    ],
)
def test_lxc_supported_power_actions_use_post_and_current_get(guest_environment, action, expected):
    """Use LXC endpoints, wait for tasks, and retrieve status with GET.

    Args:
        guest_environment: Stateful guest API factory.
        action: A supported LXC power command.
        expected: Final normalized container state.
    """
    environment = guest_environment(status=expected)
    endpoint = getattr(environment.container.status, action)
    endpoint.post.side_effect = None
    endpoint.post.return_value = "UPID:pve01:{0}".format(action)
    assert environment.handler.power.set_state(action) == (endpoint.post.return_value, expected)
    endpoint.post.assert_called_once_with()
    environment.container.status.current.get.assert_called_once_with()
    environment.container.status.current.post.assert_not_called()
    environment.api.nodes.return_value.qemu.assert_not_called()
    assert environment.events == ["wait:{0}".format(action)]


@pytest.mark.parametrize(
    "status,expected",
    [
        ({"status": "running", "qmpstatus": "paused"}, "running"),
        ({"status": "running", "qmpstatus": "io-error"}, "running"),
        ({"status": "stopped", "lock": "suspended"}, "stopped"),
    ],
)
def test_lxc_status_ignores_qemu_fields(guest_environment, status, expected):
    """Keep QEMU pause and hibernation metadata out of LXC normalization.

    Args:
        guest_environment: Stateful guest API factory.
        status: API response containing QEMU-only fields.
        expected: Container state derived solely from its LXC status.
    """
    environment = guest_environment()
    environment.container.status.current.get.side_effect = None
    environment.container.status.current.get.return_value = status
    assert environment.handler.power.get_powerstate() == expected


def test_power_rejects_unknown_guest_type(guest_environment, capsys):
    """Reject an unsupported API family before any endpoint construction.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for real Ansible JSON failures.
    """
    environment = guest_environment()
    with pytest.raises(SystemExit):
        ProxmoxPower(environment.module, client=environment.client, guest_type="unknown")
    result = json.loads(capsys.readouterr().out)
    assert result["failed"] is True
    assert "guest_type" in result["msg"]
    environment.api.nodes.assert_not_called()


def test_lxc_node_discovery_is_type_specific_and_cached(guest_environment):
    """Resolve the LXC owner without selecting an unrelated QEMU record.

    Args:
        guest_environment: Stateful guest API factory.
    """
    environment = guest_environment(status="running")
    environment.module.params["node"] = None
    environment.api.cluster.resources.get.return_value = [
        {"vmid": 1003, "type": "qemu", "node": "other"},
        {"vmid": "1003", "type": "lxc", "node": "pve02"},
    ]
    power = environment.handler.power
    assert power.get_powerstate() == "running"
    assert power.resolve_node() == "pve02"
    environment.api.cluster.resources.get.assert_called_once_with(type="vm")
    environment.api.nodes.assert_called_with("pve02")


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("log_available", [False, True])
def test_guest_creation_task_warning_is_returned_as_ansible_warning(guest_environment, capsys, guest_type, log_available):
    """Return a warning-only task result as Ansible success with a warning.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for the real Ansible failure output.
        guest_type: Guest API family under test.
        log_available: Whether Proxmox permits the task log to be retrieved.
    """
    environment = guest_environment(guest_type, exists=False)
    environment.api.nodes.return_value.tasks.side_effect = None
    task = environment.api.nodes.return_value.tasks.return_value
    task.status.get.return_value = {"status": "stopped", "exitstatus": "WARNINGS: 1"}
    warning = "WARN: Systemd 259 detected. You may need to enable nesting."
    lines = ["Extracting OS template", warning, "TASK WARNINGS: 1"]
    if log_available:
        task.log.get.return_value = [{"t": line} for line in lines]
    else:
        task.log.get.side_effect = RuntimeError("task log unavailable")

    result = reconcile(environment, capsys)

    assert "failed" not in result
    assert result["changed"] is True
    task.log.get.assert_called_once_with(limit=50)
    if log_available:
        assert warning[len("WARN:") :].strip() in warning_messages(result)
    else:
        assert any("WARNINGS: 1" in message for message in warning_messages(result))


@pytest.mark.parametrize("guest_type", ["qemu", "lxc"])
@pytest.mark.parametrize("log_available", [False, True])
@pytest.mark.parametrize(
    "exists,status,policy,action",
    [
        (True, "stopped", "on", "start"),
        (False, "stopped", "on", "start"),
        (True, "running", "off", "stop"),
    ],
)
def test_guest_power_task_warning_is_returned_as_ansible_warning(
    guest_environment,
    capsys,
    guest_type,
    log_available,
    exists,
    status,
    policy,
    action,
):
    """Include power-task logs for existing guests and post-creation startup.

    Args:
        guest_environment: Stateful guest API factory.
        capsys: Capture for the real Ansible failure output.
        guest_type: Guest API family under test.
        log_available: Whether Proxmox permits task-log retrieval.
        exists: Whether the guest exists before reconciliation.
        status: Initial power status.
        policy: Requested power-cycle policy.
        action: Power operation that completes with a warning.
    """
    environment = guest_environment(guest_type, exists=exists, status=status, power_cycle=policy)
    successful_task = Mock()
    successful_task.status.get.return_value = {"status": "stopped", "exitstatus": "OK"}
    warning_task = Mock()
    warning_task.status.get.return_value = {"status": "stopped", "exitstatus": "WARNINGS: 1"}
    warning = "WARN: Power operation completed with a warning"
    lines = ["Processing power operation", warning, "TASK WARNINGS: 1"]
    if log_available:
        warning_task.log.get.return_value = [{"t": line} for line in lines]
    else:
        warning_task.log.get.side_effect = RuntimeError("task log unavailable")
    environment.api.nodes.return_value.tasks.side_effect = lambda upid: successful_task if upid == "UPID:pve01:create" else warning_task

    result = reconcile(environment, capsys)

    assert "failed" not in result
    assert result["changed"] is True
    getattr(environment.container.status, action).post.assert_called_once_with()
    warning_task.log.get.assert_called_once_with(limit=50)
    successful_task.log.get.assert_not_called()
    if log_available:
        assert warning[len("WARN:") :].strip() in warning_messages(result)
    else:
        assert any("WARNINGS: 1" in message for message in warning_messages(result))
