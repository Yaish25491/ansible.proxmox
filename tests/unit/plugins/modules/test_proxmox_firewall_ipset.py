# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from proxmoxer.core import ResourceException

from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_ipset import ProxmoxFirewallIpsetModule as Action
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_ipset_info import ProxmoxFirewallIpsetInfoModule as Info


@pytest.mark.parametrize(
    "scope,node,vmid,root",
    [
        ("cluster", None, None, "cluster/firewall"),
        ("qemu", "pve01", 100, "nodes/pve01/qemu/100/firewall"),
        ("lxc", "pve01", 101, "nodes/pve01/lxc/101/firewall"),
    ],
)
def test_ipset_pair_lifecycle_membership_omission_empty_and_check_mode(firewall_environment, firewall_run, scope, node, vmid, root):
    module, client, api = firewall_environment(
        scope=scope,
        node=node,
        vmid=vmid,
        check_mode=True,
        entries=[{"cidr": "192.0.2.129/24", "comment": "kept", "nomatch": True}, {"cidr": "2001:db8::1/128"}],
    )
    assert firewall_run(Action, module, client)["ipset"]["entries"][0]["cidr"] == "192.0.2.0/24"
    api.post.assert_not_called()
    assert firewall_run(Info, module, client)["ipsets"] == []
    module.check_mode = False
    assert firewall_run(Action, module, client)["changed"] is True
    assert len(firewall_run(Info, module, client)["ipsets"][0]["entries"]) == 2
    assert firewall_run(Action, module, client)["changed"] is False
    module.params["entries"] = [{"cidr": "192.0.2.0/24", "nomatch": False}]
    assert firewall_run(Action, module, client)["changed"] is True
    members = firewall_run(Info, module, client)["ipsets"][0]["entries"]
    assert len(members) == 1
    assert members[0]["comment"] == "kept"
    assert not members[0].get("nomatch")
    module.params["entries"] = [{"cidr": "192.0.2.0/24", "comment": "", "nomatch": True}]
    assert firewall_run(Action, module, client)["changed"] is True
    module.params.update(entries=None, comment="set description")
    assert firewall_run(Action, module, client)["changed"] is True
    assert api.ipsets[root]["example_net"]["entries"] == [{"cidr": "192.0.2.0/24", "nomatch": 1}]
    module.params.update(entries=[], comment="")
    module.check_mode = True
    assert firewall_run(Action, module, client)["ipset"]["entries"] == []
    assert api.ipsets[root]["example_net"]["entries"]
    module.check_mode = False
    assert firewall_run(Action, module, client)["changed"] is True
    assert firewall_run(Action, module, client)["changed"] is False
    module.params.update(state="absent", entries=[{"cidr": "ignored"}], comment="0", rename="ignored/path")
    module.check_mode = True
    assert firewall_run(Action, module, client)["changed"] is True
    module.check_mode = False
    assert firewall_run(Action, module, client)["ipset"] is None
    assert firewall_run(Info, module, client)["ipsets"] == []
    assert firewall_run(Action, module, client)["changed"] is False


def test_ipset_rename_preserves_contents_and_uses_list_digest(firewall_environment, firewall_run):
    module, client, api = firewall_environment(rename="renamed_set", comment="updated")
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1", "nomatch": 1, "comment": "kept"}]}
    digest = api._get("cluster/firewall/ipset")[0]["digest"]
    module.params["digest"] = digest
    result = firewall_run(Action, module, client)
    assert result["ipset"]["name"] == "renamed_set"
    assert result["ipset"]["entries"][0]["comment"] == "kept"
    api.post.assert_called_once_with("cluster/firewall/ipset", name="renamed_set", rename="example_net", digest=digest, comment="updated")
    module.params["digest"] = None
    assert firewall_run(Action, module, client)["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True])
def test_populated_ipset_requires_explicit_force_before_prediction_or_deletion(firewall_environment, firewall_run, check_mode):
    module, client, api = firewall_environment(state="absent", check_mode=check_mode)
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1"}]}
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="not empty"):
        handler.execute()
    api.delete.assert_not_called()
    module.params["force"] = True
    assert firewall_run(Action, module, client)["changed"] is True
    if check_mode:
        api.delete.assert_not_called()
    else:
        api.delete.assert_called_once_with("cluster/firewall/ipset/example_net", force=1)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "entries",
    [
        [{"cidr": "0.0.0.0/0"}],
        [{"cidr": "::/0"}],
        [{"cidr": "bad address"}],
        [{"cidr": "192.0.2.1/32"}, {"cidr": "192.0.2.1"}],
        [{"cidr": "missing_alias"}],
        [{"cidr": "guest/network_alias"}],
        [{"cidr": "192.0.2.1", "comment": "0"}],
    ],
)
def test_invalid_ipset_members_never_create_or_predict_a_set(firewall_environment, entries, check_mode):
    module, client, api = firewall_environment(entries=entries, check_mode=check_mode)
    handler = Action(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


@pytest.mark.parametrize("alias", ["network_alias", "dc/network_alias"])
def test_ipset_alias_members_are_checked_and_preserved_as_literal_references(firewall_environment, firewall_run, alias):
    module, client, api = firewall_environment(entries=[{"cidr": alias}])
    api.aliases["cluster/firewall"]["network_alias"] = {"cidr": "192.0.2.0/24"}
    assert firewall_run(Action, module, client)["ipset"]["entries"][0]["cidr"] == alias
    assert firewall_run(Action, module, client)["changed"] is False


def test_ipset_member_update_preserves_unspecified_native_reset_fields(firewall_environment, firewall_run):
    module, client, api = firewall_environment(entries=[{"cidr": "192.0.2.1", "comment": "new comment"}])
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1", "nomatch": 1, "comment": "old comment"}]}
    result = firewall_run(Action, module, client)
    assert result["ipset"]["entries"][0]["nomatch"] == 1
    api.put.assert_called_once()
    assert api.put.call_args.kwargs["nomatch"] == 1
    assert api.put.call_args.kwargs["comment"] == "new comment"


def test_ipset_concurrent_membership_is_not_silently_overwritten(firewall_environment):
    module, client, api = firewall_environment(entries=[{"cidr": "192.0.2.1", "comment": "new"}])
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1"}]}

    def racing_put(path, **values):
        api._put(path, **values)
        api.ipsets["cluster/firewall"]["example_net"]["entries"].append({"cidr": "198.51.100.1"})

    api.put.side_effect = racing_put
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="concurrently") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    api.delete.assert_not_called()


