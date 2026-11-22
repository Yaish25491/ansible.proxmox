# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_firewall_refs_info


@pytest.fixture
def environment(pool_environment):
    def create(check_mode=False, **options):
        module, client, _api = pool_environment(check_mode=check_mode)
        module.params = {"scope": "cluster", "node": None, "vmid": None}
        module.params.update(options)
        api = Mock()
        client._api = api

        def get(endpoint, **parameters):
            if endpoint == "nodes":
                return [{"node": "pve01"}]
            if endpoint == "cluster/resources":
                return [{"vmid": 100, "type": "qemu", "node": "pve01"}, {"vmid": 101, "type": "lxc", "node": "pve01"}]
            return [{"type": "alias", "name": "test", "ref": "test", "scope": "dc", "comment": "original"}]

        api.get.side_effect = get
        return module, client, api

    return create


def execute(module, client):
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "options,path,parameters",
    [
        ({"scope": "qemu", "node": "pve01", "vmid": 100}, "nodes/pve01/qemu/100/firewall/refs", {}),
        ({"scope": "lxc", "node": "pve01", "vmid": 101}, "nodes/pve01/lxc/101/firewall/refs", {}),
        ({}, "cluster/firewall/refs", {}),
        ({"type": "alias"}, "cluster/firewall/refs", {"type": "alias"}),
        ({"type": "ipset"}, "cluster/firewall/refs", {"type": "ipset"}),
    ],
)
def test_scoped_reads_preserve_results_without_writes(environment, check_mode, options, path, parameters):
    module, client, api = environment(check_mode=check_mode, **options)
    assert execute(module, client) == {"changed": False, "references": [{"type": "alias", "name": "test", "ref": "test", "scope": "dc", "comment": "original"}]}
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
        {"node": "pve01"},
        {"vmid": 100},
        {"type": "bad"},
    ],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_parameters_rejected_before_api(environment, options, check_mode):
    module, client, api = environment(check_mode=check_mode, **options)
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_server_errors_are_not_empty_results(environment, status):
    module, client, api = environment()
    api.get.side_effect = ResourceException(status, "request failed", "server detail")
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["status_code"] == status
    assert "server detail" in result.value.args[0]["api_error"]
    assert result.value.args[0].get("changed", False) is False


def test_permission_rejection_precedes_discovery(environment):
    module, client, api = environment()
    client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("records", [[], [{"type": "alias", "name": "test", "ref": "test", "scope": "dc", "comment": "original"}]])
def test_fresh_observations_preserve_empty_results(environment, records):
    module, client, api = environment()
    api.get.side_effect = [records, [{"type": "alias", "name": "test", "ref": "test", "scope": "dc", "comment": "original"}]]
    assert execute(module, client)["references"] == records
    assert execute(module, client)["references"] == [{"type": "alias", "name": "test", "ref": "test", "scope": "dc", "comment": "original"}]
    assert api.get.call_count == 2
    assert client.validate_pve_permissions.call_count == 2


@pytest.mark.parametrize(
    "options,message",
    [
        ({"scope": "bad"}, "value of scope must be one of"),
        ({"state": "present"}, "Unsupported parameters"),
        ({"vmid": "bad"}, "cannot be converted to an int"),
        ({"api_token_id": "user@pve!token"}, "required together"),
        ({"api_password": "EXAMPLE", "api_token_id": "user@pve!token", "api_token_secret": "EXAMPLE"}, "mutually exclusive"),
    ],
)
def test_real_argument_validation(monkeypatch, options, message):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_firewall_refs_info.main()
    assert message in fail.call_args.kwargs["msg"]


def test_main_uses_information_arguments_and_auth_groups(monkeypatch, environment):
    module, _client, _api = environment()
    factory = Mock(return_value=module)
    run = Mock()
    monkeypatch.setattr(proxmox_firewall_refs_info, "AnsibleModule", factory)
    monkeypatch.setattr(proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule, "execute", run)
    proxmox_firewall_refs_info.main()
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
    assert execute(module, client) == {"changed": False, "references": []}
    assert all("firewall" not in call.args[0] for call in api.get.call_args_list)


