# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import json
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import Mock

import pytest
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException
from requests import Response

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_tfa, proxmox_tfa_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_tfa import ProxmoxTfaModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_tfa_info import ProxmoxTfaInfoModule

TOTP_URI = "otpauth://totp/EXAMPLE?secret=JBSWY3DPEHPK3PXP&issuer=EXAMPLE"
ORIGINAL = {"id": "factor-1", "type": "totp", "description": "automation", "created": 123, "enable": 1}


class TfaApi:
    """Keep TFA and user storage separate to verify the production boundary."""

    def __init__(self):
        self.users = {"application@pve"}
        self.entries = {"application@pve": []}
        self.pending = {}
        self.options = {"webauthn": {"rp": "Example", "id": "example.com"}, "console": "html5"}
        self.get = Mock(side_effect=self._get)
        self.post = Mock(side_effect=self._post)
        self.put = Mock(side_effect=self._put)
        self.delete = Mock(side_effect=self._delete)

    def _get(self, path, **parameters):
        if path == "cluster/options":
            return copy.deepcopy(self.options)
        if path.startswith("access/domains/"):
            return {"type": "ldap", "digest": "EXAMPLE-digest", "tfa": "type=yubico,id=123,key=EXAMPLE-api-key,url=https://example.invalid"}
        if path == "access/users":
            return [{"userid": userid} for userid in self.users]
        if path.endswith("/tfa") and path.startswith("access/users/"):
            return {"realm": "oath", "user": ["totp"] if int(parameters["multiple"]) else "oath", "secret": "EXAMPLE-hidden"}
        if path == "access/tfa":
            return [{"userid": userid, "entries": copy.deepcopy(entries), "totp-locked": 0, "tfa-locked-until": 123,
                     "secret": "EXAMPLE-never-return"} for userid, entries in self.entries.items()]
        userid = unquote(path.split("/")[2])
        if len(path.split("/")) == 4:
            identifier = unquote(path.split("/")[3])
            entry = next((entry for entry in self.entries.get(userid, []) if entry["id"] == identifier), None)
            if entry is None:
                raise ResourceException(404, "Not Found", "missing factor")
            return copy.deepcopy(entry)
        return copy.deepcopy(self.entries.get(userid, []))

    def _post(self, path, **parameters):
        userid = unquote(path.split("/")[2])
        factor_type = parameters["type"]
        if factor_type in ("webauthn", "u2f"):
            selector = (userid, factor_type)
            if not parameters.get("challenge"):
                payload = ({"publicKey": {"challenge": "EXAMPLE-challenge"}} if factor_type == "webauthn" else
                           {"challenge": "EXAMPLE-challenge", "appId": "https://example.com", "version": "U2F_V2"})
                serialized = json.dumps(payload)
                self.pending[selector] = {"description": parameters["description"],
                                          "challenge": "EXAMPLE-challenge" if factor_type == "webauthn" else serialized}
                return {"challenge": serialized}
            if selector not in self.pending or parameters["challenge"] != self.pending[selector]["challenge"]:
                raise ResourceException(400, "Bad Request", "invalid or expired challenge")
            description = self.pending.pop(selector)["description"]
        else:
            description = parameters.get("description", "Recovery Keys")
        if factor_type == "recovery":
            self.entries[userid].append({"id": "recovery", "type": "recovery", "description": "Recovery Keys", "enable": 1, "created": 123})
            return {"id": "recovery", "recovery": ["EXAMPLE-recovery-one", "EXAMPLE-recovery-two"]}
        identifier = "factor-{0}".format(len(self.entries[userid]) + 1)
        self.entries[userid].append({"id": identifier, "type": factor_type, "description": description,
                                     "enable": 1, "created": 123, "secret": "EXAMPLE-never-return"})
        return {"id": identifier}

    def _put(self, path, **parameters):
        userid, identifier = [unquote(part) for part in path.split("/")[2:]]
        entry = next(entry for entry in self.entries[userid] if entry["id"] == identifier)
        entry.update({key: value for key, value in parameters.items() if key in ("description", "enable")})

    def _delete(self, path, **_parameters):
        userid, identifier = [unquote(part) for part in path.split("/")[2:]]
        self.entries[userid] = [entry for entry in self.entries[userid] if entry["id"] != identifier]


