# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from urllib.parse import quote
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_tfa
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_tfa import ProxmoxTfaModule


@pytest.fixture
def tfa_admin_environment(pool_environment):
    def create(check_mode=False, locked=True, **options):
        params = dict(state="configured", userid=None, api_user="controller@pve", allow_protected_user=False,
                      u2f=None, webauthn=None, remove_properties=[], realm=None, realm_tfa=None, digest=None, allow_protected_realm=False)
        params.update(options)
        if params["state"] in ("present", "absent", "unlocked") and "userid" not in options:
            params["userid"] = "application@pve"
        module, client, api = pool_environment(check_mode=check_mode, **params)
        state = {"options": {"webauthn": {"rp": "Example", "id": "example.com"}, "console": "html5"}, "locked": locked}
        state["realms"] = {"contractors": {"type": "ldap", "digest": "digest-before", "tfa": {"type": "oath", "digits": 6, "step": 30}}}

        def get(path, **_kwargs):
            if path == "cluster/options":
                return copy.deepcopy(state["options"])
            if path == "access/users":
                return [{"userid": module.params["userid"]}]
            if path == "access/domains":
                return [{"realm": realm} for realm in state["realms"]]
            if path.startswith("access/domains/"):
                return copy.deepcopy(state["realms"][path.rsplit("/", 1)[1]])
            if path == "access/tfa":
                return [{"userid": module.params["userid"], "entries": [], "totp-locked": int(state["locked"]),
                         "tfa-locked-until": 123 if state["locked"] else None}]
            raise AssertionError("Unexpected API read")

        def put(path, **values):
            if path.startswith("access/domains/"):
                realm = state["realms"][path.rsplit("/", 1)[1]]
                assert values.pop("digest") == realm["digest"]
                if values.pop("delete", "") == "tfa":
                    realm.pop("tfa", None)
                if "tfa" in values:
                    realm["tfa"] = dict(field.split("=", 1) for field in values["tfa"].split(","))
                realm["digest"] = "digest-after"
                return None
            if path.endswith("/unlock-tfa"):
                before = state["locked"]
                state["locked"] = False
                return before
            assert path == "cluster/options"
            for key in values.pop("delete", "").split(","):
                state["options"].pop(key, None)
            for key, value in values.items():
                state["options"][key] = dict(field.split("=", 1) for field in value.split(","))
            return None

        api.get = Mock(side_effect=get)
        api.put = Mock(side_effect=put)
        return module, client, api, state
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("desired", [
    {"webauthn": {"rp": "Updated", "allow_subdomains": False}},
    {"u2f": {"appid": "https://example.com", "origin": "https://pve.example.com"}},
])
def test_cluster_config_reconciliation_preserves_omitted_fields(tfa_admin_environment, check_mode, desired):
    module, client, api, state = tfa_admin_environment(check_mode=check_mode, **desired)
    original = copy.deepcopy(state)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert "console" not in result["configuration"]
    assert result["configuration"]["webauthn"]["id"] == "example.com"
    if "webauthn" in desired:
        assert result["configuration"]["webauthn"]["allow-subdomains"] is False
    if check_mode:
        assert state == original
        api.put.assert_not_called()
    else:
        assert state["options"]["console"] == "html5"
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"remove_properties": ["webauthn"]}, {"webauthn": {}}, {"webauthn": {"rp": None}}])
def test_cluster_property_removal_and_repeated_noop(tfa_admin_environment, check_mode, options):
    module, client, api, state = tfa_admin_environment(check_mode=check_mode, **options)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result == {"changed": True, "configuration": {}}
    if check_mode:
        assert "webauthn" in state["options"]
        api.put.assert_not_called()
    else:
        assert api.put.call_args.kwargs == {"delete": "webauthn"}
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


