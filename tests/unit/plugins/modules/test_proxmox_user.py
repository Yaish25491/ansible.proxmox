# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException
from requests import Response

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_user, proxmox_user_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_user import ProxmoxUserModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_user_info import ProxmoxUserInfoModule


@pytest.fixture
def user_environment(identity_environment):
    def create(check_mode=False, existing=False, **options):
        parameters = dict(userid="application@pve", groups=None, enable=None, expire=None, password=None,
                          email=None, firstname=None, lastname=None, comment=None, api_user="controller@pve")
        parameters.update(options)
        module, client, api = identity_environment(check_mode=check_mode, **parameters)
        api.groups.update(application={}, other={})
        if existing:
            api.users["application@pve"] = {"enable": 1, "expire": 12345, "groups": ["other"], "comment": "original"}
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.fixture
def user_http_environment(user_environment):
    """Exercise actual URL preparation without sending requests to a server."""
    def create(userid, **options):
        module, client, api = user_environment(userid=userid, **options)
        https_api = ProxmoxAPI("pve.example.com", user="controller@pve", token_name="automation", token_value="EXAMPLE-only")

        def respond(request, **_kwargs):
            url = urlsplit(request.url)
            # IdentityApi decodes URL segments once; form/query values decode separately.
            path = url.path.removeprefix("/api2/json/")
            parameters = {key: values[0] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
            parameters.update({key: values[0] for key, values in parse_qs(request.body or "", keep_blank_values=True).items()})
            data = getattr(api, request.method.lower())(path, **parameters)
            response = Response()
            response.status_code = 200
            response._content = json.dumps({"data": data}).encode("utf-8")
            response.headers["Content-Type"] = "application/json"
            response.request = request
            return response

        session = https_api._store["session"]
        session.trust_env = False
        session.send = Mock(side_effect=respond)
        client._api = https_api
        return module, client, api, session.send
    return create


@pytest.mark.parametrize("userid,encoded", [
    ("ops%41@pve", "ops%2541%40pve"),
    ("ops#blue@pve", "ops%23blue%40pve"),
    ("ops?blue@pve", "ops%3Fblue%40pve"),
    ("ops%2Fblue@pve", "ops%252Fblue%40pve"),
])
def test_http_lifecycle_preserves_literal_user_identity(user_http_environment, userid, encoded):
    module, client, api, send = user_http_environment(userid, enable=False, comment="created")
    sibling = {"enable": 1, "groups": ["other"], "comment": "untouched sibling"}
    api.users["opsA@pve"] = copy.deepcopy(sibling)

    created = run(module, ProxmoxUserModule(module, client))
    assert created["changed"] is True
    assert created["user"]["userid"] == userid
    assert created["user"]["groups"] == []
    assert api.users["opsA@pve"] == sibling
    assert run(module, ProxmoxUserInfoModule(module, client))["users"] == [created["user"]]
    assert run(module, ProxmoxUserModule(module, client))["changed"] is False

    module.params["comment"] = "updated"
    updated = run(module, ProxmoxUserModule(module, client))
    assert updated["changed"] is True
    assert api.users[userid]["comment"] == "updated"
    assert api.users["opsA@pve"] == sibling
    assert run(module, ProxmoxUserInfoModule(module, client))["users"] == [updated["user"]]

    module.params["state"] = "absent"
    assert run(module, ProxmoxUserModule(module, client)) == {"changed": True, "user": None}
    assert userid not in api.users
    assert api.users["opsA@pve"] == sibling
    assert run(module, ProxmoxUserInfoModule(module, client))["users"] == []

    requests = [call.args[0] for call in send.call_args_list]
    selected = [request for request in requests if urlsplit(request.url).path.startswith("/api2/json/access/users/")]
    assert selected
    assert {request.method for request in selected} == {"GET", "PUT", "DELETE"}
    for request in selected:
        assert urlsplit(request.url).path == "/api2/json/access/users/" + encoded
        assert urlsplit(request.url).query == ""
        assert urlsplit(request.url).fragment == ""
    post = next(request for request in requests if request.method == "POST")
    assert parse_qs(post.body)["userid"] == [userid]


@pytest.mark.parametrize("userid", ["ops%41@pve", "ops#blue@pve", "ops?blue@pve", "ops%2Fblue@pve"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_http_info_lists_and_reads_literal_identity_without_writes(user_http_environment, userid, check_mode):
    module, client, api, send = user_http_environment(userid, check_mode=check_mode)
    api.users.update({userid: {"enable": 0, "groups": ["application"], "comment": "literal target"},
                      "opsA@pve": {"enable": 1, "groups": ["other"], "comment": "sibling"}})
    before = copy.deepcopy(api.users)
    selected = run(module, ProxmoxUserInfoModule(module, client))
    assert selected["changed"] is False
    assert selected["users"][0]["userid"] == userid
    assert selected["users"][0]["comment"] == "literal target"

    module.params["userid"] = None
    listed = run(module, ProxmoxUserInfoModule(module, client))
    assert listed["changed"] is False
    assert {user["userid"] for user in listed["users"]} == {userid, "opsA@pve"}
    assert next(user for user in listed["users"] if user["userid"] == userid) == selected["users"][0]
    assert api.users == before
    assert all(call.args[0].method == "GET" for call in send.call_args_list)


@pytest.mark.parametrize("userid", ["ops%41@pve", "ops#blue@pve", "ops?blue@pve", "ops%2Fblue@pve"])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_http_check_mode_predictions_preserve_both_accounts(user_http_environment, userid, operation):
    module, client, api, send = user_http_environment(userid, check_mode=True, comment="predicted")
    api.users["opsA@pve"] = {"enable": 1, "groups": ["other"], "comment": "sibling"}
    if operation != "create":
        api.users[userid] = {"enable": 0, "groups": [], "comment": "original"}
    module.params["state"] = "absent" if operation == "delete" else "present"
    before = copy.deepcopy(api.users)
    predicted = run(module, ProxmoxUserModule(module, client))
    assert predicted["changed"] is True
    if operation == "delete":
        assert predicted["user"] is None
    else:
        assert predicted["user"]["userid"] == userid
        assert predicted["user"]["comment"] == "predicted"
    assert api.users == before
    assert all(call.args[0].method == "GET" for call in send.call_args_list)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{}, {"groups": []}, {"groups": ["other", "application", "other"], "enable": False, "expire": 0},
                                     {"email": "app@example.com", "firstname": "Application", "lastname": "User", "comment": "0"},
                                     {"password": "EXAMPLE-initial-password"}])
def test_create_check_mode_noop_and_independent_info(user_environment, check_mode, options):
    module, client, api = user_environment(check_mode=check_mode, **options)
    before = copy.deepcopy(api.users)
    result = run(module, ProxmoxUserModule(module, client))
    assert result["changed"] is True
    assert result["user"]["userid"] == "application@pve"
    assert result["user"]["groups"] == sorted(set(options.get("groups", [])))
    assert result["user"]["enable"] is options.get("enable", True)
    assert "password" not in result["user"]
    if check_mode:
        assert api.users == before
        api.post.assert_not_called()
        api.put.assert_not_called()
    else:
        api.post.assert_called_once()
        independent = run(module, ProxmoxUserInfoModule(module, client))
        assert independent == {"changed": False, "users": [result["user"]]}
        assert run(module, ProxmoxUserModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options,changed", [({}, False), ({"groups": ["other", "other"]}, False), ({"groups": []}, True),
                                             ({"enable": False}, True), ({"expire": 0}, True), ({"comment": ""}, True),
                                             ({"password": "EXAMPLE-existing-password"}, False)])
