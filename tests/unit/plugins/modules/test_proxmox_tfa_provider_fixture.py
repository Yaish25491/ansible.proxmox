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

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_tfa/library/proxmox_tfa_provider_fixture_info.py"
SPEC = importlib.util.spec_from_file_location("proxmox_tfa_provider_fixture_info", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("marker", [None, "", "x", "x!EXAMPLE-legacy", "EXAMPLE-secret"])
def test_marker_preflight_is_read_only_and_never_returns_keys(pool_environment, check_mode, marker):
    module, client, api = pool_environment(check_mode=check_mode, userid="ansible-provider@pve")
    api.get = Mock(return_value={"keys": marker, "tokens": {"EXAMPLE": "EXAMPLE-secret"}})
    api.post = Mock()
    api.put = Mock()
    api.delete = Mock()
    handler = HELPER.ProviderFixtureInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "fixture": {"userid": "ansible-provider@pve", "modern_tfa": marker == "x"}}
    api.get.assert_called_once_with("access/users/ansible-provider%40pve")
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("userid", ["root@pam", "ordinary@pve", "ansible-bad/name@pve"])
def test_marker_preflight_refuses_other_users_before_api(pool_environment, userid):
    module, client, api = pool_environment(userid=userid)
    api.get = Mock()
    handler = HELPER.ProviderFixtureInfoModule(module, client)
    with pytest.raises(module.Fail, match="designated ansible-"):
        handler.execute()
    api.get.assert_not_called()


def test_marker_preflight_uses_shared_authentication_arguments(monkeypatch):
    monkeypatch.setattr(basic, "_load_params", lambda: {"api_host": "pve.example.com", "userid": "ansible-provider@pve"})
    handler = Mock()
    monkeypatch.setattr(HELPER, "ProviderFixtureInfoModule", handler)
    HELPER.main()
    module = handler.call_args.args[0]
    assert module.supports_check_mode is True
    assert module.argument_spec["api_password"]["no_log"] is True
