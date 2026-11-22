# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_firewall_log_info


@pytest.fixture
def environment(pool_environment):
    def create(check_mode=False, **options):
        module, client, _api = pool_environment(check_mode=check_mode)
        module.params = {"scope": "node", "node": "pve01", "vmid": None}
        module.params.update(options)
        api = Mock()
        client._api = api

        def get(endpoint, **parameters):
            if endpoint == "nodes":
                return [{"node": "pve01"}]
            if endpoint == "cluster/resources":
                return [{"vmid": 100, "type": "qemu", "node": "pve01"}, {"vmid": 101, "type": "lxc", "node": "pve01"}]
            return [{"n": 51, "t": "  original text é  "}]

        api.get.side_effect = get
        return module, client, api

    return create


def execute(module, client):
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "options,path,parameters",
    [
        ({"scope": "qemu", "node": "pve01", "vmid": 100}, "nodes/pve01/qemu/100/firewall/log", {}),
        ({"scope": "lxc", "node": "pve01", "vmid": 101}, "nodes/pve01/lxc/101/firewall/log", {}),
        ({}, "nodes/pve01/firewall/log", {}),
        ({"start": 0, "limit": 0, "since": 0, "until": 2}, "nodes/pve01/firewall/log", {"start": 0, "limit": 0, "since": 0, "until": 2}),
        ({"limit": 10}, "nodes/pve01/firewall/log", {"limit": 10}),
    ],
)
def test_scoped_reads_preserve_results_without_writes(environment, check_mode, options, path, parameters):
    module, client, api = environment(check_mode=check_mode, **options)
    assert execute(module, client) == {"changed": False, "firewall_log": [{"n": 51, "t": "  original text é  "}]}
    api.get.assert_any_call(path, **parameters)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()
    assert client._changed is False


