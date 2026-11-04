# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from unittest.mock import Mock, call

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_pool
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_pool import ProxmoxPoolModule


@pytest.mark.parametrize("poolid", ["", "a/b/c/d", "a//b", "a/", "/a", "a b", "a\nb", "a?query=x"])
def test_pool_identifier_validation_rejects_invalid_api_names(pool_environment, poolid):
    module, client, api = pool_environment(poolid=poolid)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match="poolid must contain"):
        handler.get_resource()
    assert api.reads == []
    assert api.writes == []
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("poolid", ["a", "legacy_123", "a/b/c", "a.b-c_d", "123"])
def test_valid_pool_identifiers_include_nested_and_legacy_names(pool_environment, poolid):
    pool = {"poolid": poolid, "members": []}
    module, client, api = pool_environment(poolid=poolid, records={poolid: pool})
    assert ProxmoxPoolModule(module, client).get_resource() == pool
    assert api.writes == []


def test_server_read_failure_is_not_masked_as_missing_pool(pool_environment):
    module, client, api = pool_environment()
    api.get = Mock(side_effect=RuntimeError("server unavailable"))
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.get_resource()
    assert failure.value.args[0]["api_error"] == "server unavailable"
    assert api.writes == []


def test_empty_inventory_returns_none_for_resource(pool_environment):
    module, client, api = pool_environment()
    assert ProxmoxPoolModule(module, client).get_resource() is None
    assert api.writes == []


def test_supplied_summary_snapshot_does_not_affect_later_fresh_reads(pool_environment):
    module, client, api = pool_environment()
    handler = ProxmoxPoolModule(module, client)
    summaries = handler._list_pools()
    resource = {"poolid": "applications", "members": []}
    api.records["applications"] = resource

    assert handler._get_pool("applications", pool_summaries=summaries) is None
    assert api.reads == [{}]
    assert handler._get_pool("applications") == resource
    assert api.reads == [{}, {}, {"poolid": "applications"}]
    assert client.validate_pve_permissions.call_count == 2


def test_requests_and_prediction_reuse_a_plan_without_mutating_it(pool_environment):
    resource = {"poolid": "applications", "comment": "old", "members": [
        {"type": "qemu", "vmid": 100},
        {"type": "storage", "storage": "local", "details": {"used": 1}},
    ]}
    original = copy.deepcopy(resource)
    module, client, api = pool_environment(vms=[101, 101])
    handler = ProxmoxPoolModule(module, client)
    handler.guest_info = {guest["vmid"]: guest for guest in api.guests}
    updates = handler._get_membership_updates(resource)
    updates["comment"] = "new"
    saved_updates = copy.deepcopy(updates)

    requests = handler._get_update_requests(updates)
    predicted = handler._predict_pool(resource, updates)

    assert updates == saved_updates
    assert resource == original
    assert requests == handler._get_update_requests(updates)
    assert predicted["comment"] == "new"
    assert [member["vmid"] for member in predicted["members"] if "vmid" in member] == [101]
    assert predicted["members"][-1]["type"] == "lxc"
    predicted["members"][0]["details"]["used"] = 2
    assert resource == original
    assert api.reads == []
    assert api.writes == []


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("members", [False, True])
def test_creation_returns_configuration_and_check_mode_does_not_write(pool_environment, check_mode, members):
    options = {"comment": "Applications"}
    if members:
        options.update(vms=[100, 101], storage=["local"])
    module, client, api = pool_environment(check_mode=check_mode, **options)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    result = result.value.args[0]
    assert result["changed"] is True
    assert result["pool"]["comment"] == "Applications"
    assert len(result["pool"]["members"]) == (3 if members else 0)
    assert bool(api.writes) is not check_mode
    assert bool(api.records) is not check_mode
    if members:
        assert {entry["vmid"] for entry in result["pool"]["members"] if "vmid" in entry} == {100, 101}
    assert call(api, "/pool") in client.validate_pve_permissions.call_args_list