def test_update_omission_defaults_and_empty_values(user_environment, check_mode, options, changed):
    module, client, api = user_environment(check_mode=check_mode, existing=True, **options)
    original = copy.deepcopy(api.users)
    result = run(module, ProxmoxUserModule(module, client))
    assert result["changed"] is changed
    if check_mode:
        assert api.users == original
    if check_mode or not changed:
        api.put.assert_not_called()
    else:
        assert run(module, ProxmoxUserInfoModule(module, client))["users"] == [result["user"]]
        assert run(module, ProxmoxUserModule(module, client))["changed"] is False
    for field in ("enable", "expire", "comment"):
        if field in options:
            assert result["user"][field] == options[field]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_delete_and_absent_noop(user_environment, check_mode, existing):
    module, client, api = user_environment(check_mode=check_mode, existing=existing, state="absent")
    assert run(module, ProxmoxUserModule(module, client)) == {"changed": existing, "user": None}
    if check_mode or not existing:
        api.delete.assert_not_called()
    else:
        assert run(module, ProxmoxUserInfoModule(module, client)) == {"changed": False, "users": []}


def test_info_list_normalizes_and_does_not_expose_unrelated_authentication_fields(user_environment):
    module, client, api = user_environment(existing=True, userid=None)
    api.users["application@pve"].update(keys="EXAMPLE-TFA-ID", tokens={"private": {}}, password="EXAMPLE-never-return")
    result = run(module, ProxmoxUserInfoModule(module, client))
    assert result["changed"] is False
    assert result["users"][0]["groups"] == ["other"]
    assert "password" not in result["users"][0]
    assert "keys" not in result["users"][0]
    assert "tokens" not in result["users"][0]


@pytest.mark.parametrize("options,message", [({"userid": "bad"}, "userid"), ({"userid": "bad/name@pve"}, "userid"),
                                             ({"userid": "application@missing"}, "realm does not exist"),
                                             ({"groups": ["missing"]}, "Groups do not exist"),
                                             ({"groups": ["bad/name"]}, "valid group"), ({"expire": -1}, "expire"),
                                             ({"password": "short"}, "Initial password"), ({"firstname": "x" * 1025}, "exceeds")])
