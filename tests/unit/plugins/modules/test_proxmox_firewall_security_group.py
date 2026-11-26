# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy

import pytest
from proxmoxer.core import ResourceException

from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_security_group import ProxmoxFirewallSecurityGroupModule as Action
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_firewall_security_group_info import ProxmoxFirewallSecurityGroupInfoModule as Info


def test_security_group_pair_ordered_lifecycle_and_check_mode(firewall_environment, firewall_run):
    rules = [{"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "443", "enable": True}, {"type": "forward", "action": "DROP"}]
    module, client, api = firewall_environment(rules=rules, comment="owned", check_mode=True)
    prediction = firewall_run(Action, module, client)
    assert prediction["changed"] is True
    assert prediction["security_group"]["rules"][1]["pos"] == 1
    assert firewall_run(Info, module, client)["security_groups"] == []
    api.post.assert_not_called()
    module.check_mode = False
    assert firewall_run(Action, module, client)["changed"] is True
    result = firewall_run(Info, module, client)
    assert [row["action"] for row in result["security_groups"][0]["rules"]] == ["ACCEPT", "DROP"]
    assert firewall_run(Action, module, client)["changed"] is False
    module.params["rules"] = list(reversed(rules))
    module.check_mode = True
    before = copy.deepcopy(api.groups)
    assert firewall_run(Action, module, client)["security_group"]["rules"][0]["action"] == "DROP"
    assert api.groups == before
    module.check_mode = False
    api.put.reset_mock()
    assert firewall_run(Action, module, client)["changed"] is True
    assert api.put.call_count == 1
    assert api.put.call_args.kwargs["moveto"] == 0
    module.params["rules"] = [{"type": "in", "action": "REJECT", "enable": True}]
    assert firewall_run(Action, module, client)["changed"] is True
    rule = firewall_run(Info, module, client)["security_groups"][0]["rules"][0]
    assert rule["action"] == "REJECT"
    assert "proto" not in rule
    module.params.update(rules=None, comment="")
    assert firewall_run(Action, module, client)["changed"] is True
    assert len(api.groups["example_net"]["rules"]) == 1
    module.params["rules"] = []
    assert firewall_run(Action, module, client)["changed"] is True
    assert firewall_run(Action, module, client)["changed"] is False
    module.params.update(state="absent", rules=[{"type": "ignored"}], comment="0", rename="invalid/path")
    module.check_mode = True
    assert firewall_run(Action, module, client)["security_group"] is None
    assert "example_net" in api.groups
    module.check_mode = False
    assert firewall_run(Action, module, client)["changed"] is True
    assert firewall_run(Info, module, client)["security_groups"] == []
    assert firewall_run(Action, module, client)["changed"] is False


def test_security_group_rule_replacement_clears_only_supported_optional_properties(firewall_environment, firewall_run):
    module, client, api = firewall_environment(rules=[{"type": "out", "action": "ACCEPT"}])
    api.groups["example_net"] = {"rules": [{"type": "in", "action": "DROP", "enable": 1, "proto": "tcp", "dport": "22", "extension": True}]}
    result = firewall_run(Action, module, client)
    rule = result["security_group"]["rules"][0]
    assert rule["enable"] == 0
    assert "proto" not in rule
    assert "dport" not in rule
    assert rule["extension"] is True
    assert api.put.call_args.kwargs["delete"] == "dport,proto"


def test_security_group_native_rename_and_repeated_rename_keep_rules(firewall_environment, firewall_run):
    module, client, api = firewall_environment(rename="renamed_group")
    original = {"rules": [{"type": "in", "action": "ACCEPT", "enable": 0}], "comment": "retained"}
    api.groups["example_net"] = copy.deepcopy(original)
    result = firewall_run(Action, module, client)
    assert result["security_group"]["group"] == "renamed_group"
    assert api.groups["renamed_group"] == original
    assert firewall_run(Action, module, client)["changed"] is False
    assert api.post.call_args.kwargs["rename"] == "example_net"


@pytest.mark.parametrize("check_mode", [False, True])
def test_populated_group_deletion_requires_explicit_force(firewall_environment, firewall_run, check_mode):
    module, client, api = firewall_environment(state="absent", check_mode=check_mode)
    api.groups["example_net"] = {"rules": [{"type": "in", "action": "DROP"}]}
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="not empty"):
        handler.execute()
    api.delete.assert_not_called()
    module.params["force"] = True
    assert firewall_run(Action, module, client)["changed"] is True
    if check_mode:
        api.delete.assert_not_called()
    else:
        assert api.delete.call_args_list[-1].args == ("cluster/firewall/groups/example_net",)
        assert len(api.delete.call_args_list) == 2


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "parameters",
    [
        {"name": "x"},
        {"name": "a" * 19},
        {"name": "invalid/path"},
        {"comment": "0"},
        {"comment": "line\rbreak"},
        {"rename": "missing_group"},
        {"rules": [{"type": "group", "action": "nested"}]},
        {"rules": [{"type": "in", "action": "ACCEPT", "comment": "0"}]},
    ],
)
def test_invalid_group_requests_do_not_write(firewall_environment, parameters, check_mode):
    module, client, api = firewall_environment(check_mode=check_mode, **parameters)
    handler = Action(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.post.assert_not_called()
    api.put.assert_not_called()
    api.delete.assert_not_called()


def test_security_group_preserves_changed_after_later_rule_error(firewall_environment):
    module, client, api = firewall_environment(rules=[{"type": "in", "action": "ACCEPT"}])

    def post(path, **values):
        if path.endswith("/example_net"):
            raise ResourceException(400, "invalid", "native rule rejected")
        return api._post(path, **values)

    api.post.side_effect = post
    handler = Action(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["status_code"] == 400


def test_security_group_detects_concurrent_rule_changes(firewall_environment):
    module, client, api = firewall_environment(rules=[{"type": "in", "action": "ACCEPT"}])
    api.groups["example_net"] = {"rules": [{"type": "out", "action": "DROP", "enable": 0}]}

    def put(path, **values):
        api._put(path, **values)
        api.groups["example_net"]["rules"].append({"type": "out", "action": "REJECT"})

    api.put.side_effect = put
    handler = Action(module, client)
    with pytest.raises(module.Fail, match="concurrently") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    api.delete.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_security_group_info_can_read_metadata_only_or_empty_selection(firewall_environment, firewall_run, check_mode):
    module, client, api = firewall_environment(name=None, include_rules=False, check_mode=check_mode)
    api.groups["example_net"] = {"rules": [{"type": "in", "action": "ACCEPT"}], "comment": "kept"}
    result = firewall_run(Info, module, client)
    assert result["changed"] is False
    assert result["security_groups"][0]["comment"] == "kept"
    assert "rules" not in result["security_groups"][0]
    api.get.assert_called_once_with("cluster/firewall/groups")
    module.params.update(name="missing_group", include_rules=True)
    assert firewall_run(Info, module, client)["security_groups"] == []
    api.post.assert_not_called()


def test_partial_native_group_rename_preserves_changed_and_the_original_error(firewall_environment):
    module, client, api = firewall_environment(rename="renamed_group")
    api.groups["example_net"] = {"rules": [{"type": "in", "action": "ACCEPT", "enable": 0}]}

    def partial_rename(path, **values):
        api.groups["renamed_group"] = copy.deepcopy(api.groups["example_net"])
        raise ResourceException(500, "rename failed", "host configuration could not be updated")

    api.post.side_effect = partial_rename
    handler = Action(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["status_code"] == 500
    assert "host configuration could not be updated" in failure.value.args[0]["api_error"]
    assert set(api.groups) == {"example_net", "renamed_group"}
