# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import Mock, call

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_info import ProxmoxNodeInfoModule


@pytest.mark.parametrize("subsets", [None, ["all"], ["dns"], ["hosts"], ["time"], ["status"], [], ["dns", "time", "dns"]])
@pytest.mark.parametrize("check_mode", [False, True])
def test_selected_node_reads_only_requested_subsets(node_environment, subsets, check_mode):
    module, client, api = node_environment(gather_subset=subsets, check_mode=check_mode)
    handler = ProxmoxNodeInfoModule(module, client)
    selected = handler.subsets if subsets is None or "all" in subsets else subsets
    expected = {"node": "pve01", "summary": api.nodes.get.return_value[0]}
    expected.update({key: api.records[key] for key in selected})
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "nodes": [expected]}
    assert api.get.call_args_list == [call("nodes/pve01/{0}".format(key)) for key in handler.subsets if key in selected]
    api.nodes.get.assert_called_once_with()
    permissions = [call(api, "/nodes/pve01")]
    if "hosts" in selected:
        permissions.append(call(api, "/"))
    assert client.validate_pve_permissions.call_args_list == permissions
    api.dns.put.assert_not_called()


def test_unselected_nodes_returns_summaries_without_per_node_reads(node_environment):
    module, client, api = node_environment(node=None)
    handler = ProxmoxNodeInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "nodes": [
        {"node": item["node"], "summary": item} for item in api.nodes.get.return_value
    ]}
    api.get.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


def test_missing_selected_node_returns_empty_list(node_environment):
    module, client, api = node_environment(node="missing")
    handler = ProxmoxNodeInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "nodes": []}
    api.get.assert_not_called()


@pytest.mark.parametrize("options,message", [({"node": None, "gather_subset": ["dns"]}, "requires node"), ({"node": ""}, "must not be empty")])
def test_invalid_selection_fails_before_connecting(node_environment, options, message):
    module, client, api = node_environment(**options)
    handler = ProxmoxNodeInfoModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.nodes.get.assert_not_called()


@pytest.mark.parametrize("stage", ["inventory", "subset", "permission"])
def test_read_failures_are_reported_instead_of_empty_results(node_environment, stage):
    module, client, api = node_environment()
    if stage == "inventory":
        api.nodes.get.side_effect = RuntimeError("offline")
    elif stage == "subset":
        api.get.side_effect = RuntimeError("offline")
    else:
        client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("permission denied")
    handler = ProxmoxNodeInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.dns.put.assert_not_called()


@pytest.mark.parametrize("options", [{"state": "present"}, {"gather_subset": ["syslog"]}])
def test_main_rejects_state_and_unknown_subsets(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_node_info.main()
    fail.assert_called_once()


def test_main_uses_shared_info_contract(monkeypatch, node_environment):
    module, _client, _api = node_environment()
    factory = Mock(return_value=module)
    execute = Mock()
    monkeypatch.setattr(proxmox_node_info, "AnsibleModule", factory)
    monkeypatch.setattr(ProxmoxNodeInfoModule, "execute", execute)
    proxmox_node_info.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" not in arguments["argument_spec"]
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    execute.assert_called_once()
