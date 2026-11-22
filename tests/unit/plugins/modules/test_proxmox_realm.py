# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from urllib.parse import unquote
from unittest.mock import Mock

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_realm, proxmox_realm_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_realm import ProxmoxRealmModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_realm_info import ProxmoxRealmInfoModule


class RealmApi:
    """Model independent discovery, config digests, provider writes and secrets."""

    def __init__(self):
        self.records = {"pve": {"type": "pve"}, "pam": {"type": "pam", "default": 1}}
        self.digest = 1
        self.secrets = {}
        self.reads = []
        self.writes = []
        self.after_write_failure = False

    def get(self, path):
        self.reads.append(path)
        if self.writes and self.after_write_failure:
            raise RuntimeError("simulated follow-up failure")
        if path == "access/domains":
            return [
                {"realm": key, **{name: value for name, value in record.items() if name in ("type", "default", "comment")}}
                for key, record in self.records.items()
            ]
        realm = unquote(path.split("/")[-1])
        return dict(copy.deepcopy(self.records[realm]), digest=str(self.digest))

    def _write(self, method, path, parameters):
        self.writes.append((method, path, copy.deepcopy(parameters)))
        realm = parameters.get("realm") if method == "POST" else unquote(path.split("/")[-1])
        if method == "DELETE":
            del self.records[realm]
            self.secrets.pop(realm, None)
        else:
            values = dict(parameters)
            if "digest" in values:
                assert values.pop("digest") == str(self.digest)
            values.pop("realm", None)
            values.pop("check-connection", None)
            if "password" in values:
                self.secrets[realm] = values.pop("password")
            data = self.records.setdefault(realm, {})
            for key in values.pop("delete", "").split(","):
                data.pop(key, None)
            if values.get("default"):
                for record in self.records.values():
                    record.pop("default", None)
            data.update(values)
            if "sync-defaults-options" in data:
                # Proxmox parses and prints this property string on writes and detail reads.
                fields = data["sync-defaults-options"].split(",")
                data["sync-defaults-options"] = ",".join(sorted(fields))
            if data.get("comment") in ("", "0"):
                data.pop("comment", None)
        self.digest += 1

    def post(self, path, **parameters):
        self._write("POST", path, parameters)

    def put(self, path, **parameters):
        self._write("PUT", path, parameters)

    def delete(self, path):
        self._write("DELETE", path, {})


@pytest.fixture
def realm_environment(pool_environment):
    def create(check_mode=False, existing=False, **parameters):
        params = dict.fromkeys(proxmox_realm.FIELDS)
        params.update(
            realm="corporate",
            type="ldap",
            server1="ldap.example.com",
            base_dn="dc=example,dc=com",
            user_attr="uid",
            api_user="controller@pve",
            password=None,
            update_password="on_create",
            remove_properties=[],
            digest=None,
            check_connection=None,
        )
        params.update(parameters)
        module, client, _api = pool_environment(check_mode=check_mode, **params)
        api = RealmApi()
        if existing:
            api.records[params["realm"]] = {"type": "ldap", "server1": "ldap.example.com", "base_dn": "dc=example,dc=com", "user_attr": "uid"}
        client._api = api
        return module, client, api

    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize(
    "provider,options",
    [
        ("ldap", {}),
        ("ad", {"domain": "example.com", "base_dn": None, "user_attr": None}),
        ("openid", {"server1": None, "base_dn": None, "user_attr": None, "issuer_url": "https://id.example.com", "client_id": "pve"}),
    ],
)
def test_provider_lifecycle_and_independent_information(realm_environment, provider, options):
    module, client, api = realm_environment(type=provider, **options)
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["changed"] is True
    assert created["realm"]["type"] == provider
    selected = run(module, ProxmoxRealmInfoModule(module, client))
    assert selected["changed"] is False
    assert selected["realms"][0]["type"] == provider
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params["comment"] = "updated"
    updated = run(module, ProxmoxRealmModule(module, client))
    assert updated["changed"] is True
    assert api.records["corporate"]["comment"] == "updated"
    assert api.writes[-1][2]["digest"] == "2"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params["state"] = "absent"
    assert run(module, ProxmoxRealmModule(module, client)) == {"changed": True, "realm": None}
    assert run(module, ProxmoxRealmInfoModule(module, client))["realms"] == []
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_check_mode_has_no_writes(realm_environment, operation):
    module, client, api = realm_environment(check_mode=True, existing=operation != "create", comment="predicted")
    module.params["state"] = "absent" if operation == "delete" else "present"
    before = copy.deepcopy(api.records)
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert api.records == before
    assert api.writes == []
    if operation == "delete":
        assert result["realm"] is None
    else:
        assert result["realm"]["comment"] == "predicted"
        assert "digest" not in result["realm"]