@pytest.mark.parametrize("check_mode", [False, True])
def test_unchanged_pool_does_not_write_or_report_change(pool_environment, check_mode):
    pool = {"poolid": "applications", "comment": "Applications", "members": [{"type": "qemu", "vmid": 100}]}
    module, client, api = pool_environment(check_mode=check_mode, records={"applications": pool}, comment="Applications", vms=[100, 100])
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "pool": pool}
    assert api.writes == []
    api.cluster.resources.get.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("mode,desired,expected", [
    ("exact", [101], {101}), ("present", [101], {100, 101}), ("absent", [100], set()),
    ("exact", [], set()), ("present", [], {100}), ("absent", [], {100}),
])
def test_membership_modes_reconcile_only_requested_type(pool_environment, check_mode, mode, desired, expected):
    pool = {"poolid": "applications", "members": [{"type": "qemu", "vmid": 100}, {"type": "storage", "storage": "local"}]}
    original = copy.deepcopy(pool)
    module, client, api = pool_environment(check_mode=check_mode, records={"applications": pool}, vms=desired, membership_mode=mode)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert {row["vmid"] for row in result.value.args[0]["pool"]["members"] if "vmid" in row} == expected
    assert {row["storage"] for row in result.value.args[0]["pool"]["members"] if "storage" in row} == {"local"}
    assert pool == original
    if check_mode:
        assert api.records["applications"] == pool
        assert api.writes == []


def test_combined_updates_send_boolean_delete_and_refresh_actual_results(pool_environment):
    pool = {"poolid": "applications", "comment": "old", "members": [{"type": "qemu", "vmid": 100}, {"type": "storage", "storage": "local"}]}
    module, client, api = pool_environment(records={"applications": pool}, vms=[101], storage=["local-lvm"], comment="new")
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert api.writes == [
        ("PUT", {"poolid": "applications", "vms": "100", "storage": "local", "comment": "new", "delete": 1}),
        ("PUT", {"poolid": "applications", "vms": "101", "storage": "local-lvm", "allow-move": 0}),
    ]
    assert result.value.args[0]["pool"] == api.records["applications"]
    for _method, parameters in api.writes:
        flag = parameters.get("delete", parameters.get("allow-move"))
        assert isinstance(flag, int)
        assert not isinstance(flag, bool)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("mode,desired,expected", [
    ("exact", ["local-lvm"], {"local-lvm"}), ("present", ["local-lvm"], {"local", "local-lvm"}),
    ("absent", ["local"], set()), ("exact", [], set()), ("present", [], {"local"}), ("absent", [], {"local"}),
])
def test_storage_membership_modes_preserve_guests(pool_environment, check_mode, mode, desired, expected):
    pool = {"poolid": "applications", "members": [{"type": "qemu", "vmid": 100}, {"type": "storage", "storage": "local"}]}
    module, client, api = pool_environment(records={"applications": pool}, check_mode=check_mode, storage=desired, membership_mode=mode)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    members = result.value.args[0]["pool"]["members"]
    assert {row["storage"] for row in members if "storage" in row} == expected
    assert {row["vmid"] for row in members if "vmid" in row} == {100}
    if check_mode:
        assert api.records["applications"] == pool
        assert api.writes == []


