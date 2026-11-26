# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy

import pytest
from proxmoxer.core import ResourceException

from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_alias import ProxmoxFirewallAliasModule as Action
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_alias_info import ProxmoxFirewallAliasInfoModule as Info


@pytest.mark.parametrize(
    "scope,node,vmid,root",
    [
        ("cluster", None, None, "cluster/firewall"),
        ("qemu", "pve01", 100, "nodes/pve01/qemu/100/firewall"),
        ("lxc", "pve01", 101, "nodes/pve01/lxc/101/firewall"),
    ],
)
def test_alias_pair_lifecycle_and_check_mode(firewall_environment, firewall_run, scope, node, vmid, root):
    module, client, api = firewall_environment(scope=scope, node=node, vmid=vmid, cidr="192.0.2.1/32", comment="owned", check_mode=True)
    assert firewall_run(Action, module, client)["alias"]["cidr"] == "192.0.2.1"
    api.post.assert_not_called()
    assert firewall_run(Info, module, client)["aliases"] == []
    module.check_mode = False
    assert firewall_run(Action, module, client)["changed"] is True
    assert firewall_run(Info, module, client)["aliases"][0]["comment"] == "owned"
    assert firewall_run(Action, module, client)["changed"] is False
    module.params.update(cidr="2001:0db8::1/128", comment="")
    module.check_mode = True
    assert firewall_run(Action, module, client)["alias"]["cidr"] == "2001:db8::1"
    assert api.aliases[root]["example_net"]["comment"] == "owned"
    module.check_mode = False
    assert firewall_run(Action, module, client)["alias"]["cidr"] == "2001:db8::1"
    assert "comment" not in firewall_run(Info, module, client)["aliases"][0]
    assert firewall_run(Action, module, client)["changed"] is False
    module.params.update(state="absent", cidr="ignored", comment="0", rename="ignored/path")
    module.check_mode = True
    assert firewall_run(Action, module, client)["changed"] is True
    api.delete.assert_not_called()
    module.check_mode = False
    assert firewall_run(Action, module, client)["alias"] is None
    assert firewall_run(Info, module, client)["aliases"] == []
    assert firewall_run(Action, module, client)["changed"] is False


@pytest.mark.parametrize("rename", ["renamed_net", "EXAMPLE_NET"])
def test_alias_native_rename_is_idempotent_and_preserves_neighbors(firewall_environment, firewall_run, rename):
    module, client, api = firewall_environment(rename=rename)
    api.aliases["cluster/firewall"] = {"example_net": {"cidr": "192.0.2.1", "comment": "retained"}, "neighbor": {"cidr": "198.51.100.1"}}
    result = firewall_run(Action, module, client)
    assert result["alias"]["name"] == rename
    assert result["alias"]["comment"] == "retained"
    assert firewall_run(Action, module, client)["changed"] is False
    assert api.aliases["cluster/firewall"]["neighbor"] == {"cidr": "198.51.100.1"}


def test_explicit_digest_is_checked_before_write_but_not_against_readback(firewall_environment, firewall_run):
    module, client, api = firewall_environment(cidr="192.0.2.2")
    api.aliases["cluster/firewall"]["example_net"] = {"cidr": "192.0.2.1"}
    original_digest = api._get("cluster/firewall/aliases")[0]["digest"]
    module.params["digest"] = original_digest
    result = firewall_run(Action, module, client)
    assert result["changed"] is True
    assert result["alias"]["digest"] != original_digest
    api.put.assert_called_once_with("cluster/firewall/aliases/example_net", digest=original_digest, cidr="192.0.2.2")


def test_case_insensitive_lookup_does_not_rename_when_updating_other_fields(firewall_environment, firewall_run):
    module, client, api = firewall_environment(name="EXAMPLE_NET", comment="updated")
    api.aliases["cluster/firewall"]["example_net"] = {"cidr": "192.0.2.1"}
    result = firewall_run(Action, module, client)
    assert result["alias"]["name"] == "example_net"
    assert api.put.call_args.args[0] == "cluster/firewall/aliases/example_net"


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "parameters",
    [
        {"cidr": "invalid"},
        {"cidr": "192.0.2.1/255.255.255.0"},
        {"cidr": "fe80::1%net0"},
        {"comment": "0"},
        {"comment": "line\nbreak"},
        {"rename": ""},
        {"name": "a"},
        {"name": "invalid/path"},
        {"name": "a" * 65},
        {},
    ],
)
def test_alias_invalid_requests_do_not_write(firewall_environment, parameters, check_mode):
    module, client, api = firewall_environment(check_mode=check_mode, **parameters)
    handler = Action(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_alias_rename_collision_is_rejected(firewall_environment, check_mode):
    module, client, api = firewall_environment(rename="taken_net", check_mode=check_mode)
    api.aliases["cluster/firewall"] = {"example_net": {"cidr": "192.0.2.1"}, "taken_net": {"cidr": "192.0.2.2"}}
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="already exists"):
        handler.execute()
    api.put.assert_not_called()


def test_alias_readback_failure_retains_successful_mutation(firewall_environment):
    module, client, api = firewall_environment(cidr="192.0.2.1")
    original = api._get

    def get(path, **kw):
        if api.post.called:
            raise ResourceException(500, "unavailable", "readback failed")
        return original(path, **kw)

    api.get.side_effect = get
    handler = Action(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["status_code"] == 500


@pytest.mark.parametrize("check_mode", [False, True])
def test_alias_refs_remain_type_specific_and_preserve_origins_and_extension_fields(firewall_environment, firewall_run, check_mode):
    module, client, api = firewall_environment(check_mode=check_mode, name=None, include_references=True, scope="qemu", node="pve01", vmid=100)
    api.aliases["cluster/firewall"]["duplicate"] = {"cidr": "192.0.2.1"}
    api.aliases["nodes/pve01/qemu/100/firewall"]["duplicate"] = {"cidr": "192.0.2.2"}
    api.extra_references = [{"type": "alias", "name": "generated", "scope": "sdn", "ref": "generated", "extension": {"v": 1}}]
    before = copy.deepcopy(api.aliases)
    result = firewall_run(Info, module, client)
    assert result["changed"] is False
    assert [row["scope"] for row in result["references"]] == ["sdn", "dc", "guest"]
    assert result["references"][0]["extension"] == {"v": 1}
    assert len(result["aliases"]) == 1
    assert api.aliases == before
    api.get.assert_any_call("nodes/pve01/qemu/100/firewall/refs", type="alias")
    api.post.assert_not_called()