@pytest.mark.parametrize("options", [{}, {"webauthn": {"allow_subdomains": True}}, {"remove_properties": ["u2f"]}])
def test_cluster_omission_and_native_boolean_default_are_noops(tfa_admin_environment, options):
    module, client, api, _state = tfa_admin_environment(**options)
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is False
    api.put.assert_not_called()


def test_cluster_legacy_property_string_is_normalized(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(webauthn={"allow_subdomains": False})
    state["options"]["webauthn"] = "rp=Example,id=example.com,allow-subdomains=0"
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is False
    assert result["configuration"]["webauthn"]["allow-subdomains"] is False
    api.put.assert_not_called()


def test_cluster_conflicting_removal_fails_before_writing(tfa_admin_environment):
    module, client, api, _state = tfa_admin_environment(webauthn={"rp": "Updated"}, remove_properties=["webauthn"])
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="update and remove"):
        handler.execute()
    api.put.assert_not_called()


@pytest.mark.parametrize("failure,changed", [("rejected", False), ("read_error", True), ("wrong_readback", True)])
def test_cluster_write_failures_preserve_completed_mutations(tfa_admin_environment, failure, changed):
    module, client, api, _state = tfa_admin_environment(webauthn={"rp": "Updated"})
    if failure == "rejected":
        api.put.side_effect = RuntimeError("request rejected")
    elif failure == "wrong_readback":
        api.put.side_effect = None
    else:
        original = api.get.side_effect

        def read(path, **kwargs):
            if client._changed:
                raise RuntimeError("readback failed")
            return original(path, **kwargs)

        api.get.side_effect = read
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is changed


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("locked", [False, True])
def test_unlock_uses_native_changed_result_and_is_idempotent(tfa_admin_environment, check_mode, locked):
    module, client, api, state = tfa_admin_environment(state="unlocked", check_mode=check_mode, locked=locked)
    result = run(module, ProxmoxTfaModule(module, client))
    if check_mode:
        assert result == {"changed": True, "tfa": {"userid": "application@pve"}}
        assert state["locked"] is locked
        api.put.assert_not_called()
    else:
        assert result == {"changed": locked, "tfa": {"userid": "application@pve", "was_locked": locked}}
        assert state["locked"] is False
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False
        assert api.put.call_args.args == ("access/users/application%40pve/unlock-tfa",)
        assert api.put.call_args.kwargs == {}


@pytest.mark.parametrize("options", [{"userid": "invalid"}, {"userid": "root@pam"}, {"userid": "controller@pve"}])
def test_unlock_identity_guards_precede_api_calls(tfa_admin_environment, options):
    module, client, api, _state = tfa_admin_environment(state="unlocked", **options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    api.put.assert_not_called()


def test_unlock_missing_user_is_rejected(tfa_admin_environment):
    module, client, api, _state = tfa_admin_environment(state="unlocked")
    api.get.return_value = []
    api.get.side_effect = None
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="does not exist"):
        handler.execute()
    api.put.assert_not_called()


@pytest.mark.parametrize("response", [None, "true", 2])
def test_unlock_invalid_response_preserves_completed_request(tfa_admin_environment, response):
    module, client, api, _state = tfa_admin_environment(state="unlocked")
    api.put.side_effect = None
    api.put.return_value = response
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="invalid result") as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


def test_unlock_failed_verification_preserves_changed(tfa_admin_environment):
    module, client, api, _state = tfa_admin_environment(state="unlocked")
    api.put.side_effect = None
    api.put.return_value = True
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="did not clear") as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


