# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import ipaddress
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_network as action
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_network_info as info


@pytest.fixture
def environment(pool_environment):
    def make(check_mode=False, records=None, **options):
        module, client, _api = pool_environment(check_mode=check_mode)
        module.params = dict(node="pve01", iface="vmbr100", state="present", reset=[], **{})
        module.params.update(options)
        api = Mock()
        api.records = copy.deepcopy(records if records is not None else [])
        api.physical = {entry["iface"]: dict(entry, priority=None, method="manual", method6="manual")
                        for entry in api.records if entry.get("exists") and entry["type"] == "eth"}

        def get(path, **parameters):
            if path == "nodes":
                return [{"node": "pve01"}]
            result = copy.deepcopy(api.records)
            for entry in result:
                for suffix in ("", "6"):
                    address, mask = "address" + suffix, "netmask" + suffix
                    if entry.get(address) and entry.get(mask) is not None:
                        network = ipaddress.ip_interface("{0}/{1}".format(entry[address], entry[mask]))
                        entry["cidr" + suffix] = "{0}/{1}".format(network.ip, network.network.prefixlen)
            if parameters.get("type"):
                result = [entry for entry in result if entry["type"] == parameters["type"]]
            return result

        def write(path, **parameters):
            name = parameters.pop("iface", None) or path.rsplit("/", 1)[1]
            current = next((entry for entry in api.records if entry["iface"] == name), None)
            if current is None:
                current = {"iface": name}
                api.records.append(current)
            for key in parameters.pop("delete", "").split(","):
                current.pop(key, None)
            current.update(parameters)
            # Native writer falls back to its computed CIDR alias if only address/mask were deleted.
            for suffix in ("", "6"):
                value = current.pop("cidr" + suffix, None)
                if "address" + suffix not in current and value:
                    ip, prefix = value.split("/")
                    current["address" + suffix], current["netmask" + suffix] = ip, int(prefix)
            # Native API derives methods from the submitted addresses, not the old record.
            current["method"] = "static" if parameters.get("address") else "manual"
            current["method6"] = "static" if parameters.get("address6") else "manual"
            current["priority"] = 2
            if current.get("comments"):
                current["comments"] = current["comments"].rstrip() + "\n"
            if current["type"] == "bridge":
                current.setdefault("bridge_stp", "off")
                # Native writer enables VLAN awareness whenever the field is defined, even if the value is zero.
                if "bridge_vlan_aware" in current:
                    current["bridge_vlan_aware"] = 1
                    current.setdefault("bridge_vids", "2-4094")
                else:
                    current.pop("bridge_vids", None)
            return None

        def delete(path):
            name = path.rsplit("/", 1)[1]
            api.records = [entry for entry in api.records if entry["iface"] != name]
            if name in api.physical:
                api.records.append(copy.deepcopy(api.physical[name]))

        api.get.side_effect = get
        api.post.side_effect = write
        api.put.side_effect = write
        api.delete.side_effect = delete
        client._api = api
        return module, client, api
    return make


def execute(module, client, handler=action.ProxmoxNodeNetworkModule):
    subject = handler(module, client)
    with pytest.raises(module.Exit) as result:
        subject.execute()
    return result.value.args[0]


def bridge(**options):
    return dict(iface="vmbr100", type="bridge", priority=2, method="static", method6="static",
                address="192.0.2.1", netmask="255.255.255.0", address6="2001:0db8::1", netmask6=64,
                cidr="192.0.2.1/24", cidr6="2001:db8::1/64", comments="owned\n", bridge_ports="eno2 eno1", bridge_stp="on", bridge_vlan_aware=1, **options)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("kind,options", [
    ("bridge", {"bridge_ports": [], "bridge_vlan_aware": True}),
    ("bond", {"slaves": [], "bond_mode": "active-backup"}),
    ("vlan", {"vlan_raw_device": "eno1", "vlan_id": 42}),
])
def test_create_and_repeat_preserves_native_noop(environment, check_mode, kind, options):
    module, client, api = environment(check_mode=check_mode, records=[{"iface": "eno1", "type": "eth", "exists": 1}],
                                      iface="vlan42" if kind == "vlan" else ("bond100" if kind == "bond" else "vmbr100"), type=kind,
                                      autostart=False, **options)
    result = execute(module, client)
    assert result["changed"] is True
    assert result["interface"]["type"] == kind
    api.put.assert_not_called()
    api.delete.assert_not_called()
    if check_mode:
        api.post.assert_not_called()
        assert len(api.records) == 1
    else:
        assert execute(module, client)["changed"] is False
        api.post.assert_called_once()
        assert api.get.call_count == 6
        assert client.validate_pve_permissions.call_count == 6


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{}, {"cidr": "192.0.2.1/24", "cidr6": "2001:db8::1/64"},
                                     {"bridge_ports": ["eno1", "eno2"]}, {"comments": "owned"}, {"autostart": False}])