def test_omission_removal_and_false_defaults(realm_environment):
    module, client, api = realm_environment(existing=True, type=None, server1=None, base_dn=None, user_attr=None)
    api.records["corporate"].update(server2="backup.example.com", comment="retain", verify=1)
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(remove_properties=["server2"], verify=False)
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert "server2" not in api.records["corporate"]
    assert "verify" not in api.records["corporate"]
    assert api.records["corporate"]["comment"] == "retain"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(remove_properties=[], comment="")
    run(module, ProxmoxRealmModule(module, client))
    assert "comment" not in api.records["corporate"]
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("realm", ["pve", "pam", "controller", ".", "..", "a", "slash/name", "wrong%41", "a" * 33])
def test_rejects_protected_and_invalid_realms_before_api(realm_environment, check_mode, realm):
    module, client, api = realm_environment(check_mode=check_mode, realm=realm, api_user="ops@controller")
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.reads == []
    assert api.writes == []


@pytest.mark.parametrize(
    "options",
    [
        {"type": None},
        {"server1": ""},
        {"base_dn": None},
        {"user_attr": "x"},
        {"port": 0},
        {"port": 65536},
        {"domain": "example.com"},
        {"client_id": "pve"},
        {"remove_properties": ["comment"]},
        {"comment": "x" * 4097},
    ],
)
def test_invalid_creation_inputs(realm_environment, options):
    module, client, api = realm_environment(**options)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.writes == []


@pytest.mark.parametrize(
    "options",
    [
        {"type": "ad"},
        {"remove_properties": ["server1"]},
        {"remove_properties": ["password"]},
        {"remove_properties": ["comment"], "comment": "conflict"},
        {"digest": "stale"},
    ],
)
def test_invalid_updates_preserve_existing(realm_environment, options):
    module, client, api = realm_environment(existing=True, **options)
    before = copy.deepcopy(api.records)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.records == before
    assert api.writes == []


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("check_connection", [False, True])
def test_openid_rejects_even_false_connection_option(realm_environment, check_mode, check_connection):
    module, client, api = realm_environment(
        check_mode=check_mode,
        type="openid",
        check_connection=check_connection,
        server1=None,
        base_dn=None,
        user_attr=None,
        issuer_url="https://id.example.com",
        client_id="pve",
    )
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="check_connection"):
        handler.execute()
    assert api.writes == []


def test_secret_policies_and_sanitized_results(realm_environment):
    module, client, api = realm_environment(password="EXAMPLE-bind-password")
    result = run(module, ProxmoxRealmModule(module, client))
    assert "password" not in result["realm"]
    assert api.secrets["corporate"] == "EXAMPLE-bind-password"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(update_password="always", password="EXAMPLE-replacement")
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert api.secrets["corporate"] == "EXAMPLE-replacement"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True


def test_openid_client_secret_is_compared_but_never_returned(realm_environment):
    module, client, api = realm_environment(
        type="openid",
        server1=None,
        base_dn=None,
        user_attr=None,
        issuer_url="https://id.example.com",
        client_id="pve",
        client_key="EXAMPLE-client-secret",
    )
    result = run(module, ProxmoxRealmModule(module, client))
    assert "client-key" not in result["realm"]
    assert api.records["corporate"]["client-key"] == "EXAMPLE-client-secret"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    selected = run(module, ProxmoxRealmInfoModule(module, client))
    assert "client-key" not in selected["realms"][0]


def test_successful_write_followed_by_failure_retains_changed(realm_environment):
    module, client, api = realm_environment()
    api.after_write_failure = True
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "corporate" in api.records


def test_matching_digest_is_used_once_before_write(realm_environment):
    module, client, api = realm_environment(existing=True, comment="changed", digest="1")
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert api.writes[0][2]["digest"] == "1"
    assert result["realm"]["digest"] == "2"


def test_default_change_is_explicit_cross_realm_effect(realm_environment):
    module, client, api = realm_environment(default=True)
    run(module, ProxmoxRealmModule(module, client))
    assert api.records["corporate"]["default"] == 1
    assert "default" not in api.records["pam"]


