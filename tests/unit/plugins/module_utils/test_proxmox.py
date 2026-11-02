# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from inspect import isabstract
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox
from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox_task
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ExitJson(Exception):
    def __init__(self, result):
        super().__init__()
        self.result = result


class FailJson(Exception):
    def __init__(self, result):
        super().__init__()
        self.result = result


class FakeModule:
    def __init__(self, params=None, check_mode=False):
        self.params = params or {}
        self.check_mode = check_mode

    def exit_json(self, **kwargs):
        raise ExitJson(kwargs)

    def fail_json(self, **kwargs):
        raise FailJson(kwargs)


class FakeAccessEndpoint:
    def __init__(self, api):
        self.api = api
        self.permissions = FakePermissionsEndpoint(api)
        self.roles = FakeRolesEndpoint(api)


class FakePermissionsEndpoint:
    def __init__(self, api):
        self.api = api
        self.paths = []

    def get(self, path=None):
        self.paths.append(path)
        if self.api.permission_error:
            raise self.api.permission_error
        return {path: self.api.permissions} if path else self.api.permissions


class FakeRolesEndpoint:
    def __init__(self, api):
        self.api = api
        self.calls = 0

    def get(self):
        self.calls += 1
        if self.api.role_error:
            raise self.api.role_error
        return self.api.roles


class FakePveApi:
    def __init__(self, permissions=None, roles=None, permission_error=None, role_error=None):
        self.permissions = permissions or {}
        self.roles = roles or []
        self.permission_error = permission_error
        self.role_error = role_error
        self.access = FakeAccessEndpoint(self)


def connection_params(**updates):
    params = {
        "api_host": "proxmox.example.test",
        "api_user": "root@pam",
        "api_password": "password-value",
        "api_token_id": None,
        "api_token_secret": None,
        "api_port": None,
        "validate_certs": True,
        "ca_path": None,
        "api_timeout": 5,
        "api_otp": None,
    }
    params.update(updates)
    return params


def test_api_validation_error_identifies_unavailable_parameter_value():
    class ApiValidationError(Exception):
        status_code = 400
        errors = {"cpu": "invalid value"}

    def reject_parameter(**params):
        raise ApiValidationError("400 Bad Request: invalid CPU model")

    info = DummyInfo(FakeModule())

    with pytest.raises(FailJson) as failure:
        info.call_api("Updating a VM", reject_parameter, cpu="unsupported-cpu")

    assert failure.value.result == {
        "msg": "unsupported-cpu value for cpu is not available",
        "api_error": "400 Bad Request: invalid CPU model",
        "error_type": "ApiValidationError",
        "status_code": 400,
    }


@pytest.mark.parametrize(
    ("status", "errors", "parameters"),
    [
        (400, {"memory": "invalid value"}, {"cpu": "unsupported-cpu"}),
        (400, None, {"cpu": "unsupported-cpu"}),
        (400, ["cpu"], {"cpu": "unsupported-cpu"}),
        (400, {"cpu": "invalid value"}, None),
        (403, {"cpu": "permission denied"}, {"cpu": "unsupported-cpu"}),
        (404, {"cpu": "not found"}, {"cpu": "unsupported-cpu"}),
    ],
)
def test_fail_request_preserves_generic_api_errors(status, errors, parameters):
    exception = RuntimeError("server rejected request")
    exception.status_code = status
    exception.errors = errors
    client = proxmox.ProxmoxClient(FakeModule())

    with pytest.raises(FailJson) as failure:
        client.fail_request("Updating a VM", exception, parameters=parameters)

    assert failure.value.result == {
        "msg": "Updating a VM failed: server rejected request",
        "api_error": "server rejected request",
        "error_type": "RuntimeError",
        "status_code": status,
    }


def test_call_api_optional_404_returns_none():
    exception = RuntimeError("resource not found")
    exception.status_code = 404
    lookup = Mock(side_effect=exception)
    info = DummyInfo(FakeModule())

    assert info.call_api("Retrieving a VM", lookup, return_none_on_404=True) is None
    lookup.assert_called_once_with()


