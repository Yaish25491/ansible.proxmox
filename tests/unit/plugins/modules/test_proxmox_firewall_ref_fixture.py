# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_firewall_refs_info/library/proxmox_firewall_ref_fixture.py"
SPEC = importlib.util.spec_from_file_location("proxmox_firewall_ref_fixture", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def execute(module, client):
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


class ReferenceApi:
    def __init__(self):
        self.records = {"alias": {}, "ipset": {}}
        self.members = {}
        self.get = Mock(side_effect=self._get)
        self.post = Mock(side_effect=self._post)
        self.delete = Mock(side_effect=self._delete)

    def alias_digest(self):
        return hashlib.sha1(json.dumps(self.records["alias"], sort_keys=True).encode()).hexdigest()

    def _get(self, path):
        if "/ipset/" in path:
            return list(self.members.get(path.rsplit("/", 1)[1], []))
        kind = "alias" if path.endswith("aliases") else "ipset"
        return [dict(value, name=name, digest=self.alias_digest()) for name, value in self.records[kind].items()]

    def _post(self, path, **values):
        kind = "alias" if path.endswith("aliases") else "ipset"
        self.records[kind][values["name"]] = dict(values)

    def _delete(self, path, **parameters):
        endpoint, name = path.rsplit("/", 1)
        kind = "alias" if endpoint.endswith("aliases") else "ipset"
        if kind == "alias" and parameters.get("digest") is not None and parameters["digest"] != self.alias_digest():
            raise ResourceException(409, "conflict", "alias digest changed")
        if kind == "ipset" and self.members.get(name) and not parameters.get("force"):
            raise ResourceException(400, "not empty", "IPSet is not empty")
        del self.records[kind][name]


@pytest.fixture
def environment(pool_environment):
    def create(kind="alias", check_mode=False):
        name = "ansible-ref-1000020000-" + kind
        module, client, _api = pool_environment(kind=kind, resource_id=name, owned_ids=[name], read_only=False, check_mode=check_mode)
        api = ReferenceApi()
        client._api = api
        return module, client, api

    return create


@pytest.mark.parametrize("kind", ["alias", "ipset"])
def test_only_owned_unused_definitions_are_created_and_removed(environment, kind):
    module, client, api = environment(kind=kind, check_mode=True)
    assert execute(module, client)["changed"] is True
    api.post.assert_not_called()
    module.check_mode = False
    result = execute(module, client)
    assert result["fixture"]["name"] == module.params["resource_id"]
    assert result["fixture"]["comment"] == HELPER.FixtureModule.comment
    assert execute(module, client)["changed"] is False
    module.params["read_only"] = True
    assert execute(module, client)["changed"] is False
    module.params.update(read_only=False, state="absent")
    module.check_mode = True
    assert execute(module, client)["changed"] is True
    api.delete.assert_not_called()
    module.check_mode = False
    assert execute(module, client)["fixture"] is None
    assert api.records[kind] == {}


@pytest.mark.parametrize("kind", ["alias", "ipset"])
def test_external_names_and_changed_ownership_are_refused(environment, kind):
    module, client, api = environment(kind=kind)
    module.params["owned_ids"] = []
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="Refusing"):
        handler.execute()
    api.get.assert_not_called()
    module.params["owned_ids"] = [module.params["resource_id"]]
    api.records[kind][module.params["resource_id"]] = {"comment": "another owner"}
    module.params["state"] = "absent"
    with pytest.raises(module.Fail, match="unrecognized fixture comment"):
        handler.execute()
    api.delete.assert_not_called()


def test_readback_failure_after_fixture_write_retains_changed(environment):
    module, client, api = environment()
    original_get = api._get

    def get(path):
        if api.post.called:
            raise RuntimeError("readback unavailable")
        return original_get(path)

    api.get.side_effect = get
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True


@pytest.mark.parametrize("kind", ["alias", "ipset"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_fixture_kind_must_match_the_claimed_identifier(environment, kind, check_mode):
    module, client, api = environment(kind=kind, check_mode=check_mode)
    module.params["resource_id"] = "ansible-ref-1000020000-" + ("ipset" if kind == "alias" else "alias")
    module.params["owned_ids"] = [module.params["resource_id"]]
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="Refusing"):
        handler.execute()
    api.get.assert_not_called()
    api.post.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_modified_alias_value_is_not_deleted(environment, check_mode):
    module, client, api = environment(check_mode=check_mode)
    name = module.params["resource_id"]
    api.records["alias"][name] = {"comment": HELPER.FixtureModule.comment, "cidr": "192.0.2.2/32"}
    module.params["state"] = "absent"
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="alias value"):
        handler.execute()
    api.delete.assert_not_called()
    assert api.records["alias"][name]["cidr"] == "192.0.2.2/32"