@pytest.mark.parametrize("check_mode", [False, True])
def test_info_list_detail_missing_and_builtin_are_read_only(realm_environment, check_mode):
    module, client, api = realm_environment(check_mode=check_mode, existing=True)
    api.records["corporate"].update({"client-key": "EXAMPLE-secret", "password": "EXAMPLE-bind", "tfa": "sensitive"})
    before = copy.deepcopy(api.records)
    selected = run(module, ProxmoxRealmInfoModule(module, client))
    assert selected["changed"] is False
    assert not {"password", "client-key", "tfa"} & selected["realms"][0].keys()
    module.params["realm"] = None
    listed = run(module, ProxmoxRealmInfoModule(module, client))
    assert len(listed["realms"]) == 3
    module.params["realm"] = "pve"
    assert run(module, ProxmoxRealmInfoModule(module, client))["realms"][0]["type"] == "pve"
    module.params["realm"] = "nonexistent"
    assert run(module, ProxmoxRealmInfoModule(module, client))["realms"] == []
    assert api.records == before
    assert api.writes == []


@pytest.mark.parametrize("module_under_test", [proxmox_realm, proxmox_realm_info])
def test_real_argument_builder_inherits_auth_and_options(monkeypatch, module_under_test):
    parameters = {
        "api_host": "pve.example.com",
        "api_token_id": "controller@pve!automation",
        "api_token_secret": "EXAMPLE-secret",
        "realm": "corporate",
    }
    monkeypatch.setattr(basic, "_load_params", lambda: parameters)
    monkeypatch.setattr(module_under_test, "ProxmoxRealmModule" if module_under_test is proxmox_realm else "ProxmoxRealmInfoModule", Mock())
    module_under_test.main()
    handler = module_under_test.ProxmoxRealmModule if module_under_test is proxmox_realm else module_under_test.ProxmoxRealmInfoModule
    module = handler.call_args.args[0]
    assert module.params["realm"] == "corporate"
    assert module.params["api_token_id"] == "controller@pve!automation"
    assert module.argument_spec["api_token_secret"]["no_log"] is True


def test_openid_creation_claim_and_immutable_claim(realm_environment):
    module, client, api = realm_environment(
        type="openid", server1=None, base_dn=None, user_attr=None, issuer_url="https://id.example.com", client_id="pve", username_claim="email"
    )
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["realm"]["username-claim"] == "email"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    before = copy.deepcopy(api.records)
    module.params["username_claim"] = "subject"
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="username_claim"):
        handler.execute()
    assert api.records == before


@pytest.mark.parametrize("check_connection", [False, True])
def test_ldap_connection_flag_is_integer_and_only_written_with_change(realm_environment, check_connection):
    module, client, api = realm_environment(check_connection=check_connection)
    run(module, ProxmoxRealmModule(module, client))
    assert api.writes[0][2]["check-connection"] == int(check_connection)
    assert isinstance(api.writes[0][2]["check-connection"], int)
    assert not isinstance(api.writes[0][2]["check-connection"], bool)
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    assert len(api.writes) == 1


def test_existing_bind_password_ignored_with_on_create_policy(realm_environment):
    module, client, api = realm_environment(existing=True, password="EXAMPLE-not-written")
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    assert api.secrets == {}
    assert api.writes == []


def test_valid_dotted_realm_is_literal(realm_environment):
    module, client, api = realm_environment(realm="corporate..realm")
    run(module, ProxmoxRealmModule(module, client))
    assert "corporate..realm" in api.records
    assert "access/domains/corporate..realm" in api.reads


@pytest.mark.parametrize("realm", [".", "..", "x", "wrong/name", "a" * 33])
def test_info_rejects_invalid_identifier_before_api(realm_environment, realm):
    module, client, api = realm_environment(realm=realm)
    handler = ProxmoxRealmInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.reads == []


@pytest.mark.parametrize(
    "authentication",
    [
        {"api_user": "ops!service@corporate", "api_token_id": None},
        {"api_user": None, "api_token_id": "ops!service@corporate!automation"},
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_controller_realm_guard_preserves_exclamation_in_owner(realm_environment, authentication, check_mode):
    module, client, api = realm_environment(check_mode=check_mode, **authentication)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="cannot be managed"):
        handler.execute()
    assert api.reads == []
    assert api.writes == []


@pytest.mark.parametrize("comment", ["", "0"])
def test_empty_comment_matches_proxmox_storage_and_repeats_without_changes(realm_environment, comment):
    module, client, api = realm_environment(comment=comment)
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert "comment" not in api.records["corporate"]
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    api.records["corporate"]["comment"] = "existing"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert "comment" not in api.records["corporate"]
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


def test_sync_defaults_property_order_is_semantic_and_repeats_as_noop(realm_environment):
    module, client, api = realm_environment(sync_defaults_options="scope=users,enable-new=0")
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["changed"] is True
    assert api.records["corporate"]["sync-defaults-options"] == "enable-new=0,scope=users"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params["sync_defaults_options"] = "remove-vanished=properties;acl,scope=both"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    module.params["sync_defaults_options"] = "scope=both,enable-new=1,remove-vanished=acl;properties"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "options", ["full=1", "purge=0,scope=users", "scope=wrong", "enable-new=true", "remove-vanished=all", "scope=users,scope=groups", "", "unknown=1"]
)
def test_invalid_or_deprecated_sync_options_are_rejected_without_writes(realm_environment, check_mode, options):
    module, client, api = realm_environment(check_mode=check_mode, sync_defaults_options=options)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.writes == []