@pytest.mark.parametrize("comment", ["", "new"])
def test_comment_only_updates_preserve_members(pool_environment, comment):
    pool = {"poolid": "applications", "comment": "old", "members": [{"type": "qemu", "vmid": 100}]}
    module, client, api = pool_environment(records={"applications": pool}, comment=comment)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0]["pool"]["comment"] == comment
    assert result.value.args[0]["pool"]["members"] == pool["members"]
    assert len(api.writes) == 1


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_zero_comment_is_rejected_before_reads_or_writes(pool_environment, check_mode, exists):
    pool = {"poolid": "applications", "comment": "preserved", "members": []}
    records = {"applications": pool} if exists else {}
    module, client, api = pool_environment(records=records, check_mode=check_mode, comment="0", vms=[])
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match='cannot preserve comment="0"') as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is False
    assert api.records == records
    assert api.reads == []
    assert api.writes == []
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_deletion_and_repeated_deletion(pool_environment, check_mode, exists):
    records = {"applications": {"poolid": "applications", "members": []}} if exists else {}
    module, client, api = pool_environment(records=records, check_mode=check_mode, state="absent")
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": exists, "pool": None}
    assert len(api.writes) == int(exists and not check_mode)
    assert api.reads == ([{}, {"poolid": "applications"}] if exists else [{}])
    client.validate_pve_permissions.assert_called_once_with(api, "/pool")


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("child", [False, True])
def test_nonempty_pool_cannot_be_deleted_even_in_check_mode(pool_environment, check_mode, child):
    records = {"applications": {"poolid": "applications", "members": [] if child else [{"type": "storage", "storage": "local"}]}}
    if child:
        records["applications/child"] = {"poolid": "applications/child", "members": []}
    module, client, api = pool_environment(records=records, check_mode=check_mode, state="absent")
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match="not empty"):
        handler.execute()
    assert api.writes == []
    assert api.reads == [{}, {"poolid": "applications"}]
    client.validate_pve_permissions.assert_called_once_with(api, "/pool")


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options,message", [
    ({"vms": [99]}, "VMIDs"), ({"vms": [1000000000]}, "VMIDs"),
    ({"vms": [999]}, "does not exist"), ({"storage": ["missing"]}, "Storage does not exist"),
    ({"poolid": "applications/missing/child"}, "Parent pool"), ({"poolid": "123"}, "start with a letter"),
    ({"state": "absent", "vms": []}, "cannot be supplied"),
])
def test_invalid_inputs_and_missing_prerequisites_fail_before_writes(pool_environment, check_mode, options, message):
    module, client, api = pool_environment(check_mode=check_mode, **options)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    assert api.writes == []


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("allow_move", [False, True])
def test_guest_move_requires_explicit_permission_and_predicts_without_mutation(pool_environment, check_mode, allow_move):
    records = {"source": {"poolid": "source", "members": [{"vmid": 100, "type": "qemu"}]}}
    module, client, api = pool_environment(records=records, check_mode=check_mode, vms=[100], allow_move=allow_move)
    api.guests[0]["pool"] = "source"
    handler = ProxmoxPoolModule(module, client)
    if not allow_move:
        with pytest.raises(module.Fail, match="allow_move=true"):
            handler.execute()
        assert api.writes == []
    else:
        with pytest.raises(module.Exit) as result:
            handler.execute()
        assert result.value.args[0]["changed"] is True
        assert len(api.records["source"]["members"]) == int(check_mode)
        if not check_mode:
            assert api.writes[-1][1]["allow-move"] == 1
            assert not isinstance(api.writes[-1][1]["allow-move"], bool)


@pytest.mark.parametrize("check_mode", [False, True])
def test_nested_pool_uses_query_parameters_and_preserves_parent(pool_environment, check_mode):
    records = {"applications": {"poolid": "applications", "members": []}}
    module, client, api = pool_environment(records=records, poolid="applications/child", check_mode=check_mode)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0]["pool"]["poolid"] == "applications/child"
    assert api.writes == ([] if check_mode else [("POST", {"poolid": "applications/child"})])
    assert "applications" in api.records
    assert api.reads == ([{}] if check_mode else [{}, {}, {"poolid": "applications/child"}])
    assert client.validate_pve_permissions.call_args_list == [call(api, "/pool")] * (1 if check_mode else 2)


@pytest.mark.parametrize("check_mode", [False, True])
def test_missing_parent_is_rejected_without_repeating_discovery(pool_environment, check_mode):
    module, client, api = pool_environment(poolid="applications/child", check_mode=check_mode)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match="Parent pool applications does not exist"):
        handler.execute()
    assert api.reads == [{}]
    assert api.writes == []
    client.validate_pve_permissions.assert_called_once_with(api, "/pool")


