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
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_token, proxmox_token_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_token import ProxmoxTokenModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_token_info import ProxmoxTokenInfoModule


@pytest.fixture
def token_environment(identity_environment):
    def create(check_mode=False, existing=False, owner=True, **options):
        params = dict(userid="application@pve", tokenid="automation", privsep=None, expire=None, comment=None)
        params.update(options)
        module, client, api = identity_environment(check_mode=check_mode, **params)
        if owner:
            api.users["application@pve"] = {"enable": 1, "groups": []}
        if existing:
            api.tokens["application@pve", "automation"] = {"privsep": 0, "expire": 12345, "comment": "original"}
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{}, {"privsep": False, "expire": 0}, {"privsep": True, "comment": "created"}, {"comment": ""}])
def test_creation_secret_and_prediction_contract(token_environment, check_mode, options):
    module, client, api = token_environment(check_mode=check_mode, **options)
    before = copy.deepcopy(api.tokens)
    result = run(module, ProxmoxTokenModule(module, client))
    assert result["changed"] is True
    assert result["token"]["full_tokenid"] == "application@pve!automation"
    assert result["token"]["privsep"] is options.get("privsep", True)
    if check_mode:
        assert "token_secret" not in result
        assert api.tokens == before
        api.post.assert_not_called()
        if "expire" not in options:
            assert "expire" not in result["token"]
    else:
        assert result["token_secret"] == "EXAMPLE-one-time-secret"
        info = run(module, ProxmoxTokenInfoModule(module, client))
        assert info == {"changed": False, "tokens": [result["token"]]}
        repeated = run(module, ProxmoxTokenModule(module, client))
        assert repeated["changed"] is False
        assert "token_secret" not in repeated
        assert "EXAMPLE-one-time-secret" not in repr(info)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options,changed", [({}, False), ({"privsep": False}, False), ({"privsep": True}, True),
                                             ({"expire": 0}, True), ({"comment": "updated"}, True)])
def test_metadata_reconciliation_preserves_omitted_values(token_environment, check_mode, options, changed):
    module, client, api = token_environment(check_mode=check_mode, existing=True, **options)
    before = copy.deepcopy(api.tokens)
    result = run(module, ProxmoxTokenModule(module, client))
    assert result["changed"] is changed
    assert "token_secret" not in result
    if check_mode:
        assert api.tokens == before
    if check_mode or not changed:
        api.put.assert_not_called()
    else:
        assert run(module, ProxmoxTokenModule(module, client))["changed"] is False
        for value in api.put.call_args.kwargs.values():
            assert not isinstance(value, bool)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_delete_and_absence(token_environment, check_mode, existing):
    module, client, api = token_environment(check_mode=check_mode, existing=existing, state="absent")
    assert run(module, ProxmoxTokenModule(module, client)) == {"changed": existing, "token": None}
    if check_mode or not existing:
        api.delete.assert_not_called()
    else:
        assert run(module, ProxmoxTokenInfoModule(module, client)) == {"changed": False, "tokens": []}
    assert "application@pve" in api.users


def test_missing_owner_is_absent_for_info_and_delete_but_rejected_for_creation(token_environment):
    module, client, api = token_environment(owner=False)
    assert run(module, ProxmoxTokenInfoModule(module, client)) == {"changed": False, "tokens": []}
    module.params["state"] = "absent"
    assert run(module, ProxmoxTokenModule(module, client)) == {"changed": False, "token": None}
    module.params["state"] = "present"
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match="owner.*does not exist"):
        handler.execute()
    api.post.assert_not_called()


def test_list_metadata_excludes_secret_like_extra_fields(token_environment):
    module, client, api = token_environment(existing=True, tokenid=None)
    api.tokens["application@pve", "automation"]["value"] = "EXAMPLE-must-not-expose"
    result = run(module, ProxmoxTokenInfoModule(module, client))
    assert result["tokens"][0]["tokenid"] == "automation"
    assert "value" not in result["tokens"][0]
    assert "token_secret" not in result["tokens"][0]


@pytest.mark.parametrize("options,message", [({"userid": "bad"}, "userid"), ({"tokenid": "bad/name"}, "tokenid"),
                                             ({"tokenid": "x"}, "tokenid"), ({"expire": -1}, "expire"),
                                             ({"comment": "0"}, "cannot persist")])