def test_later_ipset_member_failure_preserves_the_completed_set_creation(firewall_environment):
    module, client, api = firewall_environment(entries=[{"cidr": "192.0.2.1"}])

    def post(path, **values):
        if path.endswith("/example_net"):
            raise ResourceException(500, "failure", "member write failed")
        return api._post(path, **values)

    api.post.side_effect = post
    handler = Action(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "example_net" in api.ipsets["cluster/firewall"]


def test_ipset_info_preserves_sdn_refs_and_can_omit_member_discovery(firewall_environment, firewall_run):
    module, client, api = firewall_environment(name=None, include_entries=False, include_references=True)
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1"}]}
    api.extra_references = [{"type": "ipset", "name": "sdn/generated", "ref": "+sdn/generated", "scope": "sdn", "extension": True}]
    result = firewall_run(Info, module, client)
    assert "entries" not in result["ipsets"][0]
    assert result["references"][0]["ref"] == "+sdn/generated"
    assert result["references"][0]["extension"] is True
    assert all(call.args[0] != "cluster/firewall/ipset/example_net" for call in api.get.call_args_list)
    api.post.assert_not_called()


@pytest.mark.parametrize("alias", ["network_alias", "dc/network_alias"])
def test_guest_ipset_can_use_inherited_alias_without_datacenter_list_permissions(firewall_environment, firewall_run, alias):
    module, client, api = firewall_environment(scope="qemu", node="pve01", vmid=100, entries=[{"cidr": alias}])
    api.aliases["cluster/firewall"]["network_alias"] = {"cidr": "192.0.2.0/24"}
    original = api._get

    def get(path, **values):
        if path == "cluster/firewall/aliases":
            raise ResourceException(403, "Forbidden", "datacenter aliases are not directly visible")
        return original(path, **values)

    api.get.side_effect = get
    assert firewall_run(Action, module, client)["ipset"]["entries"][0]["cidr"] == alias
    api.get.assert_any_call("nodes/pve01/qemu/100/firewall/refs", type="alias")


def test_ipset_rename_detects_members_changed_during_metadata_write(firewall_environment):
    module, client, api = firewall_environment(rename="renamed_set")
    api.ipsets["cluster/firewall"]["example_net"] = {"entries": [{"cidr": "192.0.2.1"}]}

    def post(path, **values):
        api._post(path, **values)
        api.ipsets["cluster/firewall"]["renamed_set"]["entries"].append({"cidr": "198.51.100.1"})

    api.post.side_effect = post
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="during metadata") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    api.delete.assert_not_called()


def test_inherited_alias_discovery_is_reused_within_preflight_and_refreshed_next_time(firewall_environment):
    module, client, api = firewall_environment(scope="qemu", node="pve01", vmid=100, entries=[{"cidr": "dc/alpha_alias"}, {"cidr": "dc/beta_alias"}])
    api.aliases["cluster/firewall"] = {"alpha_alias": {"cidr": "192.0.2.1"}, "beta_alias": {"cidr": "192.0.2.2"}}
    handler = Action(module, client)
    endpoint = "nodes/pve01/qemu/100/firewall/refs"
    assert handler.get_resource() is None
    assert sum(call.args[0] == endpoint for call in api.get.call_args_list) == 1
    del api.aliases["cluster/firewall"]["beta_alias"]
    with pytest.raises(module.Fail, match="alias does not exist"):
        handler.get_resource()
    assert sum(call.args[0] == endpoint for call in api.get.call_args_list) == 2
    api.post.assert_not_called()