@pytest.fixture
def tfa_environment(pool_environment):
    def create(check_mode=False, existing=False, **options):
        parameters = dict(userid="application@pve", factor_id=None, type="totp", description="automation", enable=None,
                          totp_uri=TOTP_URI, value="123456", challenge=None, allow_protected_user=False,
                          reauth_password=None, api_user="controller@pve", api_password="EXAMPLE-password")
        parameters.update(options)
        module, client, _api = pool_environment(check_mode=check_mode, **parameters)
        api = TfaApi()
        if existing:
            api.entries["application@pve"] = [copy.deepcopy(ORIGINAL)]
        client._api = api
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("enable", [None, False, True])
def test_enrollment_prediction_actual_noop_independent_info(tfa_environment, check_mode, enable):
    module, client, api = tfa_environment(check_mode=check_mode, enable=enable)
    before = copy.deepcopy(api.entries)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert result["factor"]["type"] == "totp"
    assert result["factor"]["enable"] is (True if enable is None else enable)
    assert TOTP_URI not in str(result)
    assert "123456" not in str(result)
    assert "secret" not in result["factor"]
    if check_mode:
        assert "id" not in result["factor"]
        assert "created" not in result["factor"]
        assert api.entries == before
        api.post.assert_not_called()
        api.put.assert_not_called()
    else:
        assert result["factor"]["id"] == "factor-1"
        api.post.assert_called_once_with("access/tfa/application%40pve", type="totp", description="automation",
                                         totp=TOTP_URI, value="123456", password="EXAMPLE-password")
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False
        independent = run(module, ProxmoxTfaInfoModule(module, client))
        expected = dict(result["factor"])
        expected.pop("userid")
        assert independent == {"changed": False, "users": [{"userid": "application@pve", "entries": [expected]}]}


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options,changed", [({}, False), ({"enable": True}, False), ({"enable": False}, True),
                                             ({"description": ""}, True), ({"description": "renamed"}, True)])
def test_metadata_omission_empty_rename_and_noop(tfa_environment, check_mode, options, changed):
    parameters = dict(factor_id="factor-1", type=None, description=None, totp_uri=None, value=None)
    parameters.update(options)
    module, client, api = tfa_environment(existing=True, check_mode=check_mode, **parameters)
    before = copy.deepcopy(api.entries)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is changed
    assert result["factor"]["description"] == options.get("description", "automation")
    assert result["factor"]["enable"] is options.get("enable", True)
    if check_mode or not changed:
        assert api.entries == before
        api.put.assert_not_called()
    else:
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