@pytest.mark.parametrize("check_mode", [False, True])
def test_nonempty_ipset_is_rejected_before_delete_prediction_or_request(environment, check_mode):
    module, client, api = environment(kind="ipset", check_mode=check_mode)
    name = module.params["resource_id"]
    api.records["ipset"][name] = {"comment": HELPER.FixtureModule.comment}
    api.members[name] = [{"cidr": "192.0.2.2/32"}]
    module.params["state"] = "absent"
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="nonempty"):
        handler.execute()
    api.delete.assert_not_called()
    assert name in api.records["ipset"]


def test_alias_cleanup_uses_native_digest(environment):
    module, client, api = environment()
    name = module.params["resource_id"]
    api.records["alias"][name] = {"comment": HELPER.FixtureModule.comment, "cidr": "192.0.2.1/32"}
    expected = api.alias_digest()
    module.params["state"] = "absent"
    assert execute(module, client)["changed"] is True
    api.delete.assert_called_once_with("cluster/firewall/aliases/" + name, digest=expected)


def test_alias_race_is_rejected_without_deleting_changed_data(environment):
    module, client, api = environment()
    name = module.params["resource_id"]
    api.records["alias"][name] = {"comment": HELPER.FixtureModule.comment, "cidr": "192.0.2.1/32"}
    module.params["state"] = "absent"

    def delete(path, **parameters):
        api.records["alias"][name]["cidr"] = "192.0.2.2/32"
        return api._delete(path, **parameters)

    api.delete.side_effect = delete
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["status_code"] == 409
    assert failure.value.args[0].get("changed", False) is False
    assert name in api.records["alias"]


def test_alias_cleanup_without_native_digest_is_refused(environment):
    module, client, api = environment()
    name = module.params["resource_id"]
    api.records["alias"][name] = {"comment": HELPER.FixtureModule.comment, "cidr": "192.0.2.1/32"}
    original = api._get
    api.get.side_effect = lambda path: [{key: value for key, value in entry.items() if key != "digest"} for entry in original(path)]
    module.params["state"] = "absent"
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="native configuration digest"):
        handler.execute()
    api.delete.assert_not_called()


def test_ipset_race_retains_native_atomic_nonempty_guard(environment):
    module, client, api = environment(kind="ipset")
    name = module.params["resource_id"]
    api.records["ipset"][name] = {"comment": HELPER.FixtureModule.comment}
    module.params["state"] = "absent"

    def delete(path, **parameters):
        api.members[name] = [{"cidr": "192.0.2.2/32"}]
        return api._delete(path, **parameters)

    api.delete.side_effect = delete
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["status_code"] == 400
    assert api.delete.call_args.kwargs == {"force": 0}
    assert name in api.records["ipset"]


@pytest.mark.parametrize("kind", ["alias", "ipset"])
def test_read_only_preflight_can_inspect_occupied_names_without_claiming_them(environment, kind):
    module, client, api = environment(kind=kind, check_mode=True)
    name = module.params["resource_id"]
    api.records[kind][name] = {"comment": "unrecognized owner", "cidr": "192.0.2.2/32"}
    api.members[name] = [{"cidr": "192.0.2.2/32"}]
    module.params["read_only"] = True
    result = execute(module, client)
    assert result["changed"] is False
    assert result["fixture"]["comment"] == "unrecognized owner"
    api.post.assert_not_called()
    api.delete.assert_not_called()


def test_native_host_address_representation_preserves_owned_alias_noop(environment):
    module, client, api = environment()
    name = module.params["resource_id"]
    api.records["alias"][name] = {"comment": HELPER.FixtureModule.comment, "cidr": "192.0.2.1"}
    assert execute(module, client)["changed"] is False
    api.post.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("records", [None, {}, [None], [{}]])
def test_invalid_fixture_discovery_cannot_claim_a_definition(environment, records):
    module, client, api = environment()
    api.get.side_effect = None
    api.get.return_value = records
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="invalid definition list"):
        handler.execute()
    api.post.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("records", [None, {}])
def test_invalid_member_response_cannot_establish_an_empty_ipset(environment, check_mode, records):
    module, client, api = environment(kind="ipset", check_mode=check_mode)
    name = module.params["resource_id"]
    api.records["ipset"][name] = {"comment": HELPER.FixtureModule.comment}
    original = api._get
    api.get.side_effect = lambda path: records if "/ipset/" in path else original(path)
    module.params["state"] = "absent"
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="invalid member list"):
        handler.execute()
    api.delete.assert_not_called()