def test_argument_spec_merges_resource_arguments():
    spec = proxmox.proxmox_resource_argument_spec({"name": {"type": "str"}})

    assert "api_service" not in spec
    assert set(spec) == {
        "api_host", "api_user", "api_password", "api_token_id", "api_token_secret",
        "api_port", "validate_certs", "ca_path", "api_timeout", "api_otp", "state", "name",
    }
    assert spec["state"]["choices"] == ["present", "absent"]
    assert spec["name"] == {"type": "str"}


@pytest.mark.parametrize(
    ("helper", "shared_group"),
    [
        (proxmox.proxmox_required_together, ["api_token_id", "api_token_secret"]),
        (proxmox.proxmox_mutually_exclusive, ["api_password", "api_token_id"]),
    ],
)
def test_constraint_helpers_preserve_defaults_and_append_module_groups(helper, shared_group):
    assert helper() == [shared_group]
    assert helper([]) == [shared_group]
    assert helper([["first", "second"]]) == [shared_group, ["first", "second"]]


@pytest.mark.parametrize("helper", [proxmox.proxmox_required_together, proxmox.proxmox_mutually_exclusive])
def test_constraint_helpers_return_independent_groups(helper):
    module_groups = [["first", "second"]]
    expected = helper(module_groups)
    result = helper(module_groups)
    result[0].append("extra_shared")
    result[1].append("extra_module")

    assert module_groups == [["first", "second"]]
    assert helper(module_groups) == expected


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"status": 403, "status_code": 404}, 403),
        ({"status": None, "status_code": 404}, 404),
        ({"response": Mock(status_code=502)}, 502),
        ({"response": None}, None),
        ({"response": object()}, None),
        ({}, None),
    ],
)
def test_client_exception_status(attributes, expected):
    exception = RuntimeError("API failure")
    for name, value in attributes.items():
        setattr(exception, name, value)

    assert proxmox.ProxmoxClient.exception_status(exception) == expected