def test_equivalent_and_omitted_values_never_write(environment, check_mode, options):
    module, client, api = environment(check_mode=check_mode, records=[bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}], **options)
    assert execute(module, client)["changed"] is False
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"comments": "new"}, {"mtu": 1500}, {"bridge_ports": []}, {"bridge_vlan_aware": False}])
def test_partial_update_carries_both_ip_families_and_preserves_stp(environment, check_mode, options):
    module, client, api = environment(check_mode=check_mode, records=[bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}], **options)
    original = copy.deepcopy(api.records)
    result = execute(module, client)
    assert result["changed"] is True
    assert result["interface"]["address"] == "192.0.2.1"
    assert result["interface"]["address6"] == "2001:0db8::1"
    assert result["interface"]["bridge_stp"] == "on"
    if check_mode:
        api.put.assert_not_called()
        assert api.records == original
    else:
        assert api.put.call_args.kwargs["address"] == "192.0.2.1"
        assert api.put.call_args.kwargs["netmask"] == "255.255.255.0"
        assert api.put.call_args.kwargs["address6"] == "2001:0db8::1"
        assert api.put.call_args.kwargs["netmask6"] == 64
        assert result["interface"]["method"] == "static"
        assert result["interface"]["method6"] == "static"
        assert execute(module, client)["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options,missing", [
    ({"cidr": ""}, ["address", "netmask"]),
    ({"address6": ""}, ["address6", "netmask6"]),
    ({"reset": ["address", "address6", "comments"]}, ["address", "netmask", "address6", "netmask6", "comments"]),
])
def test_explicit_address_clears_and_resets_preserve_other_settings(environment, check_mode, options, missing):
    module, client, api = environment(check_mode=check_mode, records=[bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}], **options)
    result = execute(module, client)
    assert result["changed"] is True
    assert all(key not in result["interface"] for key in missing)
    assert result["interface"]["bridge_stp"] == "on"
    if not check_mode:
        assert execute(module, client)["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("kind", ["bridge", "bond", "vlan", "eth"])
def test_delete_and_absent_noop_match_configuration_not_hardware(environment, check_mode, kind):
    record = {"iface": "eno1" if kind == "eth" else "vmbr100", "type": kind, "priority": 2}
    if kind == "eth":
        record["exists"] = 1
    module, client, api = environment(check_mode=check_mode, records=[record], iface=record["iface"], state="absent")
    assert execute(module, client) == {"changed": True, "interface": None}
    if check_mode:
        api.delete.assert_not_called()
    else:
        assert execute(module, client) == {"changed": False, "interface": None}
        api.delete.assert_called_once()
        if kind == "eth":
            assert api.records[0]["exists"] == 1


@pytest.mark.parametrize("options,message", [
    ({"iface": "../bad"}, "valid non-loopback"), ({"iface": "lo"}, "valid non-loopback"), ({"iface": "a"}, "valid non-loopback"),
    ({"iface": "vmbr" + "1" * 17}, "valid non-loopback"), ({"node": "a/b"}, "node must"),
    ({"bridge_ports": ["eno1", "eno1"]}, "distinct"), ({"bridge_ports": ["vmbr100"]}, "distinct"),
    ({"mtu": 1279}, "mtu must"), ({"vlan_id": 4095}, "vlan_id must"), ({"netmask6": 129}, "netmask6 must"),
    ({"reset": ["address"], "cidr": "192.0.2.1/24"}, "reset cannot"), ({"comments": "${bad}"}, "template directives"),
    ({"gateway": "1.2.3.4\npre-up bad"}, "line breaks"), ({"cidr": "bad"}, "does not appear"),
    ({"gateway6": "192.0.2.1"}, "IPv6"), ({"address": "192.0.2.2"}, "required together"),
    ({"type": "bridge", "slaves": []}, "bond settings"), ({"type": "eth"}, "existing discovered"),
    ({"type": "vlan", "iface": "eno1.42", "vlan_id": 43, "vlan_raw_device": "eno1"}, "must agree"),
    ({"type": "vlan", "iface": "vlan42", "vlan_id": 43, "vlan_raw_device": "eno1"}, "must agree"),
    ({"type": "vlan", "iface": "vlan42"}, "existing parent"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_requests_never_mutate(environment, options, message, check_mode):
    module, client, api = environment(check_mode=check_mode, **dict({"type": "bridge"}, **options))
    handler = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("method", ["dhcp", "auto", "loopback"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_unsupported_address_methods_fail_before_partial_updates(environment, method, check_mode):
    record = bridge()
    record["method6"] = method
    module, client, api = environment(check_mode=check_mode,
                                      records=[record, {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}], comments="new")
    handler = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="Cannot safely update"):
        handler.execute()
    api.put.assert_not_called()


@pytest.mark.parametrize("reference", [{"iface": "vmbr200", "type": "bridge", "bridge_ports": "vmbr100"},
                                       {"iface": "bond200", "type": "bond", "slaves": "vmbr100"},
                                       {"iface": "vmbr100.42", "type": "vlan"}])
@pytest.mark.parametrize("check_mode", [False, True])
def test_dependent_interfaces_prevent_deletion(environment, reference, check_mode):
    module, client, api = environment(check_mode=check_mode, records=[bridge(), reference], state="absent")
    handler = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="referenced by"):
        handler.execute()
    api.delete.assert_not_called()


@pytest.mark.parametrize("handler", [action.ProxmoxNodeNetworkModule, info.ProxmoxNodeNetworkInfoModule])
@pytest.mark.parametrize("stage", ["nodes", "network"])
@pytest.mark.parametrize("bad", [None, {}, [None], [{}], [{"iface": [], "type": "eth"}]])
def test_malformed_discovery_is_not_absence(environment, handler, stage, bad):
    module, client, api = environment()
    original = api.get.side_effect
    api.get.side_effect = lambda path, **kw: bad if path.endswith(stage) else original(path, **kw)
    subject = handler(module, client)
    with pytest.raises(module.Fail, match="invalid"):
        subject.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("handler", [action.ProxmoxNodeNetworkModule, info.ProxmoxNodeNetworkInfoModule])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
@pytest.mark.parametrize("stage", ["nodes", "network"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_endpoint_failures_remain_structured(environment, handler, status, stage, check_mode):
    module, client, api = environment(check_mode=check_mode)
    original = api.get.side_effect

    def get(path, **kw):
        if path.endswith(stage):
            raise ResourceException(status, "failed", "native detail")
        return original(path, **kw)

    api.get.side_effect = get
    subject = handler(module, client)
    with pytest.raises(module.Fail) as failure:
        subject.execute()
    assert failure.value.args[0]["status_code"] == status
    assert "native detail" in failure.value.args[0]["api_error"]
    api.post.assert_not_called()


@pytest.mark.parametrize("stage", ["write", "readback", "permission", "disappeared"])
def test_post_write_errors_retain_completed_change(environment, stage):
    module, client, api = environment(type="bridge", bridge_ports=[])
    if stage == "write":
        api.post.side_effect = RuntimeError("write failed")
    elif stage == "readback":
        api.get.side_effect = [[{"node": "pve01"}], [], RuntimeError("read failed")]
    elif stage == "disappeared":
        api.get.side_effect = [[{"node": "pve01"}], [], [{"node": "pve01"}], []]
    else:
        client.validate_pve_permissions.side_effect = lambda *_: client.fail_request("permission revoked") if client._changed else None
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail) as failure:
        subject.execute()
    assert failure.value.args[0].get("changed", False) is (stage != "write")


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"iface": None}, {"iface": "vmbr100"}, {"iface": "missing"}, {"iface": None, "type": "eth"}])
def test_info_reads_filter_and_preserve_native_fields(environment, check_mode, options):
    records = [bridge(), {"iface": "eno1", "type": "eth", "exists": 1, "extra": "native"}]
    module, client, api = environment(check_mode=check_mode, records=records, **options)
    result = execute(module, client, info.ProxmoxNodeNetworkInfoModule)
    expected = [entry for entry in records if (options.get("iface") is None or entry["iface"] == options["iface"])
                and (options.get("type") is None or entry["type"] == options["type"])]
    assert result == {"changed": False, "interfaces": expected}
    api.get.assert_any_call("nodes/pve01/network", **({"type": options["type"]} if options.get("type") else {}))
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("module", [action, info])
@pytest.mark.parametrize("options,message", [
    ({"node": None}, "missing required"), ({"type": "wrong"}, "must be one of"),
    ({"stp": True}, "Unsupported parameters"), ({"apply": True}, "Unsupported parameters"),
    ({"api_token_id": "user@pve!token"}, "required together"),
])
def test_real_argument_validation(monkeypatch, module, options, message):
    args = dict(api_host="pve.example.com", node="pve01", iface="vmbr100", **{})
    args.update(options)
    if args["node"] is None:
        args.pop("node")
    monkeypatch.setattr(basic, "_load_params", lambda: args)
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        module.main()
    assert message in fail.call_args.kwargs["msg"]


@pytest.mark.parametrize("module,handler", [(action, "ProxmoxNodeNetworkModule"), (info, "ProxmoxNodeNetworkInfoModule")])
def test_main_reuses_shared_validation_and_handlers(monkeypatch, environment, module, handler):
    fake, _client, _api = environment()
    factory = Mock(return_value=fake)
    run = Mock()
    monkeypatch.setattr(module, "AnsibleModule", factory)
    monkeypatch.setattr(getattr(module, handler), "execute", run)
    module.main()
    assert factory.call_args.kwargs["supports_check_mode"] is True
    assert ["api_token_id", "api_token_secret"] in factory.call_args.kwargs["required_together"]
    run.assert_called_once()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_ethernet_configuration_is_updated_without_creating_hardware(environment, check_mode, configured):
    record = {"iface": "eno1", "type": "eth", "exists": 1, "method": "manual", "method6": "manual"}
    if configured:
        record["priority"] = 2
    module, client, api = environment(check_mode=check_mode, records=[record], iface="eno1", type="eth", cidr="192.0.2.5/24")
    result = execute(module, client)
    assert result["changed"] is True
    assert result["interface"]["address"] == "192.0.2.5"
    api.post.assert_not_called()
    if check_mode:
        api.put.assert_not_called()
    else:
        assert result["interface"]["exists"] == 1
        assert execute(module, client)["changed"] is False


@pytest.mark.parametrize("options,message", [
    ({"comments": "0"}, "cannot be persisted"),
    ({"type": "bond", "iface": "custombond"}, "bond followed by digits"),
    ({"cidr6": "fe80::1%eno1/64"}, "unscoped"),
    ({"type": "vlan", "iface": "vlan100", "vlan_raw_device": "missing"}, "existing parent"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_unpersistable_values_fail_before_writes(environment, options, message, check_mode):
    module, client, api = environment(check_mode=check_mode, **dict({"type": "bridge"}, **options))
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match=message):
        subject.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_bond_primary_maps_to_native_and_slave_order_is_not_a_change(environment, check_mode):
    records = [{"iface": "bond100", "type": "bond", "priority": 2, "method": "manual", "method6": "manual",
                "slaves": "eno2 eno1", "bond_mode": "active-backup", "bond-primary": "eno1"},
               {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}]
    module, client, api = environment(check_mode=check_mode, records=records, iface="bond100", slaves=["eno1", "eno2"], bond_primary="eno1")
    assert execute(module, client)["changed"] is False
    module.params["bond_primary"] = "eno2"
    assert execute(module, client)["changed"] is True
    if check_mode:
        api.put.assert_not_called()
    else:
        assert api.put.call_args.kwargs == {"bond-primary": "eno2"}


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [{"iface": "eno1.42", "vlan_raw_device": "eno1", "vlan_id": 42},
                                     {"iface": "customvlan", "vlan_raw_device": "eno1", "vlan_id": 42}])
def test_dotted_and_custom_vlan_configuration_uses_exact_native_mapping(environment, check_mode, options):
    module, client, api = environment(check_mode=check_mode, records=[{"iface": "eno1", "type": "eth"}], type="vlan", **options)
    assert execute(module, client)["changed"] is True
    if check_mode:
        api.post.assert_not_called()
    else:
        assert api.post.call_args.kwargs["vlan-raw-device"] == "eno1"
        assert api.post.call_args.kwargs["vlan-id"] == 42


@pytest.mark.parametrize("stage", ["put", "delete"])
@pytest.mark.parametrize("after_write", [False, True])
def test_update_and_delete_failures_keep_only_completed_changes(environment, stage, after_write):
    module, client, api = environment(records=[bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}],
                                      state="absent" if stage == "delete" else "present", comments="new")
    if after_write:
        original = api.get.side_effect

        def get(path, **parameters):
            if client._changed:
                raise RuntimeError("readback failed")
            return original(path, **parameters)

        api.get.side_effect = get
    else:
        getattr(api, stage).side_effect = RuntimeError("write failed")
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail) as failure:
        subject.execute()
    assert failure.value.args[0].get("changed", False) is after_write


@pytest.mark.parametrize("handler", [action.ProxmoxNodeNetworkModule, info.ProxmoxNodeNetworkInfoModule])
@pytest.mark.parametrize("check_mode", [False, True])
def test_permission_rejection_precedes_discovery(environment, handler, check_mode):
    module, client, api = environment(check_mode=check_mode)
    client.validate_pve_permissions.side_effect = lambda *_: client.fail_request("Permission denied")
    subject = handler(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        subject.execute()
    api.get.assert_not_called()


def test_info_does_not_cache_observations_across_calls(environment):
    module, client, api = environment(records=[bridge()])
    assert execute(module, client, info.ProxmoxNodeNetworkInfoModule)["interfaces"][0]["comments"] == "owned\n"
    api.records = []
    assert execute(module, client, info.ProxmoxNodeNetworkInfoModule)["interfaces"] == []
    assert api.get.call_count == 4


@pytest.mark.parametrize("check_mode", [False, True])
def test_disabling_vlan_awareness_deletes_native_flag_instead_of_sending_zero(environment, check_mode):
    record = bridge(bridge_vids="100-200")
    module, client, api = environment(check_mode=check_mode, records=[record, {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}],
                                      bridge_vlan_aware=False)
    result = execute(module, client)
    assert result["changed"] is True
    assert "bridge_vlan_aware" not in result["interface"]
    assert "bridge_vids" not in result["interface"]
    if check_mode:
        api.put.assert_not_called()
    else:
        assert "bridge_vlan_aware" not in api.put.call_args.kwargs
        assert "bridge_vlan_aware" in api.put.call_args.kwargs["delete"].split(",")
        assert execute(module, client)["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
def test_native_allowed_vlan_default_reset_is_idempotent(environment, check_mode):
    record = bridge(bridge_vids="2-4094")
    module, client, api = environment(check_mode=check_mode, records=[record, {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}],
                                      reset=["bridge_vids"])
    assert execute(module, client)["changed"] is False
    api.put.assert_not_called()


@pytest.mark.parametrize("value", ["0", "4095", "200-100", "bad", "2,0"])
def test_invalid_allowed_vlan_ranges_are_rejected(environment, value):
    module, client, api = environment(type="bridge", bridge_vlan_aware=True, bridge_vids=value)
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="VLAN IDs or ranges"):
        subject.execute()
    api.post.assert_not_called()


def test_clearing_address_uses_cidr_alias_to_prevent_native_writer_restoration(environment):
    module, client, api = environment(records=[bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}], cidr6="")
    result = execute(module, client)
    assert "address6" not in result["interface"]
    assert "cidr6" in api.put.call_args.kwargs["delete"].split(",")
    assert "cidr6" not in result["interface"]


@pytest.mark.parametrize("check_mode", [False, True])
def test_ports_cannot_be_taken_from_another_master(environment, check_mode):
    records = [{"iface": "eno1", "type": "eth"}, {"iface": "vmbr200", "type": "bridge", "bridge_ports": "eno1"}]
    module, client, api = environment(check_mode=check_mode, records=records, type="bridge", bridge_ports=["eno1"])
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="already assigned"):
        subject.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_local_bridge_visibility_permission_is_required_before_discovery(environment, check_mode):
    module, client, api = environment(check_mode=check_mode, type="bridge")

    def validate(_api, path):
        if path == "/sdn/zones/localnetwork":
            client.fail_request("Local bridge visibility denied")

    client.validate_pve_permissions.side_effect = validate
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="Local bridge visibility denied"):
        subject.execute()
    api.get.assert_not_called()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("options,expected", [
    ({"cidr": "192.0.2.9/25"}, {"cidr": "192.0.2.9/25", "method": "static", "cidr6": "2001:db8::1/64", "method6": "static"}),
    ({"cidr6": ""}, {"cidr": "192.0.2.1/24", "method": "static", "method6": "manual"}),
    ({"reset": ["address", "address6"]}, {"method": "manual", "method6": "manual"}),
])
def test_check_mode_predicts_consistent_computed_addresses_and_methods(environment, options, expected):
    records = [bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}]
    module, client, api = environment(check_mode=True, records=records, **options)
    predicted = execute(module, client)["interface"]
    for key, value in expected.items():
        assert predicted[key] == value
    for suffix in ("", "6"):
        if predicted["method" + suffix] == "manual":
            assert "cidr" + suffix not in predicted
    assert api.records == records
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("value", ["4 2-3", "2,3,4", "2-4 3", "002-004"])
def test_equivalent_allowed_vlan_sets_do_not_write(environment, check_mode, value):
    record = bridge(bridge_vids="2-4")
    records = [record, {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}]
    module, client, api = environment(check_mode=check_mode, records=records, bridge_vids=value)
    assert execute(module, client)["changed"] is False
    api.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("records,options", [
    ([{"iface": "vmbr100", "type": "bridge", "priority": 2},
      {"iface": "vmbr100.42", "type": "vlan", "vlan-raw-device": "vmbr100", "vlan-id": 42}],
     {"bridge_ports": ["vmbr100.42"]}),
    ([{"iface": "vlan100", "type": "vlan", "vlan-raw-device": "eno1", "vlan-id": 100, "priority": 2},
      {"iface": "vlan200", "type": "vlan", "vlan-raw-device": "vlan100", "vlan-id": 200},
      {"iface": "eno1", "type": "eth"}],
     {"iface": "vlan100", "vlan_raw_device": "vlan200"}),
])
def test_transitive_interface_dependency_cycles_fail_before_writes(environment, check_mode, records, options):
    module, client, api = environment(check_mode=check_mode, records=records, **options)
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="dependency cycle"):
        subject.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("invalid", [{"autostart": None}, {"bridge_vlan_aware": []}, {"vlan-id": {"bad": 1}}])
