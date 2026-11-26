# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_token/library/proxmox_token_fixture.py"
SPEC = importlib.util.spec_from_file_location("proxmox_token_fixture", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
IDENTITY = "ansible-owned@pve"


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("identity,owned", [("unrelated@pve", []), ("ansible-owned@pam", ["ansible-owned@pam"]),
                                            ("ansible-/invalid", ["ansible-/invalid"]), (IDENTITY, [])])
def test_fixture_refuses_unowned_or_invalid_identifiers(identity_environment, identity, owned):
    module, client, api = identity_environment(resource_id=identity, owned_ids=owned, read_only=True)
    handler = HELPER.ProxmoxFixtureModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


def test_fixture_read_only_does_not_create(identity_environment):
    module, client, api = identity_environment(resource_id=IDENTITY, owned_ids=[IDENTITY], read_only=True)
    assert run(module, HELPER.ProxmoxFixtureModule(module, client)) == {"changed": False, "fixture": None}
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


def test_fixture_creation_disables_users_and_verifies_removal(identity_environment):
    module, client, api = identity_environment(resource_id=IDENTITY, owned_ids=[IDENTITY], read_only=False, groups=[])
    created = run(module, HELPER.ProxmoxFixtureModule(module, client))
    assert created["changed"] is True
    assert created["fixture"]["enable"] == 0
    assert "password" not in api.post.call_args.kwargs
    assert run(module, HELPER.ProxmoxFixtureModule(module, client))["changed"] is False
    module.params.update(state="absent")
    assert run(module, HELPER.ProxmoxFixtureModule(module, client)) == {"changed": True, "fixture": None}
    assert run(module, HELPER.ProxmoxFixtureModule(module, client))["changed"] is False


def test_fixture_failed_removal_is_not_reported_as_success(identity_environment):
    module, client, api = identity_environment(resource_id=IDENTITY, owned_ids=[IDENTITY], read_only=False, groups=[])
    run(module, HELPER.ProxmoxFixtureModule(module, client))
    api.delete = Mock(return_value=None)
    module.params.update(state="absent")
    handler = HELPER.ProxmoxFixtureModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "Fixture removal could not be verified" in failure.value.args[0]["msg"]


@pytest.mark.parametrize("identity,encoded", [
    ("ansible-ops%41@pve", "ansible-ops%2541%40pve"),
    ("ansible-ops#tag@pve", "ansible-ops%23tag%40pve"),
    ("ansible-ops?tag@pve", "ansible-ops%3Ftag%40pve"),
    ("ansible-ops%2Ftag@pve", "ansible-ops%252Ftag%40pve"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_fixture_reads_and_removes_only_literal_owned_user(identity_environment, identity, encoded, check_mode):
    module, client, api = identity_environment(resource_id=identity, owned_ids=[identity], read_only=True, check_mode=check_mode)
    api.users[identity] = {"enable": 0, "groups": [], "comment": "owned"}
    api.users["ansible-opsA@pve"] = {"enable": 0, "groups": [], "comment": "sibling"}
    assert run(module, HELPER.ProxmoxFixtureModule(module, client))["fixture"]["comment"] == "owned"
    api.get.assert_any_call("access/users/" + encoded)
    module.params.update(read_only=False, state="absent")
    assert run(module, HELPER.ProxmoxFixtureModule(module, client))["changed"] is True
    assert api.users["ansible-opsA@pve"]["comment"] == "sibling"
    if check_mode:
        api.delete.assert_not_called()
        assert identity in api.users
    else:
        api.delete.assert_called_once_with("access/users/" + encoded)
        assert identity not in api.users