def test_invalid_inputs_do_not_write(token_environment, options, message):
    module, client, api = token_environment(**options)
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_comment_clearing_is_verified_without_secret(token_environment, check_mode):
    comment = ""
    module, client, api = token_environment(existing=True, check_mode=check_mode, comment=comment)
    before = copy.deepcopy(api.tokens)
    result = run(module, ProxmoxTokenModule(module, client))
    assert result["changed"] is True
    assert result["token"]["comment"] == comment
    assert result["token"]["expire"] == 12345
    assert result["token"]["privsep"] is False
    assert "token_secret" not in result
    api.post.assert_not_called()
    api.delete.assert_not_called()
    if check_mode:
        api.put.assert_not_called()
        assert api.tokens == before
    else:
        api.put.assert_called_once_with("access/users/application%40pve/token/automation", comment=comment)
        info = run(module, ProxmoxTokenInfoModule(module, client))
        assert info["tokens"] == [result["token"]]
        assert run(module, ProxmoxTokenModule(module, client))["changed"] is False


def test_legacy_server_ignored_comment_update_fails_readback_without_secret(token_environment):
    comment = ""
    module, client, api = token_environment(existing=True, comment=comment)
    api.put.side_effect = lambda *_args, **_kwargs: None
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match="server may not support") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "token_secret" not in failure.value.args[0]
    assert api.tokens["application@pve", "automation"]["comment"] == "original"
    api.post.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_comment_zero_is_rejected_before_reads_or_writes(token_environment, check_mode, existing):
    module, client, api = token_environment(comment="0", check_mode=check_mode, existing=existing)
    before = copy.deepcopy(api.tokens)
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match="cannot persist comment=0"):
        handler.execute()
    assert api.tokens == before
    api.get.assert_not_called()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("identifier", ["application@pve!automation", "automation"])
def test_authenticated_token_cannot_delete_itself(token_environment, identifier):
    module, client, api = token_environment(existing=True, state="absent", api_user="application@pve", api_token_id=identifier)
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match="cannot delete itself"):
        handler.execute()
    api.delete.assert_not_called()
    api.get.assert_not_called()


@pytest.mark.parametrize("stage,changed", [("permission", False), ("discovery", False), ("write", False), ("readback", True),
                                           ("missing_secret", True)])
def test_failures_preserve_changed_without_exposing_secret(token_environment, stage, changed):
    module, client, api = token_environment()
    if stage == "permission":
        client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    elif stage == "discovery":
        api.get.side_effect = ResourceException(403, "Forbidden", "denied")
    elif stage == "write":
        api.post.side_effect = ResourceException(500, "Error", "rejected")
    elif stage == "readback":
        original = api.get.side_effect
        api.get.side_effect = lambda path, **kw: original(path, **kw) if not client._changed else client.fail_request("readback failed")
    else:
        api.post.side_effect = lambda *_args, **_kw: {}
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    result = failure.value.args[0]
    assert result.get("changed", False) is changed
    assert "token_secret" not in result
    assert "EXAMPLE-one-time-secret" not in repr(result)


@pytest.mark.parametrize("options", [{}, {"userid": "application@pve"},
                                     {"userid": "application@pve", "tokenid": "automation", "privsep": "bad-bool"}])
