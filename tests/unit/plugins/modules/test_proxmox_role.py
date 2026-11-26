# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_role, proxmox_role_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_role import ProxmoxRoleModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_role_info import ProxmoxRoleInfoModule


@pytest.fixture
def role_environment(identity_environment):
    """Reuse the real client/error fixture with an isolated role API boundary."""
    def create(check_mode=False, existing=False, **parameters):
        module, client, api = identity_environment(check_mode=check_mode, roleid="ApplicationReader", privileges=None, **parameters)
        if existing:
            api.roles["ApplicationReader"] = {"VM.Audit"}
        api.records = api.roles
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("privileges", [None, [], ["VM.Audit", "Sys.Audit", "VM.Audit"]])
def test_creation_prediction_and_independent_info(role_environment, check_mode, privileges):
    module, client, api = role_environment(check_mode=check_mode)
    module.params["privileges"] = privileges
    result = run(module, ProxmoxRoleModule(module, client))
    expected = {"roleid": "ApplicationReader", "privileges": sorted(set(privileges or [])), "special": False}
    assert result == {"changed": True, "role": expected}
    assert client._changed is (not check_mode)
    if check_mode:
        api.post.assert_not_called()
        assert "ApplicationReader" not in api.records
    else:
        api.post.assert_called_once_with("access/roles", roleid="ApplicationReader", privs=",".join(expected["privileges"]))
        assert run(module, ProxmoxRoleInfoModule(module, client)) == {"changed": False, "roles": [expected]}
        assert run(module, ProxmoxRoleModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("privileges,changed", [(None, False), (["VM.Audit", "VM.Audit"], False), ([], True), (["Sys.Audit"], True)])
def test_set_reconciliation_omission_and_explicit_empty(role_environment, check_mode, privileges, changed):
    module, client, api = role_environment(check_mode=check_mode, existing=True)
    before = copy.deepcopy(api.roles)
    module.params["privileges"] = privileges
    result = run(module, ProxmoxRoleModule(module, client))
    assert result["changed"] is changed
    assert result["role"]["privileges"] == (["VM.Audit"] if privileges is None else sorted(set(privileges)))
    if check_mode or not changed:
        api.put.assert_not_called()
        assert api.roles == before
    else:
        api.put.assert_called_once()
        assert run(module, ProxmoxRoleModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_delete_and_delete_noop(role_environment, check_mode, existing):
    module, client, api = role_environment(check_mode=check_mode, existing=existing, state="absent")
    before = copy.deepcopy(api.roles)
    result = run(module, ProxmoxRoleModule(module, client))
    assert result == {"changed": existing, "role": None}
    if check_mode or not existing:
        api.delete.assert_not_called()
        assert api.roles == before
    else:
        api.delete.assert_called_once_with("access/roles/ApplicationReader")
        assert run(module, ProxmoxRoleInfoModule(module, client)) == {"changed": False, "roles": []}


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("state,privileges", [("absent", None), ("present", [])])
def test_builtins_cannot_be_changed_or_deleted(role_environment, check_mode, state, privileges):
    module, client, api = role_environment(check_mode=check_mode, state=state)
    module.params.update(roleid="Administrator", privileges=privileges)
    handler = ProxmoxRoleModule(module, client)
    with pytest.raises(module.Fail, match="Built-in"):
        handler.execute()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_matching_builtin_is_unchanged_and_info_can_list_it(role_environment, check_mode):
    module, client, api = role_environment(check_mode=check_mode)
    module.params.update(roleid="Administrator", privileges=sorted(api.records["Administrator"]))
    assert run(module, ProxmoxRoleModule(module, client))["changed"] is False
    module.params["roleid"] = None
    result = run(module, ProxmoxRoleInfoModule(module, client))
    assert result["roles"][0]["special"] is True
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("options,message", [({"roleid": ""}, "roleid"), ({"roleid": "bad/name"}, "roleid"),
                                             ({"roleid": "pVeReserved"}, "must not start"),
                                             ({"privileges": ["Unknown.Privilege"]}, "Unknown privileges")])
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_requests_never_write(role_environment, options, message, check_mode):
    module, client, api = role_environment(check_mode=check_mode)
    module.params.update(options)
    handler = ProxmoxRoleModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("stage,changed", [("permission", False), ("discovery", False), ("write", False), ("readback", True)])
def test_failures_preserve_successful_writes(role_environment, stage, changed):
    module, client, api = role_environment()
    if stage == "permission":
        client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    elif stage == "discovery":
        api.get.side_effect = ResourceException(403, "Forbidden", "denied")
    elif stage == "write":
        api.post.side_effect = ResourceException(500, "failed", "rejected")
    else:
        initial = api.get.side_effect
        api.get.side_effect = lambda path: initial(path) if not client._changed else client.fail_request("readback failed")
    handler = ProxmoxRoleModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is changed
    if stage == "permission":
        api.get.assert_not_called()


def test_failed_delete_verification_is_changed_failure(role_environment):
    module, client, api = role_environment(existing=True, state="absent")
    api.delete.side_effect = None
    handler = ProxmoxRoleModule(module, client)
    with pytest.raises(module.Fail, match="deletion could not be verified") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True


@pytest.mark.parametrize("options", [{}, {"roleid": "ApplicationReader", "state": "invalid"}])
def test_real_argument_validation(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_role.main()
    fail.assert_called_once()


@pytest.mark.parametrize("implementation", [proxmox_role, proxmox_role_info])
def test_main_uses_correct_shared_builders(monkeypatch, implementation, role_environment):
    module, _client, _api = role_environment()
    factory = Mock(return_value=module)
    handler = getattr(implementation, "ProxmoxRoleInfoModule" if implementation is proxmox_role_info else "ProxmoxRoleModule")
    execute = Mock()
    monkeypatch.setattr(implementation, "AnsibleModule", factory)
    monkeypatch.setattr(handler, "execute", execute)
    implementation.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert ("state" in arguments["argument_spec"]) is (implementation is proxmox_role)
    assert ("task_timeout" in arguments["argument_spec"]) is (implementation is proxmox_role)
    execute.assert_called_once()


def test_update_requires_requested_privileges_in_readback(role_environment):
    module, client, api = role_environment(existing=True)
    module.params["privileges"] = ["Sys.Audit"]
    api.put.side_effect = None
    handler = ProxmoxRoleModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "Role privilege readback does not match" in failure.value.args[0]["msg"]


@pytest.mark.parametrize("roleid", [".", ".."])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("handler_class,state", [(ProxmoxRoleModule, "present"), (ProxmoxRoleModule, "absent"),
                                                 (ProxmoxRoleInfoModule, "present")])
def test_reserved_role_segments_fail_before_any_api_call(role_environment, roleid, check_mode, handler_class, state):
    module, client, api = role_environment(check_mode=check_mode, state=state)
    module.params["roleid"] = roleid
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="roleid cannot be"):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.get.assert_not_called()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("roleid", ["...", ".Application", "Application..", "Application.Reader"])
def test_other_dotted_roles_keep_literal_identity_through_lifecycle(role_environment, roleid):
    module, client, api = role_environment()
    module.params.update(roleid=roleid, privileges=["VM.Audit"])
    created = run(module, ProxmoxRoleModule(module, client))
    assert created == {"changed": True, "role": {"roleid": roleid, "privileges": ["VM.Audit"], "special": False}}
    assert run(module, ProxmoxRoleInfoModule(module, client))["roles"] == [created["role"]]
    assert run(module, ProxmoxRoleModule(module, client))["changed"] is False
    module.params["privileges"] = ["Sys.Audit"]
    updated = run(module, ProxmoxRoleModule(module, client))
    assert updated["changed"] is True
    assert updated["role"]["privileges"] == ["Sys.Audit"]
    assert run(module, ProxmoxRoleInfoModule(module, client))["roles"] == [updated["role"]]
    module.params["state"] = "absent"
    assert run(module, ProxmoxRoleModule(module, client)) == {"changed": True, "role": None}
    assert run(module, ProxmoxRoleInfoModule(module, client))["roles"] == []
    api.delete.assert_called_once_with("access/roles/" + roleid)


@pytest.mark.parametrize("check_mode", [False, True])
def test_info_selected_list_and_missing_reads_preserve_roles(role_environment, check_mode):
    module, client, api = role_environment(existing=True, check_mode=check_mode)
    before = copy.deepcopy(api.roles)
    selected = run(module, ProxmoxRoleInfoModule(module, client))
    assert selected == {"changed": False, "roles": [{"roleid": "ApplicationReader", "privileges": ["VM.Audit"], "special": False}]}
    module.params["roleid"] = None
    listed = run(module, ProxmoxRoleInfoModule(module, client))
    assert listed["changed"] is False
    assert {entry["roleid"] for entry in listed["roles"]} == {"Administrator", "ApplicationReader"}
    assert next(entry for entry in listed["roles"] if entry["roleid"] == "ApplicationReader") == selected["roles"][0]
    assert next(entry for entry in listed["roles"] if entry["roleid"] == "Administrator")["special"] is True
    module.params["roleid"] = "MissingRole"
    assert run(module, ProxmoxRoleInfoModule(module, client)) == {"changed": False, "roles": []}
    assert api.roles == before
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()