def test_client_is_lazy_and_cached(monkeypatch):
    calls = []

    def fake_api(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(proxmox, "ProxmoxAPI", fake_api)
    client = proxmox.ProxmoxClient(FakeModule(connection_params()))

    assert calls == []
    first = client.get_api()
    second = client.get_api()

    assert first is second
    assert len(calls) == 1
    assert calls[0][0] == ("proxmox.example.test",)
    assert calls[0][1] == {
        "timeout": 5,
        "user": "root@pam",
        "password": "password-value",
        "verify_ssl": True,
    }


def test_https_token_supports_pbs_ca_and_full_token_identifier(monkeypatch):
    calls = []

    def fake_api(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(proxmox, "ProxmoxAPI", fake_api)
    module = FakeModule(
        connection_params(
            api_user=None,
            api_password=None,
            api_token_id="backup@pbs!automation",
            api_token_secret="token-value",
            api_port=8007,
            ca_path="/etc/pki/proxmox-ca.pem",
        )
    )

    proxmox.ProxmoxClient(module, service="pbs").get_api()

    assert calls[0][1] == {
        "service": "pbs",
        "timeout": 5,
        "port": 8007,
        "user": "backup@pbs",
        "token_name": "automation",
        "token_value": "token-value",
        "verify_ssl": "/etc/pki/proxmox-ca.pem",
    }


@pytest.mark.parametrize(
    ("permissions", "should_fail"),
    [
        ({"Sys.Audit": True, "VM.Audit": True, "VM.Allocate": True}, False),
        ({"Sys.Audit": True, "VM.Audit": True}, True),
        ({"Sys.Audit": True, "VM.Audit": False, "VM.Allocate": True}, True),
    ],
)
def test_pve_role_validation_checks_all_propagated_role_privileges(permissions, should_fail):
    role_privileges = "Sys.Audit,VM.Audit,VM.Allocate"
    module = FakeModule(connection_params())
    api = FakePveApi(
        permissions=permissions,
        roles=[{"roleid": "Administrator", "privs": role_privileges}],
    )
    client = proxmox.ProxmoxClient(module)

    if should_fail:
        with pytest.raises(FailJson) as failure:
            client.validate_pve_permissions(api, "/vms")
        assert "full Administrator permissions propagated on /vms" in failure.value.result["msg"]
    else:
        client.validate_pve_permissions(api, "/vms")


@pytest.mark.parametrize("response", [{}, {"/vms": {}}])
def test_pve_role_validation_reports_no_permissions_as_authorization_failure(monkeypatch, response):
    module = FakeModule(connection_params())
    api = FakePveApi(roles=[{"roleid": "Administrator", "privs": "VM.Audit,VM.Allocate"}])
    monkeypatch.setattr(api.access.permissions, "get", lambda path: response)
    client = proxmox.ProxmoxClient(module)

    with pytest.raises(FailJson) as failure:
        client.validate_pve_permissions(api, "/vms")

    assert failure.value.result["msg"] == (
        "Authenticated identity lacks full Administrator permissions propagated on /vms; "
        "missing: VM.Allocate, VM.Audit."
    )


@pytest.mark.parametrize("response", [None, [], "invalid", {"/vms": None}, {"/vms": []}, {"/storage": {}}])
def test_pve_role_validation_reports_unexpected_permission_responses(monkeypatch, response):
    module = FakeModule(connection_params())
    api = FakePveApi(roles=[{"roleid": "Administrator", "privs": "VM.Audit"}])
    monkeypatch.setattr(api.access.permissions, "get", lambda path: response)
    client = proxmox.ProxmoxClient(module)

    with pytest.raises(FailJson) as failure:
        client.validate_pve_permissions(api, "/vms")

    assert failure.value.result["msg"] == (
        "Unexpected PVE permissions response for role Administrator on /vms."
    )


def test_pve_role_validation_caches_roles_per_client():
    module = FakeModule(connection_params())
    api = FakePveApi(
        permissions={"VM.Audit": True},
        roles=[{"roleid": "Administrator", "privs": "VM.Audit"}],
    )
    client = proxmox.ProxmoxClient(module)
    paths = ["/vms", "/sdn/zones", "/storage"]

    for path in paths:
        client.validate_pve_permissions(api, path)

    assert api.access.roles.calls == 1
    assert api.access.permissions.paths == paths

    proxmox.ProxmoxClient(module).validate_pve_permissions(api, "/vms")

    assert api.access.roles.calls == 2


def test_pve_role_validation_rechecks_permissions_with_cached_roles():
    module = FakeModule(connection_params())
    api = FakePveApi(
        permissions={"VM.Audit": True},
        roles=[{"roleid": "Administrator", "privs": "VM.Audit"}],
    )
    client = proxmox.ProxmoxClient(module)
    client.validate_pve_permissions(api, "/vms")
    api.permissions = {}

    with pytest.raises(FailJson) as failure:
        client.validate_pve_permissions(api, "/storage")

    assert "missing: VM.Audit" in failure.value.result["msg"]
    assert api.access.roles.calls == 1
    assert api.access.permissions.paths == ["/vms", "/storage"]


@pytest.mark.parametrize("endpoint_error", ["permission_error", "role_error"])
@pytest.mark.parametrize("status_code", [None, 403, 500])
def test_pve_role_validation_fails_closed_when_permissions_unavailable(endpoint_error, status_code):
    module = FakeModule(connection_params())
    exception = RuntimeError("permission endpoint unavailable")
    if status_code is not None:
        exception.status_code = status_code
    api = FakePveApi(**{endpoint_error: exception})
    client = proxmox.ProxmoxClient(module)

    with pytest.raises(FailJson) as failure:
        client.validate_pve_permissions(api, "/vms")

    assert "Unable to verify effective PVE permissions" in failure.value.result["msg"]
    assert "role Administrator on /vms" in failure.value.result["msg"]
    assert failure.value.result["msg"].endswith("permission endpoint unavailable")
    assert failure.value.result["api_error"] == "permission endpoint unavailable"
    assert failure.value.result["error_type"] == "RuntimeError"
    if status_code is None:
        assert "status_code" not in failure.value.result
    else:
        assert failure.value.result["status_code"] == status_code


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"api_token_id": "token", "api_token_secret": "secret"}, "Provide exactly one"),
        ({"api_password": None}, "Provide exactly one"),
        (
            {"api_password": None, "api_token_id": "token", "api_token_secret": None},
            "api_token_secret is required",
        ),
        (
            {"ca_path": "/etc/pki/proxmox-ca.pem", "validate_certs": False},
            "ca_path requires validate_certs=true",
        ),
    ],
)
def test_invalid_authentication_combinations_fail(updates, message):
    client = proxmox.ProxmoxClient(FakeModule(connection_params(**updates)))

    with pytest.raises(FailJson) as failure:
        client.connection_options()

    assert message in failure.value.result["msg"]


