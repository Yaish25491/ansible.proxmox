# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic

MODULES = [
    importlib.import_module("ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_" + suffix)
    for suffix in ("alias", "alias_info", "ipset", "ipset_info", "security_group", "security_group_info")
]
HANDLERS = [
    getattr(module, name)
    for module, name in zip(
        MODULES,
        (
            "ProxmoxFirewallAliasModule",
            "ProxmoxFirewallAliasInfoModule",
            "ProxmoxFirewallIpsetModule",
            "ProxmoxFirewallIpsetInfoModule",
            "ProxmoxFirewallSecurityGroupModule",
            "ProxmoxFirewallSecurityGroupInfoModule",
        ),
    )
]
SCOPED = HANDLERS[:4]
INFOS = HANDLERS[1::2]


@pytest.mark.parametrize("handler_class", HANDLERS)
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_firewall_api_errors_are_not_empty_results(firewall_environment, handler_class, check_mode, status):
    module, client, api = firewall_environment(cidr="192.0.2.1", check_mode=check_mode)
    api.get.side_effect = ResourceException(status, "failed", "native firewall error")
    handler = handler_class(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["status_code"] == status
    assert "native firewall error" in failure.value.args[0]["api_error"]
    assert not failure.value.args[0].get("changed", False)
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("handler_class", HANDLERS)
@pytest.mark.parametrize("records", [None, {}, [None], [{}], [{"name": None, "group": None, "cidr": None}]])
def test_firewall_malformed_discovery_cannot_establish_absence(firewall_environment, handler_class, records):
    module, client, api = firewall_environment(cidr="192.0.2.1")
    api.get.side_effect = None
    api.get.return_value = records
    handler = handler_class(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("handler_class", SCOPED)
@pytest.mark.parametrize("stage", ["nodes", "cluster/resources"])
@pytest.mark.parametrize("records", [None, {}, [None], [{}], [{"node": None, "vmid": True, "type": None}]])
def test_firewall_guest_discovery_is_checked_before_reading_configuration(firewall_environment, handler_class, stage, records):
    module, client, api = firewall_environment(scope="qemu", node="pve01", vmid=100, cidr="192.0.2.1")
    original = api._get
    api.get.side_effect = lambda path, **kw: records if path == stage else original(path, **kw)
    handler = handler_class(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    assert all("firewall" not in call.args[0] for call in api.get.call_args_list)


@pytest.mark.parametrize("handler_class", SCOPED)
@pytest.mark.parametrize(
    "parameters",
    [
        {"scope": "bad"},
        {"scope": "cluster", "node": "pve01"},
        {"vmid": 100},
        {"scope": "qemu", "node": "../pve", "vmid": 100},
        {"scope": "lxc", "node": "pve01", "vmid": 99},
        {"scope": "qemu", "node": "pve01", "vmid": None},
    ],
)
def test_firewall_invalid_scope_never_accesses_api(firewall_environment, handler_class, parameters):
    module, client, api = firewall_environment(cidr="192.0.2.1", **parameters)
    handler = handler_class(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.get.assert_not_called()


@pytest.mark.parametrize("handler_class", SCOPED)
@pytest.mark.parametrize(
    "parameters",
    [{"scope": "qemu", "node": "missing", "vmid": 100}, {"scope": "lxc", "node": "pve01", "vmid": 100}, {"scope": "qemu", "node": "pve02", "vmid": 100}],
)
def test_missing_guest_is_empty_only_for_reads_or_already_absent_resources(firewall_environment, firewall_run, handler_class, parameters):
    module, client, api = firewall_environment(cidr="192.0.2.1", **parameters)
    handler = handler_class(module, client)
    if handler_class in INFOS:
        result = firewall_run(handler_class, module, client)
        assert result[handler.result_key] == []
    else:
        with pytest.raises(module.Fail, match="does not exist"):
            handler.execute()
        module.params["state"] = "absent"
        assert firewall_run(handler_class, module, client)["changed"] is False
    assert all("firewall" not in call.args[0] for call in api.get.call_args_list)


@pytest.mark.parametrize("handler_class", HANDLERS)
def test_firewall_permission_rejection_precedes_discovery(firewall_environment, handler_class):
    module, client, api = firewall_environment(cidr="192.0.2.1")
    client.validate_pve_permissions.side_effect = lambda *_: client.fail_request("Permission denied")
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("handler_class", [HANDLERS[0], HANDLERS[2], HANDLERS[4]])
@pytest.mark.parametrize("check_mode", [False, True])
def test_firewall_stale_snapshot_fails_before_mutation_or_prediction(firewall_environment, handler_class, check_mode):
    module, client, api = firewall_environment(cidr="192.0.2.2", digest="stale", check_mode=check_mode)
    api.aliases["cluster/firewall"]["example_net"] = {"cidr": "192.0.2.1"}
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": []}
    api.groups["example_net"] = {"rules": []}
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="digest does not match"):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize(
    "options,message",
    [
        ({"api_token_id": "user@pve!token"}, "required together"),
        ({"api_password": "EXAMPLE", "api_token_id": "user@pve!token", "api_token_secret": "EXAMPLE"}, "mutually exclusive"),
    ],
)
def test_firewall_real_authentication_argument_groups(monkeypatch, module, options, message):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", name="example_net", **options))
    failure = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", failure)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        module.main()
    assert message in failure.call_args.kwargs["msg"]


@pytest.mark.parametrize("module", MODULES[1::2])
def test_firewall_readers_do_not_expose_state(monkeypatch, module):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", state="present"))
    failure = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", failure)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        module.main()
    assert "Unsupported parameters" in failure.call_args.kwargs["msg"]


@pytest.mark.parametrize("handler_class", [HANDLERS[0], HANDLERS[2]])
def test_guest_move_after_a_write_is_not_successful_readback(firewall_environment, handler_class):
    module, client, api = firewall_environment(scope="qemu", node="pve01", vmid=100, state="absent")
    root = "nodes/pve01/qemu/100/firewall"
    api.aliases[root]["example_net"] = {"cidr": "192.0.2.1"}
    api.ipsets[root]["example_net"] = {"entries": []}

    def delete(path, **values):
        api._delete(path, **values)
        api.guests[0]["node"] = "pve02"

    api.delete.side_effect = delete
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="does not exist") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True


@pytest.mark.parametrize("handler_class", [HANDLERS[0], HANDLERS[2], HANDLERS[4]])
@pytest.mark.parametrize("check_mode", [False, True])
def test_firewall_rename_cannot_replace_an_existing_destination(firewall_environment, handler_class, check_mode):
    module, client, api = firewall_environment(cidr="192.0.2.1", rename="taken_name", check_mode=check_mode)
    api.aliases["cluster/firewall"] = {"example_net": {"cidr": "192.0.2.1"}, "taken_name": {"cidr": "192.0.2.2"}}
    api.ipsets["cluster/firewall"] = {"example_net": {"entries": []}, "taken_name": {"entries": []}}
    api.groups = {"example_net": {"rules": []}, "taken_name": {"rules": []}}
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="already exists"):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()


@pytest.mark.parametrize("handler_class", [HANDLERS[0], HANDLERS[2], HANDLERS[4]])
def test_firewall_rename_requires_a_source_or_previously_renamed_destination(firewall_environment, handler_class):
    module, client, api = firewall_environment(cidr="192.0.2.1", rename="missing_name")
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="Neither"):
        handler.execute()
    api.post.assert_not_called()


@pytest.mark.parametrize("handler_class", [HANDLERS[0], HANDLERS[2], HANDLERS[4]])
def test_firewall_ignored_delete_fails_readback_with_changed_preserved(firewall_environment, handler_class):
    module, client, api = firewall_environment(state="absent")
    api.aliases["cluster/firewall"]["example_net"] = {"cidr": "192.0.2.1"}
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": []}
    api.groups["example_net"] = {"rules": []}
    api.delete.side_effect = None
    handler = handler_class(module, client)
    with pytest.raises(module.Fail, match="deletion could not be verified") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
