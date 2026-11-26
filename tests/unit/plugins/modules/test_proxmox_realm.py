# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from types import SimpleNamespace
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
        data = copy.deepcopy(self.records[realm])
        if realm in ProxmoxRealmModule.builtin_comments and not data.get("comment"):
            data["comment"] = ProxmoxRealmModule.builtin_comments[realm]
        return dict(data, digest=str(self.digest))

    def _write(self, method, path, parameters):
        self.writes.append((method, path, copy.deepcopy(parameters)))
        realm = parameters.get("realm") if method == "POST" else unquote(path.split("/")[-1])
        if method == "DELETE":
            if realm in ProxmoxRealmModule.builtin_comments:
                self.records[realm] = {"type": realm, "comment": ProxmoxRealmModule.builtin_comments[realm]}
            else:
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
                if key == "password":
                    self.secrets.pop(realm, None)
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
    assert not {"password", "client-key"} & selected["realms"][0].keys()
    assert selected["realms"][0]["tfa"] == {}
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
    "options", ["full=2", "purge=wrong,scope=users", "scope=wrong", "enable-new=true", "remove-vanished=all", "scope=users,scope=groups", "", "unknown=1"]
)
def test_invalid_sync_options_are_rejected_without_writes(realm_environment, check_mode, options):
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


@pytest.fixture
def sync_environment(realm_environment):
    """Exercise the real shared task waiter with an isolated realm API."""
    def create(check_mode=False, **options):
        module, client, api = realm_environment(existing=True, check_mode=check_mode)
        module.params.update(dict.fromkeys(proxmox_realm.FIELDS))
        module.params.update(type=None, state="synced", wait=None, sync_options={"scope": "both"}, allow_protected=False)
        module.params.update(options)
        api.sync_requests = []
        api.upid = "UPID:pve01:00000001:00000002:auth-realm-sync:corporate:controller@pve:"
        status = SimpleNamespace(get=Mock(return_value={"status": "stopped", "exitstatus": "OK"}))
        api.nodes = Mock(return_value=SimpleNamespace(tasks=Mock(return_value=SimpleNamespace(status=status))))
        original = api.post

        def post(path, **parameters):
            if path.endswith("/sync"):
                api.sync_requests.append((path, parameters))
                return api.upid
            return original(path, **parameters)

        api.post = post
        return module, client, api, status.get
    return create


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("provider", ["ldap", "ad"])
@pytest.mark.parametrize("scope", ["users", "groups", "both"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_sync_covers_scope_preview_and_check_mode_without_configuration_writes(sync_environment, check_mode, provider, scope, dry_run):
    options = dict(scope=scope, dry_run=dry_run, enable_new=False, remove_vanished="acl;entry;properties", full=False, purge=True)
    module, client, api, poll = sync_environment(check_mode=check_mode, sync_options=options)
    api.records["corporate"]["type"] = provider
    before = copy.deepcopy(api.records)
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is (not dry_run)
    assert result["sync"]["scope"] == scope
    assert result["sync"]["dry_run"] is dry_run
    assert api.records == before
    assert api.writes == []
    if check_mode:
        assert api.sync_requests == []
        assert "task_upid" not in result
        poll.assert_not_called()
    else:
        path, parameters = api.sync_requests[0]
        assert path == "access/domains/corporate/sync"
        assert parameters == {"scope": scope, "dry-run": int(dry_run), "enable-new": 0,
                              "remove-vanished": "acl;entry;properties", "full": 0, "purge": 1}
        assert result["task_upid"] == api.upid
        assert result["task_status"]["exitstatus"] == "OK"
        assert client._changed is (not dry_run)
        assert all(isinstance(parameters[key], int) and not isinstance(parameters[key], bool) for key in ("dry-run", "enable-new", "full", "purge"))
        poll.assert_called_once()
    permission_paths = [call.args[1] for call in client.validate_pve_permissions.call_args_list]
    assert "/access/groups" in permission_paths
    assert "/access/realm/corporate" in permission_paths


@pytest.mark.parametrize("check_mode", [False, True])
def test_sync_can_use_stored_scope_without_overwriting_native_defaults(sync_environment, check_mode):
    module, client, api, _poll = sync_environment(check_mode=check_mode, sync_options=None)
    api.records["corporate"]["sync-defaults-options"] = "scope=users,enable-new=0,remove-vanished=properties"
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["sync"]["scope"] == "users"
    if not check_mode:
        assert api.sync_requests == [("access/domains/corporate/sync", {"dry-run": 0})]


def test_sync_submission_can_return_without_polling(sync_environment):
    module, client, api, poll = sync_environment(wait=False)
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert result["task_upid"] == api.upid
    assert "task_status" not in result
    poll.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"sync_options": {}}, {"sync_options": {"remove_vanished": "all", "scope": "users"}},
                                     {"comment": "do not silently ignore"}, {"password": "EXAMPLE-only"},
                                     {"check_connection": True}, {"remove_properties": ["comment"]}])