def test_new_child_under_legacy_numeric_parent_is_supported(pool_environment):
    module, client, api = pool_environment(records={"123": {"poolid": "123", "members": []}}, poolid="123/child")
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Exit):
        handler.execute()
    assert api.writes == [("POST", {"poolid": "123/child"})]


def test_api_errors_retain_original_detail(pool_environment):
    module, client, api = pool_environment()
    api.post = Mock(side_effect=RuntimeError("cluster is not quorate"))
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["api_error"] == "cluster is not quorate"
    assert failure.value.args[0].get("changed", False) is False


def test_creation_reports_change_when_initial_membership_request_fails(pool_environment):
    module, client, api = pool_environment(vms=[100])
    api.put = Mock(side_effect=RuntimeError("membership rejected"))
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["api_error"] == "membership rejected"
    assert api.records["applications"]["members"] == []
    assert api.writes == [("POST", {"poolid": "applications"})]


def test_update_reports_change_when_addition_fails_after_removal(pool_environment):
    pool = {"poolid": "applications", "members": [{"type": "qemu", "vmid": 100}]}
    module, client, api = pool_environment(records={"applications": pool}, vms=[101])
    original_put = api.put

    def fail_addition(**parameters):
        if not parameters.get("delete"):
            raise RuntimeError("addition rejected")
        return original_put(**parameters)

    api.put = Mock(side_effect=fail_addition)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["api_error"] == "addition rejected"
    assert api.records["applications"]["members"] == []
    assert len(api.writes) == 1


@pytest.mark.parametrize("operation", ["create", "update"])
def test_pool_reports_change_when_final_read_fails(pool_environment, operation):
    records = {"applications": {"poolid": "applications", "members": []}} if operation == "update" else {}
    module, client, api = pool_environment(records=records, comment="new")
    original_get = api.get

    def fail_after_write(**parameters):
        if api.writes:
            raise RuntimeError("final read failed")
        return original_get(**parameters)

    api.get = Mock(side_effect=fail_after_write)
    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert failure.value.args[0]["api_error"] == "final read failed"
    assert api.records["applications"]["comment"] == "new"


@pytest.mark.parametrize("operation", ["create", "update"])
def test_pool_reports_change_when_permissions_are_revoked_after_write(pool_environment, operation):
    records = {"applications": {"poolid": "applications", "members": []}} if operation == "update" else {}
    module, client, api = pool_environment(records=records, comment="new")
    del client.validate_pve_permissions  # Exercise the real shared permission validation.
    api.access = Mock()
    api.access.roles.get.return_value = [{"roleid": "Administrator", "privs": "Pool.Audit"}]
    api.access.permissions.get.side_effect = lambda **_parameters: {} if api.writes else {"/pool": {"Pool.Audit": 1}}

    handler = ProxmoxPoolModule(module, client)
    with pytest.raises(module.Fail, match="lacks full Administrator permissions") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True
    assert "Pool.Audit" in failure.value.args[0]["msg"]
    assert api.records["applications"]["comment"] == "new"
    assert len(api.writes) == 1


def test_main_declares_alias_shared_constraints_and_check_mode(monkeypatch, pool_environment):
    module, client, _api = pool_environment()
    factory = Mock(return_value=module)
    monkeypatch.setattr(proxmox_pool, "AnsibleModule", factory)
    execute = Mock()
    monkeypatch.setattr(ProxmoxPoolModule, "execute", execute)
    proxmox_pool.main()
    spec = factory.call_args.kwargs
    assert spec["supports_check_mode"] is True
    assert spec["argument_spec"]["poolid"]["aliases"] == ["name"]
    assert ["api_token_id", "api_token_secret"] in spec["required_together"]
    assert spec["argument_spec"]["api_token_secret"]["no_log"] is True
    execute.assert_called_once()


def test_main_argument_validation_rejects_missing_pool_identifier(monkeypatch):
    monkeypatch.setattr(basic, "_load_params", lambda: {"api_host": "pve.example.com"})
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", Mock(side_effect=RuntimeError("invalid arguments")))
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_pool.main()
