# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_permission_info


@pytest.fixture
def environment(pool_environment):
    def create(check_mode=False, **options):
        module, client, _api = pool_environment(check_mode=check_mode)
        module.params = {"userid": None, "path": None}
        module.params.update(options)
        api = Mock()
        client._api = api

        def get(endpoint, **parameters):
            if endpoint == "nodes":
                return [{"node": "pve01"}]
            if endpoint == "cluster/resources":
                return [{"vmid": 100, "type": "qemu", "node": "pve01"}, {"vmid": 101, "type": "lxc", "node": "pve01"}]
            return {parameters.get("path", "/vms/100"): {"VM.Audit": 1, "VM.Console": 0}}

        api.get.side_effect = get
        return module, client, api

    return create


def execute(module, client):
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "options,path,parameters",
    [
        ({}, "access/permissions", {}),
        ({"userid": "reader@pve", "path": "/vms/100"}, "access/permissions", {"userid": "reader@pve", "path": "/vms/100"}),
        ({"userid": "reader@pve!test", "path": "/"}, "access/permissions", {"userid": "reader@pve!test", "path": "/"}),
    ],
)
def test_scoped_reads_preserve_results_without_writes(environment, check_mode, options, path, parameters):
    module, client, api = environment(check_mode=check_mode, **options)
    expected_path = parameters.get("path", "/vms/100")
    assert execute(module, client) == {"changed": False, "permissions": {expected_path: {"VM.Audit": 1, "VM.Console": 0}}}
    api.get.assert_any_call(path, **parameters)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()
    assert client._changed is False


@pytest.mark.parametrize(
    "options",
    [
        {"userid": ""},
        {"userid": "user"},
        {"userid": "user@pve!"},
        {"userid": "user@r"},
        {"userid": "user@pve!t"},
        {"userid": "user@r!automation"},
        {"userid": "user@pve!token/child"},
        {"userid": "user name@pve"},
        {"path": ""},
        {"path": "vms/100"},
        {"path": "/vms/100\n"},
        {"path": "/wrong_path"},
        {"path": "/no_path"},
        {"path": "/vms/99"},
        {"path": "/sdn/zones/zone/vnet/*"},
        {"path": "/vms/100%2F200#?literal"},
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_parameters_rejected_before_api(environment, options, check_mode):
    module, client, api = environment(check_mode=check_mode, **options)
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_server_errors_are_not_empty_results(environment, status):
    module, client, api = environment()
    api.get.side_effect = ResourceException(status, "request failed", "server detail")
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["status_code"] == status
    assert "server detail" in result.value.args[0]["api_error"]
    assert result.value.args[0].get("changed", False) is False


def test_permission_rejection_precedes_discovery(environment):
    module, client, api = environment()
    client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("records", [{}, {"/vms/100": {}}, {"/vms/100": {"VM.Audit": 1, "VM.Console": 0}}])
def test_fresh_observations_preserve_empty_results(environment, records, check_mode):
    module, client, api = environment(check_mode=check_mode)
    api.get.side_effect = [records, {"/vms/100": {"VM.Audit": 1, "VM.Console": 0}}]
    assert execute(module, client)["permissions"] == records
    assert execute(module, client)["permissions"] == {"/vms/100": {"VM.Audit": 1, "VM.Console": 0}}
    assert api.get.call_count == 2
    assert client.validate_pve_permissions.call_count == 2


@pytest.mark.parametrize(
    "options,message",
    [
        ({"state": "present"}, "Unsupported parameters"),
        ({"task_timeout": 1}, "Unsupported parameters"),
        ({"api_token_id": "user@pve!token"}, "required together"),
        ({"api_password": "EXAMPLE", "api_token_id": "user@pve!token", "api_token_secret": "EXAMPLE"}, "mutually exclusive"),
    ],
)
def test_real_argument_validation(monkeypatch, options, message):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_permission_info.main()
    assert message in fail.call_args.kwargs["msg"]


def test_main_uses_information_arguments_and_auth_groups(monkeypatch, environment):
    module, _client, _api = environment()
    factory = Mock(return_value=module)
    run = Mock()
    monkeypatch.setattr(proxmox_permission_info, "AnsibleModule", factory)
    monkeypatch.setattr(proxmox_permission_info.ProxmoxPermissionInfoModule, "execute", run)
    proxmox_permission_info.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" not in arguments["argument_spec"]
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    run.assert_called_once()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("subject", ["name!local@pve", "name@local@pve", "name@local!tag@pve!automation", "raw%41#?@pve!automation"])
def test_official_subject_grammar_and_literal_query_identifiers(environment, subject, check_mode):
    module, client, api = environment(check_mode=check_mode, userid=subject, path="/vms/100")
    result = execute(module, client)
    assert result["changed"] is False
    api.get.assert_called_once_with("access/permissions", userid=subject, path="/vms/100")


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("path", ["/access//groups/", "/sdn/zones/zone/vnet/1", "/pool/blue/app"])
def test_acl_query_path_is_not_encoded_or_normalized_by_the_module(environment, path, check_mode):
    module, client, api = environment(check_mode=check_mode, path=path)
    result = execute(module, client)
    assert result["changed"] is False
    api.get.assert_called_once_with("access/permissions", path=path)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("permissions", [
    None, [], "invalid", {"/wrong_path": {}}, {"relative": {}}, {42: {}},
    {"/vms/100": None}, {"/vms/100": []}, {"/vms/100": {"": 1}}, {"/vms/100": {"VM Audit": 1}},
    {"/vms/100": {"VM.Audit": 2}}, {"/vms/100": {"VM.Audit": -1}}, {"/vms/100": {"VM.Audit": "0"}},
    {"/vms/100": {"VM.Audit": None}}, {"/vms/100": {"VM.Audit": 0.0}}, {"/vms/100": {None: 1}},
])
def test_malformed_effective_permissions_fail_unchanged(environment, check_mode, permissions):
    module, client, api = environment(check_mode=check_mode)
    api.get.return_value = permissions
    api.get.side_effect = None
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Fail, match="invalid path, privilege mapping or propagation flag") as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is False
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_selected_query_rejects_permissions_for_another_path(environment, check_mode):
    module, client, api = environment(check_mode=check_mode, path="/vms/100")
    api.get.side_effect = None
    api.get.return_value = {"/vms/101": {"VM.Audit": 1}}
    handler = proxmox_permission_info.ProxmoxPermissionInfoModule(module, client)
    with pytest.raises(module.Fail, match="invalid path"):
        handler.execute()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("permissions", [{}, {"/vms/999999999": {}}, {"/vms/999999999": {"VM.Audit": 0, "Future.Privilege": True}}])
def test_native_empty_and_zero_propagation_results_do_not_imply_resource_existence(environment, check_mode, permissions):
    module, client, api = environment(check_mode=check_mode, path="/vms/999999999")
    api.get.side_effect = None
    api.get.return_value = permissions
    result = execute(module, client)
    assert result == {"changed": False, "permissions": permissions}
    assert result["permissions"] is permissions
    api.get.assert_called_once_with("access/permissions", path="/vms/999999999")