def test_preflight_rejects_invalid_dependencies_before_writes(user_environment, options, message):
    module, client, api = user_environment(**options)
    handler = ProxmoxUserModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("userid", ["root@pam", "controller@pve"])
@pytest.mark.parametrize("options", [{"state": "absent"}, {"enable": False}])
def test_protected_accounts_cannot_be_deleted_or_disabled(user_environment, userid, options):
    module, client, api = user_environment(userid=userid, **options)
    handler = ProxmoxUserModule(module, client)
    with pytest.raises(module.Fail, match="cannot be deleted or disabled"):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("stage,changed", [("permission", False), ("discovery", False), ("write", False), ("readback", True)])
def test_api_failures_retain_completed_write_status(user_environment, stage, changed):
    module, client, api = user_environment()
    if stage == "permission":
        client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    elif stage == "discovery":
        api.get.side_effect = ResourceException(403, "Forbidden", "denied")
    elif stage == "write":
        api.post.side_effect = ResourceException(500, "Error", "rejected")
    else:
        initial = api.get.side_effect
        api.get.side_effect = lambda path, **kw: initial(path, **kw) if not client._changed else client.fail_request("readback failed")
    handler = ProxmoxUserModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is changed


@pytest.mark.parametrize("options", [{}, {"userid": "application@pve", "enable": "invalid-bool"}])
def test_real_required_and_type_validation(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_user.main()
    fail.assert_called_once()


@pytest.mark.parametrize("implementation", [proxmox_user, proxmox_user_info])
def test_main_shared_auth_and_argument_contract(monkeypatch, implementation, user_environment):
    module, _client, _api = user_environment()
    factory = Mock(return_value=module)
    handler = getattr(implementation, "ProxmoxUserModule" if implementation is proxmox_user else "ProxmoxUserInfoModule")
    execute = Mock()
    monkeypatch.setattr(implementation, "AnsibleModule", factory)
    monkeypatch.setattr(handler, "execute", execute)
    implementation.main()
    spec = factory.call_args.kwargs["argument_spec"]
    assert ("state" in spec) is (implementation is proxmox_user)
    assert ("task_timeout" in spec) is (implementation is proxmox_user)
    if implementation is proxmox_user:
        assert spec["password"]["no_log"] is True
    assert factory.call_args.kwargs["supports_check_mode"] is True
    execute.assert_called_once()


def test_update_requires_requested_values_in_readback(user_environment):
    module, client, api = user_environment(existing=True, comment="requested")
    api.put.side_effect = None
    handler = ProxmoxUserModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "User readback does not match" in failure.value.args[0]["msg"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("attribute,maximum", [("email", 254), ("firstname", 1024), ("lastname", 1024), ("comment", 2048)])
@pytest.mark.parametrize("extra", [0, 1])
def test_attribute_length_boundaries_before_api_access(user_environment, check_mode, attribute, maximum, extra):
    value = "x" * (maximum + extra)
    module, client, api = user_environment(check_mode=check_mode, **{attribute: value})
    handler = ProxmoxUserModule(module, client)
    if extra:
        with pytest.raises(module.Fail, match="exceeds"):
            handler.execute()
        client.validate_pve_permissions.assert_not_called()
        api.get.assert_not_called()
        api.post.assert_not_called()
    else:
        result = run(module, handler)
        assert result["changed"] is True
        assert result["user"][attribute] == value
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("length,accepted", [(7, False), (8, True), (64, True), (65, False)])
def test_creation_password_length_boundaries(user_environment, check_mode, length, accepted):
    module, client, api = user_environment(check_mode=check_mode, password="x" * length)
    handler = ProxmoxUserModule(module, client)
    if accepted:
        result = run(module, handler)
        assert result["changed"] is True
        assert "password" not in result["user"]
        if check_mode:
            api.post.assert_not_called()
        else:
            assert api.post.call_args.kwargs["password"] == "x" * length
    else:
        with pytest.raises(module.Fail, match="Initial password"):
            handler.execute()
        api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("password", ["short", "x" * 65])
def test_existing_user_ignores_creation_password_and_realm_lookup(user_environment, check_mode, password):
    module, client, api = user_environment(check_mode=check_mode, existing=True, password=password)
    api.domains = []
    before = copy.deepcopy(api.users)
    assert run(module, ProxmoxUserModule(module, client))["changed"] is False
    assert api.users == before
    assert "access/domains" not in [call.args[0] for call in api.get.call_args_list]
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_absent_user_skips_creation_dependencies(user_environment, check_mode):
    module, client, api = user_environment(check_mode=check_mode, userid="missing@missing", state="absent",
                                           password="short", groups=["missing"])
    result = run(module, ProxmoxUserModule(module, client))
    assert result == {"changed": False, "user": None}
    api.get.assert_called_once_with("access/users")
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()
