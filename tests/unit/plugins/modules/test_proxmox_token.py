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


def test_nonempty_comment_cannot_be_silently_cleared(token_environment):
    module, client, api = token_environment(existing=True, comment="")
    handler = ProxmoxTokenModule(module, client)
    with pytest.raises(module.Fail, match="Clearing a token comment"):
        handler.execute()
    api.put.assert_not_called()


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
