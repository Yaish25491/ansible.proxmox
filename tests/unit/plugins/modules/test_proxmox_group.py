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
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_group, proxmox_group_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_group import ProxmoxGroupModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_group_info import ProxmoxGroupInfoModule


@pytest.fixture
def group_environment(identity_environment):
    def create(check_mode=False, existing=False, **options):
        params = dict(groupid="application", comment=None, members=None, membership_mode="exact")
        params.update(options)
        module, client, api = identity_environment(check_mode=check_mode, **params)
        api.groups["other"] = {}
        api.users.update({"first@pve": {"enable": 1, "groups": ["other"]}, "second@pve": {"enable": 1, "groups": ["other"]}})
        if existing:
            api.groups["application"] = {"comment": "original"}
            api.users["first@pve"]["groups"].append("application")
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("members", [None, [], ["first@pve", "second@pve", "first@pve"]])
@pytest.mark.parametrize("comment", [None, "0", "description"])
def test_create_predict_and_verify_independently(group_environment, check_mode, members, comment):
    module, client, api = group_environment(check_mode=check_mode, members=members, comment=comment)
    original = copy.deepcopy(api.users)
    result = run(module, ProxmoxGroupModule(module, client))
    expected = {"groupid": "application", "comment": comment or "", "members": sorted(set(members or []))}
    assert result == {"changed": True, "group": expected}
    if check_mode:
        api.post.assert_not_called()
        api.put.assert_not_called()
        assert api.users == original
    else:
        assert run(module, ProxmoxGroupInfoModule(module, client)) == {"changed": False, "groups": [expected]}
        assert run(module, ProxmoxGroupModule(module, client))["changed"] is False
        for userid in members or []:
            assert "other" in api.users[userid]["groups"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("members,mode,expected", [(None, "exact", ["first@pve"]), ([], "exact", []),
                                                   (["first@pve", "first@pve"], "exact", ["first@pve"]),
                                                   (["second@pve"], "present", ["first@pve", "second@pve"]),
                                                   (["first@pve"], "absent", []), ([], "present", ["first@pve"])])
def test_membership_modes_preserve_other_groups(group_environment, check_mode, members, mode, expected):
    module, client, api = group_environment(check_mode=check_mode, existing=True, members=members, membership_mode=mode)
    before = copy.deepcopy(api.users)
    result = run(module, ProxmoxGroupModule(module, client))
    assert result["group"]["members"] == expected
    assert result["changed"] is (expected != ["first@pve"])
    if check_mode:
        assert api.users == before
    else:
        assert run(module, ProxmoxGroupInfoModule(module, client))["groups"][0]["members"] == expected
        assert run(module, ProxmoxGroupModule(module, client))["changed"] is False
    assert "other" in api.users["first@pve"]["groups"]
    assert "other" in api.users["second@pve"]["groups"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_delete_does_not_delete_member_users(group_environment, check_mode, existing):
    module, client, api = group_environment(check_mode=check_mode, existing=existing, state="absent")
    assert run(module, ProxmoxGroupModule(module, client)) == {"changed": existing, "group": None}
    assert set(api.users) == {"first@pve", "second@pve"}
    if check_mode or not existing:
        api.delete.assert_not_called()
    else:
        assert run(module, ProxmoxGroupInfoModule(module, client)) == {"changed": False, "groups": []}


def test_info_list_and_comment_only_update(group_environment):
    module, client, api = group_environment(existing=True, comment="")
    result = run(module, ProxmoxGroupModule(module, client))
    assert result["group"]["comment"] == ""
    assert api.users["first@pve"]["groups"] == ["other", "application"]
    module.params["groupid"] = None
    assert len(run(module, ProxmoxGroupInfoModule(module, client))["groups"]) == 2


@pytest.mark.parametrize("options,message", [({"groupid": "bad/name"}, "groupid"), ({"groupid": ""}, "groupid"),
                                             ({"members": ["bad-user"]}, "valid user"),
                                             ({"members": ["missing@pve"], "comment": "new"}, "Users do not exist")])
def test_preflight_validates_all_members_before_any_write(group_environment, options, message):
    module, client, api = group_environment(**options)
    handler = ProxmoxGroupModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()


def test_second_member_write_failure_reports_first_completed_write(group_environment):
    module, client, api = group_environment(existing=True, members=["first@pve", "second@pve"])
    api.users["first@pve"]["groups"] = ["other"]
    original = api.put.side_effect

    def put(path, **parameters):
        if path.endswith("second%40pve"):
            raise ResourceException(500, "Error", "second write rejected")
        return original(path, **parameters)

    api.put.side_effect = put
    handler = ProxmoxGroupModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["status_code"] == 500
    assert api.users["first@pve"]["groups"] == ["application", "other"]
    assert api.users["second@pve"]["groups"] == ["other"]


@pytest.mark.parametrize("stage", ["permission", "discovery", "write", "readback"])
def test_error_reporting_does_not_hide_failures_as_absence(group_environment, stage):
    module, client, api = group_environment(comment="new")
    if stage == "permission":
        client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    elif stage == "discovery":
        api.get.side_effect = ResourceException(403, "Forbidden", "denied")
    elif stage == "write":
        api.post.side_effect = ResourceException(500, "Error", "rejected")
    else:
        original = api.get.side_effect
        api.get.side_effect = lambda path, **kw: original(path, **kw) if not client._changed else client.fail_request("readback failed")
    handler = ProxmoxGroupModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is (stage == "readback")


@pytest.mark.parametrize("options", [{}, {"groupid": "application", "membership_mode": "invalid"}])
def test_real_argument_validation(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_group.main()
    fail.assert_called_once()


@pytest.mark.parametrize("implementation", [proxmox_group, proxmox_group_info])
def test_main_reuses_shared_arguments(monkeypatch, implementation, group_environment):
    module, _client, _api = group_environment()
    factory = Mock(return_value=module)
    handler = getattr(implementation, "ProxmoxGroupModule" if implementation is proxmox_group else "ProxmoxGroupInfoModule")
    execute = Mock()
    monkeypatch.setattr(implementation, "AnsibleModule", factory)
    monkeypatch.setattr(handler, "execute", execute)
    implementation.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert ("state" in arguments["argument_spec"]) is (implementation is proxmox_group)
    execute.assert_called_once()


@pytest.fixture
def group_http_environment(group_environment):
    """Use actual Proxmoxer/Requests URL preparation with a mocked transport."""
    def create(userid, **options):
        module, client, api = group_environment(members=[userid], **options)
        api.users[userid] = {"enable": 0, "groups": ["other"]}
        api.users["opsA@pve"] = {"enable": 0, "groups": ["other"], "comment": "untouched sibling"}
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
])
def test_http_membership_updates_only_literal_user_and_preserves_other_groups(group_http_environment, userid, encoded):
    module, client, api, send = group_http_environment(userid, comment="created")
    sibling = copy.deepcopy(api.users["opsA@pve"])
    created = run(module, ProxmoxGroupModule(module, client))
    assert created == {"changed": True, "group": {"groupid": "application", "comment": "created", "members": [userid]}}
    assert api.users[userid]["groups"] == ["application", "other"]
    assert api.users["opsA@pve"] == sibling
    assert run(module, ProxmoxGroupInfoModule(module, client))["groups"] == [created["group"]]
    assert run(module, ProxmoxGroupModule(module, client))["changed"] is False

    module.params.update(members=[userid], membership_mode="absent")
    removed = run(module, ProxmoxGroupModule(module, client))
    assert removed["changed"] is True
    assert removed["group"]["members"] == []
    assert api.users[userid]["groups"] == ["other"]
    assert api.users["opsA@pve"] == sibling
    assert run(module, ProxmoxGroupInfoModule(module, client))["groups"] == [removed["group"]]
    assert run(module, ProxmoxGroupModule(module, client))["changed"] is False

    module.params["membership_mode"] = "present"
    assert run(module, ProxmoxGroupModule(module, client))["group"]["members"] == [userid]
    module.params["state"] = "absent"
    assert run(module, ProxmoxGroupModule(module, client)) == {"changed": True, "group": None}
    assert api.users[userid]["groups"] == ["other"]
    assert api.users["opsA@pve"] == sibling
    requests = [call.args[0] for call in send.call_args_list]
    updates = [request for request in requests if request.method == "PUT" and "/access/users/" in request.url]
    assert len(updates) == 3
    for request in updates:
        assert urlsplit(request.url).path == "/api2/json/access/users/" + encoded
        assert urlsplit(request.url).query == ""
        assert urlsplit(request.url).fragment == ""
    assert parse_qs(updates[0].body)["groups"] == ["application,other"]
    assert parse_qs(updates[1].body)["groups"] == ["other"]


@pytest.mark.parametrize("userid", ["ops%41@pve", "ops#blue@pve", "ops?blue@pve", "ops%2Fblue@pve"])
@pytest.mark.parametrize("operation", ["create", "add", "remove", "delete"])
def test_http_check_mode_membership_predictions_leave_users_and_groups_unchanged(group_http_environment, userid, operation):
    module, client, api, send = group_http_environment(userid, check_mode=True)
    if operation != "create":
        api.groups["application"] = {"comment": "original"}
    if operation in ("remove", "delete"):
        api.users[userid]["groups"].append("application")
    if operation == "remove":
        module.params["membership_mode"] = "absent"
    if operation == "delete":
        module.params["state"] = "absent"
    before_users, before_groups = copy.deepcopy((api.users, api.groups))
    result = run(module, ProxmoxGroupModule(module, client))
    assert result["changed"] is True
    if operation == "delete":
        assert result["group"] is None
    else:
        assert result["group"]["members"] == ([] if operation == "remove" else [userid])
    assert api.users == before_users
    assert api.groups == before_groups
    assert all(call.args[0].method == "GET" for call in send.call_args_list)


@pytest.mark.parametrize("groupid", [".", ".."])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("handler_class,state", [(ProxmoxGroupModule, "present"), (ProxmoxGroupModule, "absent"),
                                                 (ProxmoxGroupInfoModule, "present")])
def test_dot_segment_group_ids_fail_before_any_api_call(group_environment, groupid, check_mode, handler_class, state):
    module, client, api = group_environment(groupid=groupid, state=state, check_mode=check_mode)
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="groupid cannot be"):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.get.assert_not_called()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("groupid", ["...", ".application", "application..", "application.group"])
def test_other_dot_containing_group_ids_keep_literal_identity(group_environment, groupid):
    module, client, _api = group_environment(groupid=groupid)
    expected = {"groupid": groupid, "comment": "", "members": []}
    assert run(module, ProxmoxGroupModule(module, client)) == {"changed": True, "group": expected}
    assert run(module, ProxmoxGroupInfoModule(module, client)) == {"changed": False, "groups": [expected]}


@pytest.mark.parametrize("check_mode", [False, True])
def test_info_list_and_selected_reads_preserve_user_and_group_state(group_environment, check_mode):
    module, client, api = group_environment(check_mode=check_mode, existing=True)
    before_users, before_groups = copy.deepcopy((api.users, api.groups))
    selected = run(module, ProxmoxGroupInfoModule(module, client))
    assert selected == {"changed": False, "groups": [{"groupid": "application", "comment": "original", "members": ["first@pve"]}]}
    module.params["groupid"] = None
    listed = run(module, ProxmoxGroupInfoModule(module, client))
    assert listed["changed"] is False
    assert next(group for group in listed["groups"] if group["groupid"] == "application") == selected["groups"][0]
    assert {group["groupid"] for group in listed["groups"]} == {"application", "other"}
    assert api.users == before_users
    assert api.groups == before_groups
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()
