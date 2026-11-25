# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


TARGET = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_realm"


def load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACL = load_helper("proxmox_realm_acl_fixture", TARGET / "library/proxmox_realm_acl_fixture.py")
LDAP = load_helper("proxmox_realm_ldap_fixture", TARGET / "files/realm_ldap_server.py")
USER = "ansible-owned-a@ansible-realm-owned-ldap"
BINDING = {"path": "/vms", "type": "user", "ugid": USER, "roleid": "NoAccess", "propagate": 0}


@pytest.fixture
def acl_fixture_environment(pool_environment):
    def create(check_mode=False, entries=None, **options):
        module, client, _api = pool_environment(check_mode=check_mode, userid=USER, owned_users=[USER], read_only=False, **options)
        records = copy.deepcopy(entries or [])

        def put(endpoint, **parameters):
            assert endpoint == "access/acl"
            assert parameters["path"] == "/vms"
            assert parameters["roles"] == "NoAccess"
            assert parameters["users"] == USER
            if parameters.get("delete"):
                records.remove(BINDING)
            else:
                assert parameters["propagate"] == 0
                records.append(copy.deepcopy(BINDING))

        api = SimpleNamespace(get=Mock(side_effect=lambda _path: copy.deepcopy(records)), put=Mock(side_effect=put))
        client._api = api
        return module, client, api, records
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("userid,owned", [
    (USER, []), ("root@pam", ["root@pam"]),
    ("ansible-a@pam", ["ansible-a@pam"]), ("ansible-/invalid@ansible-realm-a", []),
])
def test_realm_acl_fixture_refuses_unowned_subjects_before_api_access(pool_environment, userid, owned):
    module, client, _api = pool_environment(userid=userid, owned_users=owned, read_only=False)
    client._api = Mock()
    handler = ACL.ProxmoxRealmAclFixture(module, client)
    with pytest.raises(module.Fail, match="Refusing"):
        handler.execute()
    client.api.get.assert_not_called()
    client.api.put.assert_not_called()


@pytest.mark.parametrize("unexpected", [
    {"roleid": "PVEAdmin"}, {"path": "/"}, {"propagate": 1},
    {"ugid": "ansible-unknown@ansible-realm-owned-ldap"},
    {"type": "token", "ugid": USER + "!unexpected"},
    {"type": "group", "ugid": "unknown-ansible-realm-owned-ldap"},
])
def test_realm_acl_fixture_refuses_orphaned_or_unexpected_realm_bindings(acl_fixture_environment, unexpected):
    module, client, api, _records = acl_fixture_environment(entries=[dict(BINDING, **unexpected)])
    handler = ACL.ProxmoxRealmAclFixture(module, client)
    with pytest.raises(module.Fail, match="Unexpected ACL"):
        handler.execute()
    api.put.assert_not_called()


def test_realm_acl_fixture_lifecycle_preserves_unrelated_bindings(acl_fixture_environment):
    unrelated = dict(BINDING, ugid="unrelated@pve", roleid="PVEAuditor")
    module, client, api, records = acl_fixture_environment(entries=[unrelated])
    module.params["read_only"] = True
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["binding"] is None
    api.put.assert_not_called()
    module.params["read_only"] = False
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["changed"] is True
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["changed"] is False
    module.params["state"] = "absent"
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["changed"] is True
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["changed"] is False
    assert records == [unrelated]


def test_realm_acl_fixture_check_mode_does_not_write(acl_fixture_environment):
    module, client, api, records = acl_fixture_environment(check_mode=True)
    assert run(module, ACL.ProxmoxRealmAclFixture(module, client))["changed"] is True
    api.put.assert_not_called()
    assert records == []


def test_realm_acl_fixture_failed_cleanup_remains_changed(acl_fixture_environment):
    module, client, api, _records = acl_fixture_environment(entries=[BINDING])
    module.params["state"] = "absent"
    api.put = Mock(return_value=None)
    handler = ACL.ProxmoxRealmAclFixture(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "removal could not be verified" in failure.value.args[0]["msg"]


def test_directory_fixture_handles_native_filters_requested_names_and_search_scopes():
    records = [{"dn": "uid=owned," + LDAP.BASE_DN, "attributes": {"objectClass": ["inetOrgPerson"], "uid": ["owned"]}},
               {"dn": "cn=group," + LDAP.BASE_DN, "attributes": {"objectClass": ["groupOfNames"], "cn": ["group"]}},
               {"dn": "uid=nested,ou=child," + LDAP.BASE_DN, "attributes": {"objectClass": ["user"], "uid": ["nested"]}}]
    classes = LDAP.encode(
        161,
        b"".join(LDAP.encode(163, LDAP.string("objectClass") + LDAP.string(name)) for name in ("inetorgperson", "user")),
    )
    users = (160, LDAP.encode(135, b"uid") + classes)
    assert [entry["dn"] for entry in LDAP.search_entries(records, LDAP.BASE_DN, 2, users)] == [records[0]["dn"], records[2]["dn"]]
    assert LDAP.search_entries(records, LDAP.BASE_DN, 1, users) == [records[0]]
    assert LDAP.search_entries(records, records[0]["dn"], 0, users) == [records[0]]
    root = LDAP.search_entries(records, "", 0, (135, b"objectClass"))
    assert root[0]["attributes"]["defaultNamingContext"] == [LDAP.BASE_DN]


@pytest.mark.parametrize("payload", [b"\x04", b"\x04\x80", b"\x04\x84\x00\x01\x00\x00", b"\x04\x05x"])
def test_directory_fixture_rejects_unbounded_or_truncated_messages(payload):
    with pytest.raises(ValueError):
        LDAP.elements(payload)