def test_client_failure_appends_api_error(monkeypatch):
    def fake_api(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(proxmox, "ProxmoxAPI", fake_api)
    client = proxmox.ProxmoxClient(FakeModule(connection_params()))

    with pytest.raises(FailJson) as failure:
        client.get_api()

    assert failure.value.result["error_type"] == "RuntimeError"
    assert failure.value.result["api_error"] == "connection refused"
    assert failure.value.result["msg"].endswith(failure.value.result["api_error"])


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"api_host": ""}, "api_host must not be empty"),
        ({"api_timeout": 0}, "api_timeout must be greater than zero"),
        ({"api_port": 0}, "api_port must be between 1 and 65535"),
        ({"api_port": 65536}, "api_port must be between 1 and 65535"),
    ],
)
def test_invalid_connection_values_fail(updates, message):
    client = proxmox.ProxmoxClient(FakeModule(connection_params(**updates)))

    with pytest.raises(FailJson) as failure:
        client.connection_options()

    assert failure.value.result["msg"] == message


class DummyResource(ProxmoxResourceModule):
    result_key = "dummy"

    def __init__(self, module, current):
        super().__init__(module)
        self.current = current
        self.operations = []

    def get_resource(self):
        return self.current

    def create_resource(self):
        self.operations.append("create")
        return {"name": self.module.params["name"], "value": self.module.params["value"]}

    def get_update_parameters(self, resource):
        if resource["value"] != self.module.params["value"]:
            return {"value": self.module.params["value"]}
        return {}

    def update_resource(self, resource, updates):
        self.operations.append("update")
        updated = dict(resource)
        updated.update(updates)
        return updated

    def delete_resource(self, resource):
        self.operations.append("delete")

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            return {"name": self.module.params["name"], "value": self.module.params["value"]}
        return super().get_check_mode_resource(operation, resource, updates)


@pytest.mark.parametrize(
    ("current", "desired", "expected"),
    [
        (4, 4, True),
        ("4", 4, True),
        ("4", 5, False),
        ("not-an-integer", 4, False),
        ("4.5", 4.5, True),
        ("not-a-float", 4.5, False),
        ("1", True, False),
        (None, 0, False),
    ],
)
def test_parameter_values_equal_handles_numeric_strings(current, desired, expected):
    """Compare values directly or by converting numeric API strings.

    Args:
        current: Existing API value to compare.
        desired: Requested value.
        expected: Whether the values should compare as equal.
    """
    assert ProxmoxResourceModule.parameter_values_equal(current, desired) is expected


def test_serialize_formatted_entries_preserves_inputs_and_applies_fixed_options():
    resource = DummyResource(FakeModule(), current=None)
    entries = [
        {"name": "net0", "model": "virtio", "firewall": True},
        {"name": "net1", "model": "e1000"},
    ]

    assert resource._serialize_formatted_entries("network", entries, "net", "model", {"firewall": False}) == {
        "net0": "virtio,firewall=0",
        "net1": "e1000,firewall=0",
    }
    assert entries == [
        {"name": "net0", "model": "virtio", "firewall": True},
        {"name": "net1", "model": "e1000"},
    ]
    assert resource._serialize_formatted_entries("network", [], "net", "model", {}) == {}


def test_serialize_formatted_entries_rejects_duplicate_names():
    resource = DummyResource(FakeModule(), current=None)
    entries = [{"name": "net0", "model": "virtio"}, {"name": "net0", "model": "e1000"}]

    with pytest.raises(FailJson) as failure:
        resource._serialize_formatted_entries("network", entries, "net", "model", {})

    assert failure.value.result["msg"] == "Duplicate formatted API parameter net0."


def test_merge_formatted_deletions_preserves_order_and_existing_updates():
    updates = {"net0": "virtio", "delete": "scsi0,net1"}
    deletions = ["net1", "net2", "net2", "net3"]

    result = ProxmoxResourceModule._merge_formatted_deletions(updates, deletions)

    assert result is updates
    assert result == {"net0": "virtio", "delete": "scsi0,net1,net2,net3"}
    assert deletions == ["net1", "net2", "net2", "net3"]