def test_sync_rejects_invalid_requests_before_submission(sync_environment, check_mode, options):
    module, client, api, poll = sync_environment(check_mode=check_mode, **options)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.sync_requests == []
    assert api.writes == []
    poll.assert_not_called()


@pytest.mark.parametrize("provider", ["openid", "pam", "pve"])
def test_native_non_directory_providers_cannot_be_synchronized(sync_environment, provider):
    module, client, api, _poll = sync_environment()
    api.records["corporate"]["type"] = provider
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="Only LDAP and AD"):
        handler.execute()
    assert api.sync_requests == []


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("failure", ["task", "timeout", "status_read", "realm_read", "bad_upid"])
def test_sync_failures_preserve_mutation_state_and_task_metadata(sync_environment, monkeypatch, dry_run, failure):
    from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox_task
    module, client, api, poll = sync_environment(sync_options={"scope": "users", "dry_run": dry_run})
    if failure == "task":
        poll.return_value = {"status": "stopped", "exitstatus": "directory unavailable"}
    elif failure == "timeout":
        module.params["task_timeout"] = 1
        poll.return_value = {"status": "running"}
        monkeypatch.setattr(proxmox_task.time, "monotonic", Mock(side_effect=[0, 2]))
    elif failure == "status_read":
        poll.side_effect = RuntimeError("status unavailable")
    elif failure == "realm_read":
        original = api.get
        api.get = lambda path: client.fail_request("readback unavailable") if api.sync_requests else original(path)
    else:
        api.upid = None
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is (not dry_run)
    if failure in ("task", "timeout"):
        assert result.value.args[0]["task"] == api.upid
    assert api.writes == []


@pytest.mark.parametrize("provider", ["ldap", "ad"])
def test_legacy_native_configuration_options_and_password_removal(realm_environment, provider):
    module, client, api = realm_environment(type=provider, domain="example.com" if provider == "ad" else None,
                                            secure=True, password="EXAMPLE-bind", sync_defaults_options="scope=both,full=1,purge=1")
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["realm"]["secure"] == 1
    assert api.secrets["corporate"] == "EXAMPLE-bind"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(password=None, remove_properties=["password"])
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert "corporate" not in api.secrets
    assert "password" in api.writes[-1][2]["delete"].split(",")


def test_mode_and_legacy_secure_obey_native_mode_precedence(realm_environment):
    module, client, api = realm_environment(secure=True, mode="ldap")
    created = run(module, ProxmoxRealmModule(module, client))
    assert created["realm"]["secure"] == 1
    assert created["realm"]["mode"] == "ldap"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(mode="ldaps")
    assert run(module, ProxmoxRealmModule(module, client))["realm"]["mode"] == "ldaps"
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("realm", ["pam", "pve"])
def test_builtin_settings_and_native_reset_are_available_with_explicit_opt_in(realm_environment, check_mode, realm):
    module, client, api = realm_environment(realm=realm, type=None, server1=None, base_dn=None, user_attr=None,
                                            allow_protected=True, check_mode=check_mode, comment="custom comment")
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert result["realm"]["comment"] == "custom comment"
    if not check_mode:
        assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    module.params.update(comment=None, state="reset")
    result = run(module, ProxmoxRealmModule(module, client))
    assert result["changed"] is True
    assert result["realm"]["type"] == realm
    assert result["realm"]["comment"] == ProxmoxRealmModule.builtin_comments[realm]


@pytest.mark.parametrize("check_mode", [False, True])
def test_builtin_absence_explains_native_synthesized_record(realm_environment, check_mode):
    module, client, api = realm_environment(realm="pam", state="absent", allow_protected=True, check_mode=check_mode)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="state=reset"):
        handler.execute()
    assert api.writes == []