def test_legacy_current_sync_defaults_compare_to_modern_semantics(realm_environment):
    module, client, api = realm_environment(existing=True, sync_defaults_options="remove-vanished=acl;entry;properties,scope=both")
    api.records["corporate"]["sync-defaults-options"] = "full=1,purge=1,scope=both"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    assert api.writes == []


def test_update_disappearance_without_type_preserves_completed_mutation(realm_environment):
    module, client, api = realm_environment(existing=True, type=None, comment="updated")
    original_put = api.put

    def put(path, **parameters):
        original_put(path, **parameters)
        del api.records["corporate"]

    api.put = put
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "could not be read back" in failure.value.args[0]["msg"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("mode", ["ldap", "ldaps", "ldap+starttls"])
def test_explicit_mode_replaces_legacy_secure_flag(realm_environment, check_mode, mode):
    module, client, api = realm_environment(existing=True, check_mode=check_mode, mode=mode)
    api.records["corporate"]["secure"] = 1
    before = copy.deepcopy(api.records)
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert "secure" not in result["realm"]
    assert result["realm"].get("mode", "ldap") == mode
    if check_mode:
        assert api.records == before
        assert api.writes == []
    else:
        assert "secure" not in api.records["corporate"]
        assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("provider", [None, "ldap"])
def test_unexpected_provider_after_successful_write_retains_changed(realm_environment, existing, provider):
    module, client, api = realm_environment(existing=existing, type=provider if existing else "ldap", comment="new value")
    method = "put" if existing else "post"
    original = getattr(api, method)

    def write(path, **parameters):
        original(path, **parameters)
        api.records["corporate"]["type"] = "openid"

    setattr(api, method, write)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "provider type" in failure.value.args[0]["msg"]
    assert len(api.writes) == 1


def test_unexpected_claim_after_successful_creation_retains_changed(realm_environment):
    module, client, api = realm_environment(
        type="openid", server1=None, base_dn=None, user_attr=None, issuer_url="https://id.example.com", client_id="pve", username_claim="email"
    )
    original = api.post

    def write(path, **parameters):
        original(path, **parameters)
        api.records["corporate"]["username-claim"] = "subject"

    api.post = write
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "username_claim" in failure.value.args[0]["msg"]


def test_openid_additional_audiences_lifecycle_and_sanitized_readback(realm_environment):
    module, client, api = realm_environment(
        type="openid", server1=None, base_dn=None, user_attr=None, issuer_url="https://id.example.com", client_id="pve",
        audiences="https://app.example.com api-client", query_userinfo=False, groups_autocreate=True, groups_overwrite=True
    )
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["realm"]["audiences"] == "https://app.example.com api-client"
    assert api.writes[0][2]["query-userinfo"] == 0
    assert api.writes[0][2]["groups-autocreate"] == 1
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    info = run(module, ProxmoxRealmInfoModule(module, client))
    assert info["realms"][0]["audiences"] == created["realm"]["audiences"]
    module.params.update(audiences=None, remove_properties=[])
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(remove_properties=["audiences"])
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert "audiences" not in api.records["corporate"]
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("provider", ["ldap", "ad"])
def test_additional_audiences_are_openid_only(realm_environment, provider):
    module, client, api = realm_environment(type=provider, audiences="other-client")
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="audiences"):
        handler.execute()
    assert api.writes == []


def test_argument_builder_accepts_additional_audiences(monkeypatch):
    parameters = {"api_host": "pve.example.com", "api_token_id": "controller@pve!automation", "api_token_secret": "EXAMPLE-secret",
                  "realm": "corporate", "type": "openid", "audiences": "extra-client"}
    monkeypatch.setattr(basic, "_load_params", lambda: parameters)
    handler = Mock()
    monkeypatch.setattr(proxmox_realm, "ProxmoxRealmModule", handler)
    proxmox_realm.main()
    assert handler.call_args.args[0].params["audiences"] == "extra-client"