def test_merge_formatted_deletions_leaves_updates_alone_when_empty():
    updates = {"net0": "virtio", "delete": "scsi0"}

    result = ProxmoxResourceModule._merge_formatted_deletions(updates, [])

    assert result is updates
    assert result == {"net0": "virtio", "delete": "scsi0"}


@pytest.mark.parametrize(
    ("current_options", "desired_options", "expected"),
    [
        ({"model": "virtio", "macaddr": "auto"}, {"model": "virtio"}, True),
        ({"firewall": "1"}, {"firewall": True}, True),
        ({"firewall": "0"}, {"firewall": False}, True),
        ({"firewall": True}, {"firewall": "1"}, True),
        ({"firewall": False}, {"firewall": "0"}, True),
        ({"firewall": True}, {"firewall": 1}, True),
        ({"firewall": False}, {"firewall": 0}, True),
        ({"firewall": True}, {"firewall": True}, True),
        ({"firewall": False}, {"firewall": False}, True),
        ({"firewall": True}, {"firewall": False}, False),
        ({"firewall": False}, {"firewall": True}, False),
        ({"model": "e1000"}, {"model": "virtio"}, False),
        ({}, {"model": None}, False),
        ({"model": "virtio"}, {}, True),
    ],
)
def test_formatted_options_compare_only_requested_fields(current_options, desired_options, expected):
    resource = DummyResource(FakeModule(), current=None)

    assert resource._formatted_options_equal(current_options, desired_options) is expected


@pytest.mark.parametrize(
    ("state", "current", "check_mode", "changed", "operation", "value"),
    [
        ("present", None, False, True, "create", "desired"),
        ("present", {"name": "demo", "value": "old"}, False, True, "update", "desired"),
        ("present", {"name": "demo", "value": "desired"}, False, False, None, "desired"),
        ("absent", {"name": "demo", "value": "old"}, False, True, "delete", None),
        ("absent", None, False, False, None, None),
        ("present", None, True, True, None, "desired"),
        ("present", {"name": "demo", "value": "old"}, True, True, None, "desired"),
    ],
)
def test_resource_lifecycle(state, current, check_mode, changed, operation, value):
    module = FakeModule({"state": state, "name": "demo", "value": "desired"}, check_mode=check_mode)
    resource = DummyResource(module, current)

    with pytest.raises(ExitJson) as result:
        resource.execute_resource_module()

    assert result.value.result["changed"] is changed
    assert resource.operations == ([] if operation is None else [operation])
    if value is None:
        assert result.value.result["dummy"] is None
    else:
        assert result.value.result["dummy"]["value"] == value


def test_resource_lifecycle_returns_delete_result():
    module = FakeModule({"state": "absent", "name": "demo"})
    resource = DummyResource(module, {"name": "demo", "value": "old"})
    resource.delete_resource = lambda current: "UPID:pve01:task"

    with pytest.raises(ExitJson) as result:
        resource.execute_resource_module()

    assert result.value.result["dummy"] == "UPID:pve01:task"


class DummyInfo(ProxmoxInfoModule):
    result_key = "information"

    def get_info(self):
        return {"version": "1.0"}


class DummyAction(ProxmoxActionModule):
    result_key = "action"

    def __init__(self, module, needed):
        super().__init__(module)
        self.needed = needed
        self.performed = False

    def is_action_needed(self):
        return self.needed

    def perform_action(self):
        self.performed = True
        return {"status": "done"}

    def get_action_result(self):
        return {"status": "current"}


@pytest.mark.parametrize(
    ("template", "required_methods"),
    [
        (ProxmoxInfoModule, {"get_info"}),
        (ProxmoxActionModule, {"perform_action"}),
        (ProxmoxResourceModule, {"get_resource", "create_resource", "update_resource", "delete_resource"}),
    ],
)
def test_lifecycle_templates_require_abstract_hooks(template, required_methods):
    """Verify lifecycle templates require their abstract hooks.

    Args:
        template: Abstract lifecycle class to inspect.
        required_methods: Hook names subclasses must implement.
    """
    assert isabstract(template)
    assert template.__abstractmethods__ == required_methods
    module = FakeModule()

    with pytest.raises(TypeError, match="abstract"):
        template(module)

    for missing_method in required_methods:
        implementations = {
            method: lambda self, *args: None
            for method in required_methods - {missing_method}
        }
        incomplete = type("IncompleteModule", (template,), implementations)

        with pytest.raises(TypeError, match=missing_method):
            incomplete(module)