@pytest.mark.parametrize("scope,vmid,expected", [("qemu", 100, "/vms/100"), ("lxc", 101, "/vms/101")])
def test_guest_precheck_uses_guest_acl_and_one_discovery(environment, scope, vmid, expected):
    module, client, api = environment(scope=scope, node="pve01", vmid=vmid)
    execute(module, client)
    client.validate_pve_permissions.assert_called_once_with(api, expected)
    assert [call.args[0] for call in api.get.call_args_list].count("nodes") == 1
    assert [call.args[0] for call in api.get.call_args_list].count("cluster/resources") == 1


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("qemu", 100), ("lxc", 101)])
@pytest.mark.parametrize("endpoint", ["nodes", "cluster/resources"])
@pytest.mark.parametrize("records", [None, {}, [None], [{}]])
def test_malformed_discovery_is_not_reported_as_absence(environment, check_mode, scope, vmid, endpoint, records):
    module, client, api = environment(check_mode=check_mode, scope=scope, node="pve01", vmid=vmid)
    original = api.get.side_effect
    api.get.side_effect = lambda path, **parameters: records if path == endpoint else original(path, **parameters)
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail, match="discovery returned"):
        handler.execute()
    assert all("firewall" not in call.args[0] for call in api.get.call_args_list)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("records", [None, {}, [None]])
def test_malformed_reference_response_is_a_structured_failure(environment, check_mode, records):
    module, client, api = environment(check_mode=check_mode)
    api.get.return_value = records
    api.get.side_effect = None
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail, match="invalid reference list"):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("qemu", 100), ("lxc", 101)])
@pytest.mark.parametrize("kind", ["alias", "ipset"])
def test_guest_type_filters_forward_native_parameters(environment, check_mode, scope, vmid, kind):
    module, client, api = environment(check_mode=check_mode, scope=scope, node="pve01", vmid=vmid, type=kind)
    original = api.get.side_effect
    endpoint = "nodes/pve01/{0}/{1}/firewall/refs".format(scope, vmid)
    records = [{"type": kind, "name": "test", "ref": ("+" if kind == "ipset" else "") + "test", "scope": "guest"}]
    api.get.side_effect = lambda path, **parameters: records if path == endpoint else original(path, **parameters)
    assert execute(module, client) == {"changed": False, "references": records}
    api.get.assert_any_call(endpoint, type=kind)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("qemu", 100), ("lxc", 101)])
@pytest.mark.parametrize("stage", ["nodes", "cluster/resources", "firewall/refs"])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_guest_discovery_and_reference_http_failures_are_not_absence(environment, check_mode, scope, vmid, stage, status):
    module, client, api = environment(check_mode=check_mode, scope=scope, node="pve01", vmid=vmid)
    original = api.get.side_effect

    def get(path, **parameters):
        if path.endswith(stage):
            raise ResourceException(status, "request failed", "native guest error")
        return original(path, **parameters)

    api.get.side_effect = get
    handler = proxmox_firewall_refs_info.ProxmoxFirewallRefsInfoModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["status_code"] == status
    assert "native guest error" in failure.value.args[0]["api_error"]
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("cluster", None), ("qemu", 100), ("lxc", 101)])
def test_native_origins_duplicate_names_order_and_extension_fields_are_preserved(environment, check_mode, scope, vmid):
    options = {"scope": scope} if scope == "cluster" else {"scope": scope, "node": "pve01", "vmid": vmid}
    module, client, api = environment(check_mode=check_mode, **options)
    original = api.get.side_effect
    records = [
        {"type": "ipset", "name": "sdn/example", "ref": "+sdn/example", "scope": "sdn", "extension": {"version": 1}},
        {"type": "alias", "name": "duplicate", "ref": "duplicate", "scope": "dc"},
    ]
    if scope != "cluster":
        records.append({"type": "alias", "name": "duplicate", "ref": "duplicate", "scope": "guest", "comment": "guest origin"})
    api.get.side_effect = lambda path, **parameters: records if path.endswith("firewall/refs") else original(path, **parameters)
    assert execute(module, client) == {"changed": False, "references": records}


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("scope,vmid", [("qemu", 100), ("lxc", 101)])
def test_moved_guest_is_rediscovered_without_reading_stale_firewall_endpoint(environment, check_mode, scope, vmid):
    module, client, api = environment(check_mode=check_mode, scope=scope, node="pve01", vmid=vmid)
    original = api.get.side_effect
    moved = False

    def get(path, **parameters):
        if path == "cluster/resources" and moved:
            return [{"vmid": vmid, "type": scope, "node": "pve02"}]
        return original(path, **parameters)

    api.get.side_effect = get
    assert execute(module, client)["references"]
    moved = True
    assert execute(module, client) == {"changed": False, "references": []}
    assert sum("firewall/refs" in call.args[0] for call in api.get.call_args_list) == 1
    assert sum(call.args[0] == "nodes" for call in api.get.call_args_list) == 2
    assert sum(call.args[0] == "cluster/resources" for call in api.get.call_args_list) == 2