@pytest.mark.parametrize("state", ["configured", "unlocked"])
def test_administration_real_argument_builders(monkeypatch, state):
    arguments = {"api_host": "pve.example.com", "api_token_id": "controller@pve!automation",
                 "api_token_secret": "EXAMPLE-token", "state": state}
    if state == "configured":
        arguments["webauthn"] = {"rp": "Example", "allow_subdomains": False}
    else:
        arguments["userid"] = "application@pve"
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    captured = []

    class Handler:
        def __init__(self, module):
            captured.append(module)

        def execute(self):
            return None

    monkeypatch.setattr(proxmox_tfa, "ProxmoxTfaModule", Handler)
    proxmox_tfa.main()
    module = captured[0]
    assert module.supports_check_mode is True
    assert module.params["state"] == state
    assert module.argument_spec["api_password"]["no_log"] is True
    assert module.argument_spec["realm_tfa"]["options"]["key"]["no_log"] is True
    assert "task_timeout" in module.argument_spec
    if state == "configured":
        assert module.params["webauthn"]["allow_subdomains"] is False
        assert module.params["userid"] is None


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("desired", [{"type": "yubico", "id": "123", "key": "EXAMPLE-api-key", "url": "https://example.invalid/verify"},
                                     {"digits": 8, "step": 60}])
def test_realm_provider_configuration_and_secret_redaction(tfa_admin_environment, check_mode, desired):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa=desired, check_mode=check_mode)
    before = copy.deepcopy(state)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert result["configuration"]["realm"] == "contractors"
    assert "key" not in result["configuration"]["tfa"]
    assert "EXAMPLE-api-key" not in str(result)
    if check_mode:
        assert state == before
        api.put.assert_not_called()
        assert "digest" not in result["configuration"]
    else:
        assert api.put.call_args.kwargs["digest"] == "digest-before"
        assert state["realms"]["contractors"]["type"] == "ldap"
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


@pytest.mark.parametrize("options", [{"realm_tfa": {}}, {"remove_properties": ["tfa"]}])
@pytest.mark.parametrize("check_mode", [False, True])
def test_realm_tfa_removal_and_omitted_fields(tfa_admin_environment, options, check_mode):
    module, client, api, state = tfa_admin_environment(realm="contractors", check_mode=check_mode, **options)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert "tfa" not in result["configuration"]
    if check_mode:
        api.put.assert_not_called()
    else:
        assert "contractors" in state["realms"]
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


def test_realm_existing_secret_preserved_but_never_returned(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa={"url": "https://example.invalid/new"})
    state["realms"]["contractors"]["tfa"] = {"type": "yubico", "key": "EXAMPLE-existing-key", "id": "123"}
    result = run(module, ProxmoxTfaModule(module, client))
    assert "EXAMPLE-existing-key" in api.put.call_args.kwargs["tfa"]
    assert "EXAMPLE-existing-key" not in str(result)


@pytest.mark.parametrize("options,message", [
    ({"realm_tfa": {"type": "oath"}}, "require realm"),
    ({"digest": "stale"}, "require realm"),
    ({"remove_properties": ["tfa"]}, "require realm"),
    ({"realm": "bad/name"}, "realm identifier"),
    ({"realm": ""}, "realm identifier"),
    ({"realm": "contractors", "u2f": {}}, "cannot be combined"),
    ({"realm": "pve", "realm_tfa": {}}, "allow_protected_realm"),
    ({"realm": "contractors", "realm_tfa": {"digits": 9}}, "digits must"),
    ({"realm": "contractors", "realm_tfa": {"step": 5}}, "step must"),
    ({"realm": "contractors", "realm_tfa": {"type": "oath"}, "remove_properties": ["tfa"]}, "update and remove"),
    ({"realm": "contractors", "realm_tfa": {"step": 60}, "digest": "stale"}, "digest is stale"),
])
def test_realm_validation_happens_before_any_write(tfa_admin_environment, options, message):
    module, client, api, _state = tfa_admin_environment(**options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.put.assert_not_called()


def test_realm_missing_after_write_preserves_changed(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa={"step": 60})
    api.put.side_effect = lambda *_args, **_kwargs: state["realms"].clear()
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="realm does not exist") as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


def test_new_realm_tfa_requires_provider_type(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa={"step": 60})
    del state["realms"]["contractors"]["tfa"]
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="provider type is required"):
        handler.execute()
    api.put.assert_not_called()