@pytest.mark.parametrize("implementation", [DummyInfo, DummyAction, DummyResource, ProxmoxTask, proxmox.ProxmoxClient])
def test_complete_implementations_are_not_abstract(implementation):
    assert not isabstract(implementation)


def test_info_lifecycle_is_read_only():
    info = DummyInfo(FakeModule())

    with pytest.raises(ExitJson) as result:
        info.execute()

    assert result.value.result == {"changed": False, "information": {"version": "1.0"}}


@pytest.mark.parametrize(
    ("needed", "check_mode", "changed", "performed"),
    [(False, False, False, False), (True, True, True, False), (True, False, True, True)],
)
def test_action_lifecycle(needed, check_mode, changed, performed):
    action = DummyAction(FakeModule(check_mode=check_mode), needed)

    with pytest.raises(ExitJson) as result:
        action.execute()

    assert result.value.result["changed"] is changed
    assert action.performed is performed


def test_task_node_parsing():
    assert ProxmoxTask.task_node("UPID:pve01:00000001:00000002:task") == "pve01"

    with pytest.raises(ValueError, match="Invalid Proxmox task UPID"):
        ProxmoxTask.task_node("not-a-upid")


class FakeTaskStatus:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def get(self):
        return self.statuses.pop(0)


class FakeTaskPath:
    def __init__(self, statuses):
        self.status = FakeTaskStatus(statuses)


class FakeTasksPath:
    def __init__(self, statuses):
        self.statuses = statuses

    def __call__(self, upid):
        return FakeTaskPath(self.statuses)


class FakeNodePath:
    def __init__(self, statuses):
        self.tasks = FakeTasksPath(statuses)


class FakeNodesPath:
    def __init__(self, statuses):
        self.statuses = statuses

    def __call__(self, node):
        return FakeNodePath(self.statuses)


class FakeTaskApi:
    def __init__(self, statuses):
        self.nodes = FakeNodesPath(statuses)


class FakeClient:
    def __init__(self, api):
        self.api = api

    def fail_request(self, operation, exception, parameters=None):
        raise AssertionError("Unexpected API failure")


class DummyTaskModule(ProxmoxTask):
    pass


def test_wait_for_task_rejects_negative_timeout():
    task = DummyTaskModule(FakeModule(), client=FakeClient(object()))

    with pytest.raises(FailJson) as failure:
        task.wait_for_task("UPID:pve01:00000001:00000002:task", timeout=-1)

    assert failure.value.result["msg"] == "timeout must be zero or greater"


def test_wait_for_task_returns_successful_status(monkeypatch):
    statuses = [
        {"status": "running"},
        {"status": "stopped", "exitstatus": "OK"},
    ]
    task = DummyTaskModule(FakeModule(), client=FakeClient(FakeTaskApi(statuses)))
    monkeypatch.setattr(proxmox_task.time, "sleep", lambda interval: None)

    result = task.wait_for_task("UPID:pve01:00000001:00000002:task", timeout=10)

    assert result["exitstatus"] == "OK"


def test_wait_for_task_zero_timeout_waits_until_task_finishes(monkeypatch):
    statuses = [
        {"status": "running"},
        {"status": "running"},
        {"status": "stopped", "exitstatus": "OK"},
    ]
    task = DummyTaskModule(FakeModule(), client=FakeClient(FakeTaskApi(statuses)))
    monkeypatch.setattr(proxmox_task.time, "monotonic", Mock(side_effect=AssertionError("deadline created")))
    monkeypatch.setattr(proxmox_task.time, "sleep", lambda interval: None)

    result = task.wait_for_task("UPID:pve01:00000001:00000002:task", timeout=0)

    assert result["exitstatus"] == "OK"


def test_wait_for_task_reports_failed_exit_status():
    statuses = [{"status": "stopped", "exitstatus": "permission denied"}]
    task = DummyTaskModule(FakeModule(), client=FakeClient(FakeTaskApi(statuses)))

    with pytest.raises(FailJson) as failure:
        task.wait_for_task("UPID:pbs01:00000001:00000002:task", timeout=10)

    assert failure.value.result["msg"] == "Proxmox task failed"
    assert failure.value.result["task_exit_status"] == "permission denied"