@pytest.mark.parametrize(
    "options",
    [
        {"scope": "qemu", "node": "pve01"},
        {"scope": "lxc", "vmid": 101, "node": "pve01/path"},
        {"scope": "qemu", "vmid": 99, "node": "pve01"},
        {"scope": "qemu", "vmid": 1000000000, "node": "pve01"},
        {"scope": "qemu", "vmid": 100, "node": ".."},
        {"scope": "other"},
        {"start": -1},
        {"limit": -1},
        {"since": -1},
        {"until": -1},
        {"node": ""},
        {"vmid": 100},
        {"since": 5, "until": 4},
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_parameters_rejected_before_api(environment, options, check_mode):
    module, client, api = environment(check_mode=check_mode, **options)
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_server_errors_are_not_empty_results(environment, status):
    module, client, api = environment()
    api.get.side_effect = ResourceException(status, "request failed", "server detail")
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["status_code"] == status
    assert "server detail" in result.value.args[0]["api_error"]
    assert result.value.args[0].get("changed", False) is False


def test_permission_rejection_precedes_discovery(environment):
    module, client, api = environment()
    client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("records", [[], [{"n": 51, "t": "  original text é  "}]])
def test_fresh_observations_preserve_empty_results(environment, records):
    module, client, api = environment()
    api.get.side_effect = [[{"node": "pve01"}], records, [{"node": "pve01"}], [{"n": 51, "t": "  original text é  "}]]
    assert execute(module, client)["firewall_log"] == records
    assert execute(module, client)["firewall_log"] == [{"n": 51, "t": "  original text é  "}]
    assert api.get.call_count == 4
    assert client.validate_pve_permissions.call_count == 2


@pytest.mark.parametrize(
    "options,message",
    [
        ({"scope": "bad"}, "value of scope must be one of"),
        ({"node": None}, "missing required arguments: node"),
        ({"state": "present"}, "Unsupported parameters"),
        ({"vmid": "bad"}, "cannot be converted to an int"),
        ({"since": "2026-11-01"}, "cannot be converted to an int"),
        ({"api_token_id": "user@pve!token"}, "required together"),
        ({"api_password": "EXAMPLE", "api_token_id": "user@pve!token", "api_token_secret": "EXAMPLE"}, "mutually exclusive"),
    ],
)
def test_real_argument_validation(monkeypatch, options, message):
    arguments = dict({"api_host": "pve.example.com", "node": "pve01"}, **options)
    if options.get("node", "provided") is None:
        arguments.pop("node")
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_firewall_log_info.main()
    assert message in fail.call_args.kwargs["msg"]


def test_main_uses_information_arguments_and_auth_groups(monkeypatch, environment):
    module, _client, _api = environment()
    factory = Mock(return_value=module)
    run = Mock()
    monkeypatch.setattr(proxmox_firewall_log_info, "AnsibleModule", factory)
    monkeypatch.setattr(proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule, "execute", run)
    proxmox_firewall_log_info.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" not in arguments["argument_spec"]
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    run.assert_called_once()


@pytest.mark.parametrize(
    "options",
    [
        {"scope": "qemu", "node": "missing", "vmid": 100},
        {"scope": "qemu", "node": "pve01", "vmid": 999},
        {"scope": "qemu", "node": "pve01", "vmid": 101},
        {"scope": "lxc", "node": "pve01", "vmid": 100},
        {"scope": "qemu", "node": "other", "vmid": 100},
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_missing_or_mislocated_guest_never_reads_firewall(environment, options, check_mode):
    module, client, api = environment(check_mode=check_mode, **options)
    assert execute(module, client) == {"changed": False, "firewall_log": []}
    assert all("firewall" not in call.args[0] for call in api.get.call_args_list)


@pytest.mark.parametrize("scope,vmid,expected", [("qemu", 100, "/vms/100"), ("lxc", 101, "/vms/101")])
def test_guest_precheck_uses_guest_acl_and_one_discovery(environment, scope, vmid, expected):
    module, client, api = environment(scope=scope, node="pve01", vmid=vmid)
    execute(module, client)
    client.validate_pve_permissions.assert_called_once_with(api, expected)
    assert [call.args[0] for call in api.get.call_args_list].count("nodes") == 1
    assert [call.args[0] for call in api.get.call_args_list].count("cluster/resources") == 1


@pytest.mark.parametrize("check_mode", [False, True])
def test_zero_until_is_unbounded_and_does_not_reject_positive_since(environment, check_mode):
    module, client, api = environment(check_mode=check_mode, since=5, until=0)
    result = execute(module, client)
    assert result["changed"] is False
    api.get.assert_any_call("nodes/pve01/firewall/log", since=5, until=0)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid,stage", [
    ("node", None, "nodes"), ("qemu", 100, "nodes"), ("lxc", 101, "nodes"),
    ("qemu", 100, "cluster/resources"), ("lxc", 101, "cluster/resources"),
])
@pytest.mark.parametrize("records", [None, {}, [None], [{}]])
def test_malformed_discovery_is_not_reported_as_absence(environment, check_mode, scope, vmid, stage, records):
    module, client, api = environment(check_mode=check_mode, scope=scope, node="pve01", vmid=vmid)
    original = api.get.side_effect
    api.get.side_effect = lambda path, **parameters: records if path == stage else original(path, **parameters)
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail, match="discovery returned"):
        handler.execute()
    assert all("firewall/log" not in call.args[0] for call in api.get.call_args_list)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("records", [None, {}, [None]])
def test_malformed_log_response_is_a_structured_failure(environment, check_mode, records):
    module, client, api = environment(check_mode=check_mode)
    original = api.get.side_effect
    api.get.side_effect = lambda path, **parameters: records if path.endswith("firewall/log") else original(path, **parameters)
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail, match="invalid firewall log list"):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("node", None), ("qemu", 100), ("lxc", 101)])
@pytest.mark.parametrize("parameters", [
    {}, {"start": 0, "limit": 0, "since": 0, "until": 0},
    {"start": 3, "limit": 2, "since": 5, "until": 0},
    {"since": 0, "until": 5}, {"since": 5, "until": 5},
])
def test_all_scopes_forward_filters_without_pagination_or_normalization(environment, check_mode, scope, vmid, parameters):
    module, client, api = environment(check_mode=check_mode, scope=scope, vmid=vmid, **parameters)
    records = [{"n": 3, "t": "  original text é  ", "extra": True}, {"n": 1, "t": "no content"},
               {"n": 1, "t": "unable to open file - Permission denied"}]
    original = api.get.side_effect
    api.get.side_effect = lambda path, **options: records if path.endswith("firewall/log") else original(path, **options)
    result = execute(module, client)
    assert result == {"changed": False, "firewall_log": records}
    reads = [call for call in api.get.call_args_list if call.args[0].endswith("firewall/log")]
    assert len(reads) == 1
    assert reads[0].kwargs == parameters
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid,stage", [
    ("node", None, "nodes"), ("node", None, "firewall/log"),
    ("qemu", 100, "cluster/resources"), ("qemu", 100, "firewall/log"),
    ("lxc", 101, "cluster/resources"), ("lxc", 101, "firewall/log"),
])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_discovery_and_endpoint_errors_remain_structured(environment, check_mode, scope, vmid, stage, status):
    module, client, api = environment(check_mode=check_mode, scope=scope, vmid=vmid)
    original = api.get.side_effect

    def get(path, **parameters):
        if path.endswith(stage):
            raise ResourceException(status, "request failed", "server detail")
        return original(path, **parameters)

    api.get.side_effect = get
    handler = proxmox_firewall_log_info.ProxmoxFirewallLogInfoModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["status_code"] == status
    assert "server detail" in result.value.args[0]["api_error"]
    assert result.value.args[0].get("changed", False) is False
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("scope,vmid", [("qemu", 100), ("lxc", 101)])
@pytest.mark.parametrize("check_mode", [False, True])
def test_guest_location_is_refreshed_on_each_execution(environment, scope, vmid, check_mode):
    module, client, api = environment(check_mode=check_mode, scope=scope, vmid=vmid)
    api.get.side_effect = [[{"node": "pve01"}], [{"vmid": vmid, "type": scope, "node": "pve01"}], [],
                           [{"node": "pve01"}], [{"vmid": vmid, "type": scope, "node": "other"}]]
    assert execute(module, client) == {"changed": False, "firewall_log": []}
    assert execute(module, client) == {"changed": False, "firewall_log": []}
    assert sum(call.args[0].endswith("firewall/log") for call in api.get.call_args_list) == 1
    assert client.validate_pve_permissions.call_count == 2
