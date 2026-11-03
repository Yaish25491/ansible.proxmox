# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import Mock

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_pool_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_pool_info import ProxmoxPoolInfoModule


@pytest.mark.parametrize("poolid", ["", "a/b/c/d", "a//b", "a/", "/a", "a b", "a\nb", "a?query=x"])
def test_info_rejects_invalid_pool_identifiers_before_reads(pool_environment, poolid):
    module, client, api = pool_environment(poolid=poolid)
    handler = ProxmoxPoolInfoModule(module, client)
    with pytest.raises(module.Fail, match="poolid must contain"):
        handler.get_info()
    assert api.reads == []
    assert api.writes == []
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("poolid", ["a", "legacy_123", "a/b/c", "a.b-c_d", "123"])
def test_info_supports_nested_and_legacy_pool_identifiers(pool_environment, poolid):
    pool = {"poolid": poolid, "members": []}
    module, client, api = pool_environment(poolid=poolid, records={poolid: pool})
    assert ProxmoxPoolInfoModule(module, client).get_info() == [pool]
    assert api.writes == []


def test_info_propagates_inventory_read_failures(pool_environment):
    module, client, api = pool_environment()
    api.get = Mock(side_effect=RuntimeError("server unavailable"))
    handler = ProxmoxPoolInfoModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.get_info()
    assert failure.value.args[0]["api_error"] == "server unavailable"
    assert api.writes == []


@pytest.mark.parametrize("poolid", [None, "applications"])
def test_info_returns_empty_list_for_empty_inventory(pool_environment, poolid):
    module, client, api = pool_environment(poolid=poolid)
    assert ProxmoxPoolInfoModule(module, client).get_info() == []
    assert api.reads == [{}]
    assert api.writes == []


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("poolid,member_type,count", [
    (None, None, 1), ("applications/child", None, 1),
    ("applications/child", "qemu", 1), ("applications/child", "lxc", 1),
    ("applications/child", "storage", 1), ("missing", None, 0),
])
def test_info_lists_selects_filters_and_never_changes(pool_environment, check_mode, poolid, member_type, count):
    records = {"applications/child": {"poolid": "applications/child", "comment": "child", "members": [
        {"vmid": 100, "type": "qemu"}, {"vmid": 101, "type": "lxc"}, {"storage": "local", "type": "storage"},
    ]}}
    module, client, api = pool_environment(records=records, check_mode=check_mode, poolid=poolid, type=member_type)
    handler = ProxmoxPoolInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0]["changed"] is False
    assert len(result.value.args[0]["pools"]) == count
    assert api.writes == []
    if poolid == "applications/child":
        assert len(result.value.args[0]["pools"][0]["members"]) == (1 if member_type else 3)
        if member_type:
            assert result.value.args[0]["pools"][0]["members"][0]["type"] == member_type
        assert api.reads[-1] == dict(poolid=poolid, **({"type": member_type} if member_type else {}))
    elif poolid == "missing":
        assert api.reads == [{}]


def test_permission_failure_is_not_treated_as_empty_inventory(pool_environment):
    module, client, api = pool_environment(poolid=None)
    client.validate_pve_permissions.side_effect = RuntimeError("permission denied")
    handler = ProxmoxPoolInfoModule(module, client)
    with pytest.raises(RuntimeError, match="permission denied"):
        handler.get_info()
    assert api.reads == []


@pytest.mark.parametrize("options", [{"type": "qemu"}, {"state": "present"}, {"type": "unknown", "poolid": "apps"}])
def test_main_rejects_state_and_invalid_or_unscoped_type_filter(monkeypatch, options):
    arguments = dict(api_host="pve.example.com", **options)
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_pool_info.main()
    fail.assert_called_once()


def test_main_runs_info_handler_with_shared_auth_constraints(monkeypatch, pool_environment):
    module, _client, _api = pool_environment()
    factory = Mock(return_value=module)
    monkeypatch.setattr(proxmox_pool_info, "AnsibleModule", factory)
    execute = Mock()
    monkeypatch.setattr(ProxmoxPoolInfoModule, "execute", execute)
    proxmox_pool_info.main()
    spec = factory.call_args.kwargs
    assert spec["supports_check_mode"] is True
    assert "state" not in spec["argument_spec"]
    assert ["api_password", "api_token_id"] in spec["mutually_exclusive"]
    execute.assert_called_once()