@pytest.mark.parametrize("factor_type", ["totp", "u2f", "webauthn", "yubico"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_manage_existing_types_without_enrollment(tfa_environment, factor_type, check_mode):
    module, client, api = tfa_environment(existing=True, factor_id="factor-1", type=None, enable=False,
                                          description=None, totp_uri=None, value=None, check_mode=check_mode)
    api.entries["application@pve"][0]["type"] = factor_type
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert result["factor"]["type"] == factor_type
    assert result["factor"]["enable"] is False
    api.post.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("check_mode", [False, True])
def test_absent_noop_and_delete_verification(tfa_environment, existing, check_mode):
    module, client, api = tfa_environment(existing=existing, state="absent", factor_id="factor-1", check_mode=check_mode, api_user="root@pam")
    before = copy.deepcopy(api.entries)
    assert run(module, ProxmoxTfaModule(module, client)) == {"changed": existing, "factor": None}
    if check_mode or not existing:
        assert api.entries == before
        api.delete.assert_not_called()
    else:
        assert api.entries["application@pve"] == []


def test_existing_enrollment_ignores_creation_only_secrets(tfa_environment):
    module, client, api = tfa_environment(existing=True, totp_uri="not-a-uri", value=None)
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is False
    api.post.assert_not_called()


@pytest.mark.parametrize("options,message", [
    ({"api_token_id": "automation"}, "password authentication"),
    ({"api_token_secret": "EXAMPLE"}, "password authentication"),
    ({"api_password": None}, "password authentication"),
    ({"userid": "root@pam"}, "root or authenticated"),
    ({"userid": "controller@pve"}, "root or authenticated"),
    ({"userid": "invalid"}, "userid"),
    ({"userid": "bad/name@pve"}, "userid"),
    ({"factor_id": "."}, "factor_id"),
    ({"factor_id": ".."}, "factor_id"),
    ({"factor_id": "a/b"}, "factor_id"),
    ({"factor_id": ""}, "factor_id"),
    ({"factor_id": "a b"}, "factor_id"),
    ({"description": "x" * 256}, "description"),
    ({"description": ""}, "enrollment identity"),
    ({"type": None}, "enrollment identity"),
    ({"reauth_password": "four"}, "between 5 and 64"),
    ({"reauth_password": "x" * 65}, "between 5 and 64"),
    ({"reauth_password": ""}, "between 5 and 64"),
    ({"totp_uri": None}, "Initial TOTP"),
    ({"totp_uri": "https://example.com"}, "Initial TOTP"),
    ({"value": None}, "Initial TOTP"),
    ({"type": "webauthn"}, "totp_uri is only valid"),
    ({"type": "recovery"}, "totp_uri is only valid"),
    ({"factor_id": "missing"}, "selected factor ID does not exist"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_reject_invalid_inputs_without_mutations(tfa_environment, options, message, check_mode):
    module, client, api = tfa_environment(check_mode=check_mode, **options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


def test_duplicate_selector_fails_without_writes(tfa_environment):
    module, client, api = tfa_environment(existing=True)
    api.entries["application@pve"].append(dict(ORIGINAL, id="factor-2"))
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="Multiple TFA"):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()


def test_explicit_id_can_select_duplicate_description(tfa_environment):
    module, client, api = tfa_environment(existing=True, factor_id="factor-1", enable=False)
    api.entries["application@pve"].append(dict(ORIGINAL, id="factor-2"))
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True
    assert api.entries["application@pve"][1] == dict(ORIGINAL, id="factor-2")


def test_type_change_fails_but_absent_ignores_reconciliation(tfa_environment):
    module, client, api = tfa_environment(existing=True, factor_id="factor-1", type="webauthn")
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="type of an existing factor"):
        handler.execute()
    api.put.assert_not_called()
    module.params.update(state="absent", api_user="root@pam")
    assert run(module, handler)["changed"] is True


@pytest.mark.parametrize("state", ["present", "absent"])
def test_missing_user_preflight(tfa_environment, state):
    module, client, api = tfa_environment(state=state)
    api.users.clear()
    handler = ProxmoxTfaModule(module, client)
    if state == "present":
        with pytest.raises(module.Fail, match="target user must exist"):
            handler.execute()
    else:
        assert run(module, handler) == {"changed": False, "factor": None}
    api.post.assert_not_called()


def test_explicit_reauth_password_is_used_only_for_body_mutations(tfa_environment):
    module, client, api = tfa_environment(reauth_password="EXAMPLE-confirm", enable=False)
    run(module, ProxmoxTfaModule(module, client))
    assert api.post.call_args.kwargs["password"] == "EXAMPLE-confirm"
    assert api.put.call_args.kwargs["password"] == "EXAMPLE-confirm"
    module.params.update(state="absent", api_user="root@pam")
    run(module, ProxmoxTfaModule(module, client))
    assert "password" not in api.delete.call_args.kwargs


def test_root_operator_does_not_send_optional_confirmation(tfa_environment):
    module, client, api = tfa_environment(api_user="root@pam", api_password="EXAMPLE-long-authentication-password" * 3)
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True
    assert "password" not in api.post.call_args.kwargs
    module.params["state"] = "absent"
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True
    assert "password" not in api.delete.call_args.kwargs


@pytest.mark.parametrize("failure", ["missing_id", "missing_readback", "bad_metadata", "disable_rejected"])
def test_enrollment_followup_failure_preserves_completed_write(tfa_environment, failure):
    module, client, api = tfa_environment(enable=False)
    original_post = api._post

    def post(path, **parameters):
        result = original_post(path, **parameters)
        if failure == "missing_id":
            return {}
        if failure == "missing_readback":
            api.entries["application@pve"] = []
        if failure == "bad_metadata":
            api.entries["application@pve"][0]["description"] = "unmatched"
        return result

    api.post.side_effect = post
    if failure == "disable_rejected":
        api.put.side_effect = RuntimeError("write rejected")
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail) as failure_result:
        handler.execute()
    assert failure_result.value.args[0]["changed"] is True


@pytest.mark.parametrize("operation", ["post", "put", "delete"])
def test_rejected_first_write_reports_no_completed_mutation(tfa_environment, operation):
    module, client, api = tfa_environment(existing=operation != "post", enable=False,
                                          state="absent" if operation == "delete" else "present",
                                          api_user="root@pam" if operation == "delete" else "controller@pve")
    getattr(api, operation).side_effect = RuntimeError("server rejected request")
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is False


@pytest.mark.parametrize("operation", ["put", "delete"])
def test_mutation_that_does_not_reconcile_fails_with_changed(tfa_environment, operation):
    module, client, api = tfa_environment(existing=True, enable=False, state="absent" if operation == "delete" else "present",
                                          api_user="root@pam" if operation == "delete" else "controller@pve")
    getattr(api, operation).side_effect = None
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="could not be verified") as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


@pytest.mark.parametrize("check_mode", [False, True])
def test_info_allowlists_metadata_in_all_and_selected_reads(tfa_environment, check_mode):
    module, client, api = tfa_environment(existing=True, check_mode=check_mode, userid=None, factor_id=None,
                                          api_token_id="automation", api_password=None)
    api.entries["application@pve"][0].update(secret="EXAMPLE-secret", recovery=["EXAMPLE-recovery"], challenge="EXAMPLE-challenge")
    before = copy.deepcopy(api.entries)
    result = run(module, ProxmoxTfaInfoModule(module, client))
    assert result["changed"] is False
    assert result["users"][0]["totp_locked"] is False
    assert result["users"][0]["tfa_locked_until"] == 123
    assert result["users"][0]["entries"] == [dict(ORIGINAL, enable=True)]
    assert "secret" not in str(result)
    assert "recovery" not in str(result)
    assert "challenge" not in str(result)
    module.params.update(userid="application@pve", factor_id="factor-1")
    selected = run(module, ProxmoxTfaInfoModule(module, client))
    assert selected["users"][0]["entries"] == [dict(ORIGINAL, enable=True)]
    module.params["factor_id"] = "missing"
    assert run(module, ProxmoxTfaInfoModule(module, client)) == {"changed": False, "users": []}
    module.params.update(userid="missing@pve", factor_id=None)
    assert run(module, ProxmoxTfaInfoModule(module, client))["users"] == [{"userid": "missing@pve", "entries": []}]
    assert api.entries == before
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("options", [{"userid": "invalid"}, {"factor_id": "."}, {"factor_id": ".."}, {"factor_id": ""},
                                     {"factor_id": "bad/name"}, {"userid": None, "factor_id": "factor-1"}])
def test_info_invalid_identifiers_fail_before_api(tfa_environment, options):
    module, client, api = tfa_environment(**options)
    handler = ProxmoxTfaInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("userid,factor_id", [("ops%41@pve", "id%41"), ("ops?blue@pve", "id?blue"),
                                              ("ops#blue@pve", "id#blue"), ("ops%2Fblue@pve", "id%2Fblue")])
def test_real_http_encodes_literal_segments_once(tfa_environment, userid, factor_id):
    module, client, api = tfa_environment(userid=userid, factor_id=factor_id, description="updated", api_user="root@pam", reauth_password="EXAMPLE-confirm")
    api.users.add(userid)
    api.entries[userid] = [dict(ORIGINAL, id=factor_id)]
    https = ProxmoxAPI("pve.example.com", user="controller@pve", token_name="automation", token_value="EXAMPLE")

    def respond(request, **_kwargs):
        url = urlsplit(request.url)
        path = url.path.removeprefix("/api2/json/")
        parameters = {key: value[0] for key, value in parse_qs(url.query, keep_blank_values=True).items()}
        parameters.update({key: value[0] for key, value in parse_qs(request.body or "", keep_blank_values=True).items()})
        data = getattr(api, request.method.lower())(path, **parameters)
        response = Response()
        response.status_code = 200
        response._content = json.dumps({"data": data}).encode("utf-8")
        return response

    session = https._store["session"]
    session.trust_env = False
    session.send = Mock(side_effect=respond)
    client._api = https
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["factor"]["id"] == factor_id
    assert result["factor"]["userid"] == userid
    assert run(module, ProxmoxTfaInfoModule(module, client))["users"][0]["entries"][0]["id"] == factor_id
    module.params["state"] = "absent"
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True
    for call in session.send.call_args_list:
        url = urlsplit(call.args[0].url)
        assert url.query == ""
        assert "EXAMPLE-confirm" not in call.args[0].url
        assert url.fragment == ""
    writes = [call.args[0] for call in session.send.call_args_list if call.args[0].method in ("PUT", "DELETE")]
    assert len(writes) == 2
    for request in writes:
        segments = urlsplit(request.url).path.split("/")[-2:]
        assert unquote(segments[0]) == userid
        assert unquote(segments[1]) == factor_id


@pytest.mark.parametrize("module_code", [proxmox_tfa, proxmox_tfa_info])
def test_real_argument_validation_and_secret_metadata(monkeypatch, module_code):
    arguments = {"api_host": "pve.example.com", "api_user": "controller@pve", "api_password": "EXAMPLE-password",
                 "userid": "application@pve"}
    if module_code is proxmox_tfa:
        arguments.update(type="totp", description="automation", totp_uri=TOTP_URI, value="123456", reauth_password="EXAMPLE-confirm")
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    captured = []

    class Handler:
        def __init__(self, module):
            captured.append(module)

        def execute(self):
            return None

    monkeypatch.setattr(module_code, "ProxmoxTfaModule" if module_code is proxmox_tfa else "ProxmoxTfaInfoModule", Handler)
    module_code.main()
    assert captured[0].supports_check_mode is True
    assert captured[0].argument_spec["api_password"]["no_log"] is True
    if module_code is proxmox_tfa:
        assert captured[0].params["state"] == "present"
        for name in ("totp_uri", "value", "reauth_password"):
            assert captured[0].argument_spec[name]["no_log"] is True


@pytest.mark.parametrize("module_code,arguments,message", [
    (proxmox_tfa, {"api_host": "pve.example.com"}, "userid"),
    (proxmox_tfa, {"api_host": "pve.example.com", "userid": "application@pve", "type": "invalid"}, "value of type"),
    (proxmox_tfa_info, {"api_host": "pve.example.com", "factor_id": "factor-1"}, "userid"),
])
def test_real_argument_validation_rejects_invalid_input(monkeypatch, module_code, arguments, message):
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    failure = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", failure)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        module_code.main()
    assert message in failure.call_args.kwargs["msg"]


@pytest.mark.parametrize("check_mode", [False, True])
def test_nonroot_deletion_is_rejected_without_secret_transport(tfa_environment, check_mode):
    module, client, api = tfa_environment(existing=True, state="absent", factor_id="factor-1", check_mode=check_mode)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="deletion requires root@pam"):
        handler.execute()
    api.delete.assert_not_called()
    assert client._changed is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("factor_id", [None, "recovery"])
def test_recovery_set_creation_noop_and_one_time_codes(tfa_environment, check_mode, factor_id):
    module, client, api = tfa_environment(type="recovery", factor_id=factor_id, description=None, totp_uri=None, value=None,
                                          check_mode=check_mode)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["changed"] is True
    assert result["factor"]["type"] == "recovery"
    if check_mode:
        assert "recovery_codes" not in result
        assert "id" not in result["factor"]
        api.post.assert_not_called()
        return
    assert result["factor"]["id"] == "recovery"
    assert result["recovery_codes"] == ["EXAMPLE-recovery-one", "EXAMPLE-recovery-two"]
    repeated = run(module, ProxmoxTfaModule(module, client))
    assert repeated["changed"] is False
    assert "recovery_codes" not in repeated
    info = run(module, ProxmoxTfaInfoModule(module, client))
    assert "EXAMPLE-recovery-one" not in str(info)
    module.params.update(state="absent", api_user="root@pam")
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True
    assert api.entries["application@pve"] == []


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("factor_type", ["webauthn", "u2f"])
def test_hardware_start_finish_and_repeated_completion(tfa_environment, factor_type, check_mode):
    module, client, api = tfa_environment(type=factor_type, totp_uri=None, value=None, check_mode=check_mode)
    started = run(module, ProxmoxTfaModule(module, client))
    assert started["changed"] is True
    assert api.entries["application@pve"] == []
    if check_mode:
        assert "challenge" not in started
        assert "id" not in started["factor"]
        api.post.assert_not_called()
        return
    assert started["factor"] is None
    assert started["enrollment_pending"] is True
    challenge = json.loads(started["challenge"])["publicKey"]["challenge"] if factor_type == "webauthn" else started["challenge"]
    module.params.update(challenge=challenge, value='{"EXAMPLE":"attestation"}')
    finished = run(module, ProxmoxTfaModule(module, client))
    assert finished["factor"]["type"] == factor_type
    assert "challenge" not in finished
    assert "enrollment_pending" not in finished
    assert api.post.call_args.kwargs["challenge"] == challenge
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is False
    assert api.post.call_count == 2


@pytest.mark.parametrize("check_mode", [False, True])
def test_yubico_creation_and_existing_selector(tfa_environment, check_mode):
    module, client, api = tfa_environment(type="yubico", totp_uri=None, value="EXAMPLE-device-otp", check_mode=check_mode)
    result = run(module, ProxmoxTfaModule(module, client))
    assert result["factor"]["type"] == "yubico"
    assert "EXAMPLE-device-otp" not in str(result)
    if check_mode:
        api.post.assert_not_called()
    else:
        assert api.post.call_args.kwargs["value"] == "EXAMPLE-device-otp"
        assert "totp" not in api.post.call_args.kwargs
        assert run(module, ProxmoxTfaModule(module, client))["changed"] is False


@pytest.mark.parametrize("options,message", [
    ({"type": "totp", "challenge": "EXAMPLE"}, "challenge is only valid"),
    ({"type": "yubico", "totp_uri": None, "value": None}, "current device OTP"),
    ({"type": "yubico", "totp_uri": None, "challenge": "EXAMPLE"}, "challenge is only valid"),
    ({"type": "webauthn", "totp_uri": None, "value": "response"}, "both challenge and value"),
    ({"type": "u2f", "totp_uri": None, "value": None, "challenge": "EXAMPLE"}, "both challenge and value"),
    ({"type": "recovery", "totp_uri": None}, "does not accept value"),
    ({"type": "recovery", "totp_uri": None, "value": None}, "recovery-set description or enable"),
    ({"type": "recovery", "totp_uri": None, "description": None, "value": None, "enable": False}, "recovery-set description or enable"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_provider_inputs_rejected_before_any_write(tfa_environment, options, message, check_mode):
    module, client, api = tfa_environment(check_mode=check_mode, **options)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("response", [None, {}, {"id": "unexpected"}, {"challenge": ""}, {"challenge": {"invalid": True}}])
def test_invalid_hardware_start_response_preserves_completed_request(tfa_environment, response):
    module, client, api = tfa_environment(type="webauthn", totp_uri=None, value=None)
    api.post.side_effect = None
    api.post.return_value = response
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="no usable challenge") as error:
        handler.execute()
    assert error.value.args[0]["changed"] is True
    assert "challenge" not in error.value.args[0]


@pytest.mark.parametrize("codes", [None, [], [None], "EXAMPLE"])
def test_invalid_recovery_creation_response_does_not_expose_codes(tfa_environment, codes):
    module, client, api = tfa_environment(type="recovery", description=None, totp_uri=None, value=None)
    original = api._post

    def create(path, **parameters):
        response = original(path, **parameters)
        response["recovery"] = codes
        return response

    api.post.side_effect = create
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="no usable codes") as error:
        handler.execute()
    assert error.value.args[0]["changed"] is True
    assert "recovery_codes" not in error.value.args[0]


@pytest.mark.parametrize("query,options", [
    ("configuration", {"userid": "application@pve"}),
    ("user_types", {"userid": None}),
    ("user_types", {"factor_id": "factor-1"}),
    ("factors", {"multiple": True}),
])
def test_info_query_selection_validation(tfa_environment, query, options):
    module, client, api = tfa_environment(query=query, **options)
    handler = ProxmoxTfaInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("multiple", [None, False, True])
def test_native_user_type_query_preserves_server_representation(tfa_environment, check_mode, multiple):
    module, client, api = tfa_environment(query="user_types", multiple=multiple, check_mode=check_mode)
    result = run(module, ProxmoxTfaInfoModule(module, client))
    assert result["changed"] is False
    assert result["user_types"]["user"] == ("oath" if multiple is False else ["totp"])
    assert result["user_types"]["realm"] == "oath"
    assert "EXAMPLE-hidden" not in str(result)
    assert api.get.call_args.kwargs == {"multiple": int(multiple is not False)}


@pytest.mark.parametrize("check_mode", [False, True])
def test_configuration_info_excludes_other_datacenter_properties(tfa_environment, check_mode):
    module, client, api = tfa_environment(query="configuration", userid=None, check_mode=check_mode)
    result = run(module, ProxmoxTfaInfoModule(module, client))
    assert result == {"changed": False, "configuration": {"webauthn": {"rp": "Example", "id": "example.com"}}}
    client.validate_pve_permissions.assert_called_once_with(api, "/")


@pytest.mark.parametrize("userid", ["root@pam", "controller@pve"])
def test_protected_identity_requires_explicit_opt_in(tfa_environment, userid):
    operator = "root@pam" if userid == "root@pam" else "controller@pve"
    module, client, api = tfa_environment(userid=userid, api_user=operator, allow_protected_user=True)
    api.users.add(userid)
    api.entries[userid] = []
    assert run(module, ProxmoxTfaModule(module, client))["changed"] is True


def test_nonroot_cannot_manage_root_even_with_opt_in(tfa_environment):
    module, client, api = tfa_environment(userid="root@pam", allow_protected_user=True)
    handler = ProxmoxTfaModule(module, client)
    with pytest.raises(module.Fail, match="Only root@pam"):
        handler.execute()
    api.post.assert_not_called()


def test_realm_configuration_info_does_not_expose_api_key(tfa_environment):
    module, client, api = tfa_environment(query="configuration", realm="contractors", userid=None)
    result = run(module, ProxmoxTfaInfoModule(module, client))
    assert result["configuration"]["realm"] == "contractors"
    assert result["configuration"]["tfa"]["id"] == "123"
    assert "key" not in result["configuration"]["tfa"]
    assert "EXAMPLE-api-key" not in str(result)
