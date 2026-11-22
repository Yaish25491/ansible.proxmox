# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_acl/library/proxmox_acl_fixture.py"
SPEC = importlib.util.spec_from_file_location("proxmox_acl_fixture", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def execute(module, client):
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.fixture
def environment(identity_environment):
    def create(check_mode=False):
        identifier = "ansible-acl-1000020000-token"
        module, client, api = identity_environment(
            resource_id=identifier, owned_ids=[identifier], userid="ansible-acl-1000020000@pve", read_only=False, check_mode=check_mode
        )
        api.users[module.params["userid"]] = {"enable": 0, "comment": "Owned ACL integration fixture"}
        return module, client, api

    return create


def test_owned_fixture_lifecycle_and_check_mode(environment):
    module, client, api = environment(check_mode=True)
    assert execute(module, client)["changed"] is True
    api.post.assert_not_called()
    module.check_mode = False
    created = execute(module, client)
    assert created["changed"] is True
    assert created["fixture"] is not None
    assert "value" not in created["fixture"]
    assert execute(module, client)["changed"] is False
    module.params["read_only"] = True
    assert execute(module, client)["changed"] is False
    module.params.update(read_only=False, state="absent")
    module.check_mode = True
    assert execute(module, client)["changed"] is True
    api.delete.assert_not_called()
    module.check_mode = False
    assert execute(module, client) == {"changed": True, "fixture": None}
    assert execute(module, client)["changed"] is False


def test_unowned_fixture_never_reaches_api(environment):
    module, client, api = environment()
    module.params["owned_ids"] = []
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="Refusing"):
        handler.execute()
    api.get.assert_not_called()
    api.post.assert_not_called()


def test_token_requires_owned_existing_parent(environment):
    module, client, api = environment()
    api.users.clear()
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="Create the owned user"):
        handler.execute()
    api.post.assert_not_called()
    module.params["userid"] = "root@pam"
    with pytest.raises(module.Fail, match="owned ACL fixture"):
        handler.execute()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"comment": "unrelated"}, {"enable": 1}, {"enable": None}])
def test_unowned_or_enabled_parent_refuses_token_mutations(environment, check_mode, options):
    module, client, api = environment(check_mode=check_mode)
    api.users[module.params["userid"]].update(options)
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="ownership or disabled state"):
        handler.execute()
    api.post.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_mismatched_parent_refuses_before_api(environment, check_mode):
    module, client, api = environment(check_mode=check_mode)
    module.params["userid"] = "ansible-acl-1999929999@pve"
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="matching owned"):
        handler.execute()
    api.get.assert_not_called()
    api.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_missing_parent_refuses_creation_even_in_check_mode(environment, check_mode):
    module, client, api = environment(check_mode=check_mode)
    api.users.clear()
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail, match="Create the owned user"):
        handler.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("read_only", [False, True])
def test_missing_parent_is_absent_for_cleanup_and_reads(environment, read_only):
    module, client, api = environment()
    api.users.clear()
    module.params.update(state="absent", read_only=read_only)
    assert execute(module, client) == {"changed": False, "fixture": None}
    api.delete.assert_not_called()


def test_changed_retained_when_parent_disappears_after_token_creation(environment):
    module, client, api = environment()
    original = api._post

    def create(path, **parameters):
        original(path, **parameters)
        api.users.clear()

    api.post.side_effect = create
    handler = HELPER.FixtureModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