def test_preserved_yubico_key_is_masked_in_native_validation_errors(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa={"url": "https://example.invalid/new"})
    secret = "EXAMPLE/key+with=padding"
    module.no_log_values = {"EXAMPLE-existing-auth-password"}
    state["realms"]["contractors"]["tfa"] = {"type": "yubico", "key": secret}
    api.put.side_effect = ResourceException(400, "Bad Request", "validation failed", errors={"tfa": "invalid property"})
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    scrubbed = basic.remove_values(result.value.args[0], module.no_log_values)
    assert secret not in str(scrubbed)
    assert quote(secret, safe="") in module.no_log_values
    assert "EXAMPLE-existing-auth-password" in module.no_log_values


def test_realm_tfa_missing_after_successful_write_retains_changed(tfa_admin_environment):
    module, client, api, state = tfa_admin_environment(realm="contractors", realm_tfa={"step": 60})

    def write(_path, **_values):
        state["realms"]["contractors"].pop("tfa")
        state["realms"]["contractors"]["digest"] = "digest-after"

    api.put.side_effect = write
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [
    {"u2f": {"origin": "https://example.invalid,appid=https://unexpected.invalid"}},
    {"webauthn": {"rp": "Example\nUnexpected"}},
    {"realm": "contractors", "realm_tfa": {"url": "https://example.invalid,id=unexpected"}},
    {"realm": "contractors", "realm_tfa": {"key": "EXAMPLE,step=90"}},
])
def test_property_delimiters_are_rejected_before_api_access(tfa_admin_environment, check_mode, options):
    module, client, api, _state = tfa_admin_environment(check_mode=check_mode, **options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="cannot contain commas or line breaks"):
        handler.execute()
    api.get.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("state", ["configured", "unlocked"])
def test_consolidated_administration_keeps_token_authentication(tfa_admin_environment, check_mode, state):
    module, client, api, _data = tfa_admin_environment(
        state=state, check_mode=check_mode, api_password=None,
        api_token_id="controller@pve!automation", api_token_secret="EXAMPLE-token",
    )
    result = run(module, ProxmoxTfaModule(module, client))
    assert ("configuration" if state == "configured" else "tfa") in result
    assert "factor" not in result
    if check_mode:
        api.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("state", ["present", "absent"])
def test_consolidated_factor_operations_still_reject_token_authentication(tfa_admin_environment, check_mode, state):
    module, client, api, _data = tfa_admin_environment(
        state=state, check_mode=check_mode, api_password=None,
        api_token_id="controller@pve!automation", api_token_secret="EXAMPLE-token",
    )
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="requires password authentication"):
        handler.execute()
    api.get.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [
    {"state": "configured", "userid": "application@pve"},
    {"state": "configured", "factor_id": "factor-1"},
    {"state": "configured", "reauth_password": "EXAMPLE-confirm"},
    {"state": "configured", "allow_protected_user": True},
    {"state": "unlocked", "enable": False},
    {"state": "unlocked", "realm_tfa": {}},
    {"state": "unlocked", "reauth_password": "EXAMPLE-confirm"},
    {"state": "unlocked", "allow_protected_realm": True},
    {"state": "present", "webauthn": {}},
    {"state": "absent", "realm": "contractors"},
])
def test_consolidated_operations_reject_mixed_options_before_api_access(tfa_admin_environment, check_mode, options):
    module, client, api, _data = tfa_admin_environment(check_mode=check_mode, **options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("state", ["present", "absent", "unlocked"])
def test_real_consolidated_arguments_require_user_only_for_user_operations(monkeypatch, state):
    monkeypatch.setattr(basic, "_load_params", lambda: {"api_host": "pve.example.com", "state": state})
    failure = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", failure)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_tfa.main()
    assert "userid" in failure.call_args.kwargs["msg"]
