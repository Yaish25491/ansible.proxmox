# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from unittest.mock import Mock, call

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_dns
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_dns import ProxmoxNodeDnsModule


@pytest.mark.parametrize("options", [
    {}, {"search": "example.com"}, {"dns_servers": ["192.0.2.53", "2001:0db8:0:0:0:0:0:53"]},
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_matching_or_omitted_settings_do_not_write(node_environment, options, check_mode):
    module, client, api = node_environment(check_mode=check_mode, **options)
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "dns": api.records["dns"]}
    api.dns.put.assert_not_called()
    api.nodes.get.assert_called_once_with()


@pytest.mark.parametrize("options,expected", [
    ({"search": "new.example.com"}, {"search": "new.example.com", "dns1": "192.0.2.53", "dns2": "2001:db8::53"}),
    ({"search": ""}, {"dns1": "192.0.2.53", "dns2": "2001:db8::53"}),
    ({"dns_servers": []}, {"search": "example.com"}),
    ({"search": "", "dns_servers": []}, {}),
    ({"dns_servers": ["2001:db8::1", "198.51.100.53", "203.0.113.53"]},
     {"search": "example.com", "dns1": "2001:db8::1", "dns2": "198.51.100.53", "dns3": "203.0.113.53"}),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_reconciliation_preserves_omissions_and_predicts_explicit_clears(node_environment, options, expected, check_mode):
    module, client, api = node_environment(check_mode=check_mode, **options)
    original = copy.deepcopy(api.records["dns"])
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": True, "dns": expected}
    if check_mode:
        api.dns.put.assert_not_called()
        assert api.records["dns"] == original
        assert client._changed is False
    else:
        api.dns.put.assert_called_once_with(**dict(expected, search=expected.get("search", "")))
        assert api.records["dns"] == expected
        assert api.nodes.get.call_count == 2
        assert client.validate_pve_permissions.call_args_list == [call(api, "/nodes/pve01"), call(api, "/nodes/pve01")]
        with pytest.raises(module.Exit) as repeated:
            handler.execute()
        assert repeated.value.args[0]["changed"] is False
        assert api.dns.put.call_count == 1


@pytest.mark.parametrize("options,message", [
    ({"node": ""}, "node must not be empty"),
    ({"search": "a b"}, "single domain"), ({"search": "a\nb"}, "single domain"),
    ({"search": "0"}, "cannot be persisted"),
    ({"dns_servers": ["192.0.2.1"] * 4}, "at most three"),
    ({"dns_servers": ["2001:db8::1", "2001:0db8::1"]}, "distinct"),
    ({"dns_servers": ["example.com"]}, "IPv4 or IPv6"),
    ({"dns_servers": [""]}, "IPv4 or IPv6"),
    ({"dns_servers": ["0.0.0.0"]}, "cannot contain"),
    ({"dns_servers": ["fe80::1%eth0"]}, "cannot contain"),
])
def test_invalid_input_fails_before_api_calls(node_environment, options, message):
    module, client, api = node_environment(**options)
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.nodes.get.assert_not_called()
    api.dns.put.assert_not_called()


def test_initially_empty_configuration_is_a_resource_and_can_be_updated(node_environment):
    module, client, api = node_environment(dns={}, dns_servers=["192.0.2.53"])
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": True, "dns": {"dns1": "192.0.2.53"}}
    api.dns.put.assert_called_once_with(search="", dns1="192.0.2.53")


def test_missing_node_fails_without_attempting_creation(node_environment):
    module, client, api = node_environment(node="missing", search="new.example.com")
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Fail, match="does not exist"):
        handler.execute()
    api.dns.get.assert_not_called()
    api.dns.put.assert_not_called()


def test_domain_update_preserves_original_ipv6_spelling_for_exact_restoration(node_environment):
    original = {"search": "example.com", "dns1": "2001:0db8:0:0:0:0:0:0053"}
    module, client, api = node_environment(dns=original, search="new.example.com")
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0]["dns"] == dict(original, search="new.example.com")
    api.dns.put.assert_called_once_with(search="new.example.com", dns1=original["dns1"])


def test_new_server_spelling_matches_prediction_and_readback(node_environment):
    address = "2001:0db8:0:0:0:0:0:0054"
    module, client, api = node_environment(dns_servers=[address], check_mode=True)
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Exit) as predicted:
        handler.execute()
    module.check_mode = False
    with pytest.raises(module.Exit) as actual:
        handler.execute()
    assert predicted.value.args[0] == actual.value.args[0]
    assert api.records["dns"]["dns1"] == address


@pytest.mark.parametrize("stage", ["inventory", "read", "write", "readback", "permissions", "disappeared"])
def test_failures_preserve_only_completed_writes(node_environment, stage):
    module, client, api = node_environment(search="new.example.com")
    error = RuntimeError("API unavailable")
    if stage == "inventory":
        api.nodes.get.side_effect = error
    elif stage == "read":
        api.dns.get.side_effect = error
    elif stage == "write":
        api.dns.put.side_effect = error
    elif stage == "readback":
        api.dns.get.side_effect = [copy.deepcopy(api.records["dns"]), error]
    elif stage == "disappeared":
        api.nodes.get.side_effect = [api.nodes.get.return_value, []]
    else:
        def check_permissions(*_args):
            if client._changed:
                client.fail_request("Permission revoked")
        client.validate_pve_permissions.side_effect = check_permissions
    handler = ProxmoxNodeDnsModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is (stage in ("readback", "permissions", "disappeared"))
    if stage in ("inventory", "read"):
        api.dns.put.assert_not_called()


@pytest.mark.parametrize("options", [{}, {"node": "pve01", "state": "absent"}, {"node": "pve01", "dns_servers": {"dns1": "192.0.2.53"}}])
def test_main_validates_arguments_with_real_ansible_module(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_node_dns.main()
    fail.assert_called_once()


def test_main_uses_shared_constraints_and_resource_handler(monkeypatch, node_environment):
    module, _client, _api = node_environment()
    factory = Mock(return_value=module)
    execute = Mock()
    monkeypatch.setattr(proxmox_node_dns, "AnsibleModule", factory)
    monkeypatch.setattr(ProxmoxNodeDnsModule, "execute", execute)
    proxmox_node_dns.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "task_timeout" in arguments["argument_spec"]
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    execute.assert_called_once()
