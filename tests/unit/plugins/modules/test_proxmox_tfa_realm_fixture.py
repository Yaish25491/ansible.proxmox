# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from ansible.module_utils import basic

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_tfa/library/proxmox_tfa_realm_fixture.py"
SPEC = importlib.util.spec_from_file_location("proxmox_tfa_realm_fixture", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
IDENTIFIER = "ansible-tfa-1000020000"


@pytest.fixture
def realm_fixture_environment(pool_environment):
    def create(check_mode=False, **options):
        params = dict(resource_id=IDENTIFIER, owned_ids=[IDENTIFIER], read_only=False)
        params.update(options)
        module, client, api = pool_environment(check_mode=check_mode, **params)
        state = {}

        def get(path, **_kwargs):
            if path == "access/domains":
                return [{"realm": name} for name in state]
            return dict(state[path.rsplit("/", 1)[1]])

        def post(_path, **values):
            state[values["realm"]] = values

        def delete(path, **_values):
            state.pop(path.rsplit("/", 1)[1], None)

        api.get = Mock(side_effect=get)
        api.post = Mock(side_effect=post)
        api.delete = Mock(side_effect=delete)
        return module, client, api, state
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
def test_owned_realm_lifecycle_and_disconnected_creation(realm_fixture_environment, check_mode):
    module, client, api, state = realm_fixture_environment(check_mode=check_mode)
    result = run(module, HELPER.ProxmoxTfaRealmFixtureModule(module, client))
    assert result["changed"] is True
    if check_mode:
        assert state == {}
        api.post.assert_not_called()
        return
    assert api.post.call_args.kwargs["check-connection"] == 0
    assert api.post.call_args.kwargs["server1"] == "192.0.2.1"
    assert "password" not in api.post.call_args.kwargs
    assert run(module, HELPER.ProxmoxTfaRealmFixtureModule(module, client))["changed"] is False
    module.params["state"] = "absent"
    assert run(module, HELPER.ProxmoxTfaRealmFixtureModule(module, client)) == {"changed": True, "fixture": None}
    assert state == {}


@pytest.mark.parametrize("identifier,owned", [("pam", ["pam"]), (IDENTIFIER, []), ("ansible-tfa-bad/name", ["ansible-tfa-bad/name"])])
def test_fixture_ownership_validation_precedes_reads(realm_fixture_environment, identifier, owned):
    module, client, api, _state = realm_fixture_environment(resource_id=identifier, owned_ids=owned)
    handler = HELPER.ProxmoxTfaRealmFixtureModule(module, client)
    with pytest.raises(module.Fail, match="owned fixture"):
        handler.execute()
    api.get.assert_not_called()


def test_read_only_preflight_can_observe_but_not_claim_an_existing_realm(realm_fixture_environment):
    module, client, api, state = realm_fixture_environment(read_only=True)
    state[IDENTIFIER] = {"comment": "Unrelated concurrent owner"}
    result = run(module, HELPER.ProxmoxTfaRealmFixtureModule(module, client))
    assert result["fixture"]["comment"] == "Unrelated concurrent owner"
    api.post.assert_not_called()
    api.delete.assert_not_called()
    module.params["read_only"] = False
    handler = HELPER.ProxmoxTfaRealmFixtureModule(module, client)
    with pytest.raises(module.Fail, match="ownership comment"):
        handler.execute()


@pytest.mark.parametrize("failure", ["create_not_stored", "delete_not_removed"])
def test_fixture_mutations_require_independent_readback(realm_fixture_environment, failure):
    module, client, api, _state = realm_fixture_environment()
    if failure == "create_not_stored":
        api.post.side_effect = None
    else:
        run(module, HELPER.ProxmoxTfaRealmFixtureModule(module, client))
        module.params["state"] = "absent"
        api.delete.side_effect = None
    handler = HELPER.ProxmoxTfaRealmFixtureModule(module, client)
    with pytest.raises(module.Fail, match="could not be verified") as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


def test_fixture_real_argument_contract(monkeypatch):
    monkeypatch.setattr(basic, "_load_params", lambda: {"api_host": "pve.example.com", "resource_id": IDENTIFIER, "owned_ids": [IDENTIFIER]})
    captured = []

    class Handler:
        def __init__(self, module):
            captured.append(module)

        def execute(self):
            return None

    monkeypatch.setattr(HELPER, "ProxmoxTfaRealmFixtureModule", Handler)
    HELPER.main()
    assert captured[0].supports_check_mode is True
    assert captured[0].params["read_only"] is False