def test_real_argument_validation(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_token.main()
    fail.assert_called_once()


@pytest.mark.parametrize("implementation", [proxmox_token, proxmox_token_info])
def test_main_shared_argument_contract(monkeypatch, implementation, token_environment):
    module, _client, _api = token_environment()
    factory = Mock(return_value=module)
    handler = getattr(implementation, "ProxmoxTokenModule" if implementation is proxmox_token else "ProxmoxTokenInfoModule")
    execute = Mock()
    monkeypatch.setattr(implementation, "AnsibleModule", factory)
    monkeypatch.setattr(handler, "execute", execute)
    implementation.main()
    spec = factory.call_args.kwargs["argument_spec"]
    assert ("state" in spec) is (implementation is proxmox_token)
    assert ("task_timeout" in spec) is (implementation is proxmox_token)
    assert factory.call_args.kwargs["supports_check_mode"] is True
    execute.assert_called_once()


@pytest.fixture
def token_http_environment(token_environment):
    """Exercise actual Proxmoxer and Requests URL preparation without networking."""
    def create(userid, **options):
        module, client, api = token_environment(userid=userid, **options)
        api.users[userid] = {"enable": 0, "groups": []}
        api.users["opsA@pve"] = {"enable": 0, "groups": []}
        api.tokens["opsA@pve", "automation"] = {"privsep": 1, "comment": "untouched sibling"}
        https_api = ProxmoxAPI("pve.example.com", user="controller@pve", token_name="automation", token_value="EXAMPLE-only")

        def respond(request, **_kwargs):
            url = urlsplit(request.url)
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
    ("ops!team@pve", "ops%21team%40pve"),
])
def test_http_token_lifecycle_only_changes_literal_owner(token_http_environment, userid, encoded):
    module, client, api, send = token_http_environment(userid, comment="created")
    sibling = copy.deepcopy(api.tokens["opsA@pve", "automation"])
    users = copy.deepcopy(api.users)
    created = run(module, ProxmoxTokenModule(module, client))
    assert created["token"]["userid"] == userid
    assert created["token_secret"] == "EXAMPLE-one-time-secret"
    assert api.tokens[userid, "automation"]["comment"] == "created"
    assert api.tokens["opsA@pve", "automation"] == sibling
    assert run(module, ProxmoxTokenModule(module, client))["changed"] is False
    for selected in ("automation", None):
        module.params["tokenid"] = selected
        info = run(module, ProxmoxTokenInfoModule(module, client))
        assert info == {"changed": False, "tokens": [created["token"]]}
    module.params["tokenid"] = "automation"
    for comment in ("updated", ""):
        module.params["comment"] = comment
        updated = run(module, ProxmoxTokenModule(module, client))
        assert updated["changed"] is True
        assert updated["token"]["comment"] == comment
        assert "token_secret" not in updated
        assert run(module, ProxmoxTokenInfoModule(module, client))["tokens"] == [updated["token"]]
        assert run(module, ProxmoxTokenModule(module, client))["changed"] is False
        assert api.tokens["opsA@pve", "automation"] == sibling
    module.params["comment"] = "0"
    handler = ProxmoxTokenModule(module, client)
    requests_before = len(send.call_args_list)
    with pytest.raises(module.Fail, match="cannot persist comment=0"):
        handler.execute()
    assert len(send.call_args_list) == requests_before
    assert api.tokens[userid, "automation"].get("comment", "") == ""
    assert api.tokens["opsA@pve", "automation"] == sibling
    module.params["state"] = "absent"
    assert run(module, ProxmoxTokenModule(module, client)) == {"changed": True, "token": None}
    assert run(module, ProxmoxTokenInfoModule(module, client)) == {"changed": False, "tokens": []}
    assert run(module, ProxmoxTokenModule(module, client))["changed"] is False
    assert api.tokens == {("opsA@pve", "automation"): sibling}
    assert api.users == users
    paths = [call.args[0] for call in send.call_args_list if "/access/users/" in call.args[0].url]
    assert {request.method for request in paths} == {"GET", "POST", "PUT", "DELETE"}
    for request in paths:
        url = urlsplit(request.url)
        assert url.path.startswith("/api2/json/access/users/" + encoded + "/token")
        assert url.query == ""
        assert url.fragment == ""


@pytest.mark.parametrize("userid", ["ops%41@pve", "ops#blue@pve", "ops?blue@pve", "ops%2Fblue@pve"])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_http_token_check_mode_and_info_preserve_all_accounts(token_http_environment, userid, operation):
    module, client, api, send = token_http_environment(userid, check_mode=True)
    if operation != "create":
        api.tokens[userid, "automation"] = {"comment": "original", "privsep": 1}
    if operation == "delete":
        module.params["state"] = "absent"
    else:
        module.params["comment"] = "changed"
    users, tokens = copy.deepcopy((api.users, api.tokens))
    assert run(module, ProxmoxTokenModule(module, client))["changed"] is True
    for selected in (None, "automation", "missing"):
        module.params["tokenid"] = selected
        result = run(module, ProxmoxTokenInfoModule(module, client))
        assert result["changed"] is False
        assert "EXAMPLE-one-time-secret" not in repr(result)
    assert api.users == users
    assert api.tokens == tokens
    assert all(call.args[0].method == "GET" for call in send.call_args_list)