def test_invalid_consumed_server_fields_are_structured_failures(environment, check_mode, invalid):
    record = bridge()
    record.update(invalid)
    module, client, api = environment(check_mode=check_mode, records=[record], bridge_ports=[], comments="new")
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="invalid"):
        subject.execute()
    api.put.assert_not_called()


@pytest.mark.parametrize("handler", [action.ProxmoxNodeNetworkModule, info.ProxmoxNodeNetworkInfoModule])
@pytest.mark.parametrize("bad", [[{"iface": "", "type": "eth"}], [{"iface": "eno1", "type": ""}]])
def test_empty_server_identifiers_cannot_masquerade_as_missing_resources(environment, handler, bad):
    module, client, api = environment(records=bad, type="bridge" if handler is action.ProxmoxNodeNetworkModule else None)
    subject = handler(module, client)
    with pytest.raises(module.Fail, match="invalid"):
        subject.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("options", [{"cidr": "192.0.2.9/25"}, {"cidr6": ""}, {"reset": ["address", "address6"]}])
def test_predicted_address_metadata_agrees_with_fresh_native_style_readback(environment, options):
    records = [bridge(), {"iface": "eno1", "type": "eth"}, {"iface": "eno2", "type": "eth"}]
    module, client, api = environment(check_mode=True, records=records, **options)
    predicted = execute(module, client)["interface"]
    module.check_mode = False
    actual = execute(module, client)["interface"]
    for key in ("cidr", "cidr6", "method", "method6"):
        assert predicted.get(key) == actual.get(key)
    api.put.assert_called_once()


@pytest.mark.parametrize("handler", [action.ProxmoxNodeNetworkModule, info.ProxmoxNodeNetworkInfoModule])
@pytest.mark.parametrize("iface", ["en\u00e91", "eno\u0661", "eno1.\u0664\u0662"])
def test_interface_shorthand_keeps_native_ascii_validation(environment, handler, iface):
    module, client, api = environment(iface=iface)
    subject = handler(module, client)
    with pytest.raises(module.Fail, match="iface must"):
        subject.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_vlan_range_shorthand_does_not_accept_unicode_digits(environment, check_mode):
    module, client, api = environment(check_mode=check_mode, type="bridge", bridge_vlan_aware=True, bridge_vids="\u0662-\u0664")
    subject = action.ProxmoxNodeNetworkModule(module, client)
    with pytest.raises(module.Fail, match="VLAN IDs or ranges"):
        subject.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