@pytest.mark.parametrize("value", ["type=oath,digits=8,step=60", "type=yubico,id=123,key=EXAMPLE-only,url=https://otp.invalid"])
def test_tfa_configuration_is_semantic_and_only_safe_metadata_is_returned(realm_environment, value):
    module, client, api = realm_environment(tfa=value)
    created = run(module, ProxmoxRealmModule(module, client))
    assert "key" not in created["realm"]["tfa"]
    assert "EXAMPLE-only" not in repr(created)
    module.params["tfa"] = ",".join(reversed(value.split(",")))
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False
    selected = run(module, ProxmoxRealmInfoModule(module, client))
    assert selected["realms"][0]["tfa"] == created["realm"]["tfa"]
    assert "EXAMPLE-only" not in repr(selected)
    module.params.update(tfa=None, remove_properties=["tfa"])
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is True
    assert "tfa" not in api.records["corporate"]


@pytest.mark.parametrize("tfa", ["type=unknown", "type=oath,digits=5", "type=oath,step=9", "type=oath,type=yubico", "type=oath,unknown=1"])
def test_invalid_native_tfa_settings_fail_before_writes(realm_environment, tfa):
    module, client, api = realm_environment(tfa=tfa)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="tfa"):
        handler.execute()
    assert api.writes == []


@pytest.mark.parametrize("realm", ["pam", "pve"])
@pytest.mark.parametrize("removal", [{"comment": ""}, {"remove_properties": ["comment"]}])
def test_builtin_comment_removal_restores_the_native_synthesized_default(realm_environment, realm, removal):
    module, client, api = realm_environment(realm=realm, type=None, server1=None, base_dn=None, user_attr=None,
                                            allow_protected=True, **removal)
    api.records[realm]["comment"] = "Custom built-in comment"
    removed = run(module, ProxmoxRealmModule(module, client))
    assert removed["changed"] is True
    assert removed["realm"]["comment"] == ProxmoxRealmModule.builtin_comments[realm]
    assert run(module, ProxmoxRealmModule(module, client))["changed"] is False


@pytest.mark.parametrize("options", [{"type": "ad"}, {"realm": "missing"}])
def test_sync_validates_existing_realm_and_expected_type(sync_environment, options):
    module, client, api, poll = sync_environment(**options)
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert api.sync_requests == []
    poll.assert_not_called()


def test_sync_permission_failure_prevents_submission(sync_environment):
    module, client, api, poll = sync_environment()

    def validate(_api, path):
        if path == "/access/groups":
            module.fail_json(msg="missing group permission")

    client.validate_pve_permissions.side_effect = validate
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail, match="missing group permission"):
        handler.execute()
    assert api.sync_requests == []
    poll.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True])
def test_sync_rejected_submission_is_unchanged(sync_environment, dry_run):
    module, client, api, poll = sync_environment(sync_options={"scope": "both", "dry_run": dry_run})
    api.post = Mock(side_effect=RuntimeError("request rejected"))
    handler = ProxmoxRealmModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert not failure.value.args[0].get("changed", False)
    poll.assert_not_called()


@pytest.mark.parametrize("state,options", [
    ("synced", {"sync_options": {"scope": "groups", "enable_new": False, "full": True, "purge": False, "dry_run": True}, "wait": False}),
    ("reset", {"allow_protected": True}),
    ("present", {"secure": True, "tfa": "type=yubico,id=123,key=EXAMPLE-tfa-key"}),
])
def test_real_argument_validation_accepts_all_native_operation_options(monkeypatch, state, options):
    parameters = {"api_host": "pve.example.com", "api_token_id": "controller@pve!automation", "api_token_secret": "EXAMPLE-secret",
                  "realm": "corporate", "state": state, **options}
    monkeypatch.setattr(basic, "_load_params", lambda: parameters)
    handler = Mock()
    monkeypatch.setattr(proxmox_realm, "ProxmoxRealmModule", handler)
    proxmox_realm.main()
    module = handler.call_args.args[0]
    assert module.params["state"] == state
    for key, value in options.items():
        actual = module.params[key]
        assert ({name: item for name, item in actual.items() if item is not None} if isinstance(actual, dict) else actual) == value
    assert module.argument_spec["tfa"]["no_log"] is True


def test_embedded_tfa_key_is_registered_for_failure_redaction(realm_environment):
    module, client, _api = realm_environment(tfa="type=yubico,id=123,key=EXAMPLE-tfa-key")
    module.no_log_values = set()
    run(module, ProxmoxRealmModule(module, client))
    assert "EXAMPLE-tfa-key" in module.no_log_values
