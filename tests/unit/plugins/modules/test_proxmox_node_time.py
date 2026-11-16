# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from unittest.mock import Mock, call

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_time
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_info import ProxmoxNodeInfoModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_time import ProxmoxNodeTimeModule


@pytest.mark.parametrize("check_mode", [False, True])
def test_matching_timezone_returns_observations_without_writing(node_environment, check_mode):
    module, client, api = node_environment(timezone="UTC", check_mode=check_mode)
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "time": api.records["time"]}
    api.time.put.assert_not_called()
    api.nodes.get.assert_called_once_with()
    client.validate_pve_permissions.assert_called_once_with(api, "/nodes/pve01")
    assert client._changed is False


@pytest.mark.parametrize("timezone,offset", [
    ("Europe/Berlin", 3600), ("America/New_York", -18000), ("Asia/Kathmandu", 20700), ("Etc/UTC", 0),
])
def test_timezone_update_readback_and_info_pair_are_idempotent(node_environment, timezone, offset):
    module, client, api = node_environment(timezone=timezone)
    original = copy.deepcopy(api.records["time"])
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": True, "time": dict(original, timezone=timezone, localtime=original["time"] + offset)}
    api.time.put.assert_called_once_with(timezone=timezone)
    assert api.records["time"]["time"] == original["time"]
    assert api.nodes.get.call_count == 2
    assert client.validate_pve_permissions.call_args_list == [call(api, "/nodes/pve01"), call(api, "/nodes/pve01")]

    module.params["gather_subset"] = ["time"]
    info = ProxmoxNodeInfoModule(module, client)
    with pytest.raises(module.Exit) as independent:
        info.execute()
    assert independent.value.args[0]["changed"] is False
    assert independent.value.args[0]["nodes"][0]["time"] == api.records["time"]
    api.get.assert_called_once_with("nodes/pve01/time")

    # Both observed timestamps may advance between invocations without requiring a write.
    api.records["time"]["time"] += 10
    api.records["time"]["localtime"] += 10
    with pytest.raises(module.Exit) as repeated:
        handler.execute()
    assert repeated.value.args[0] == {"changed": False, "time": api.records["time"]}
    assert api.time.put.call_count == 1


@pytest.mark.parametrize("timezone", ["Europe/Berlin", "Asia/Kathmandu", "Etc/UTC", "Unavailable/On_Node"])
def test_check_mode_predicts_only_timezone_and_does_not_use_controller_zone_database(node_environment, timezone):
    module, client, api = node_environment(timezone=timezone, check_mode=True)
    original = copy.deepcopy(api.records)
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": True, "time": {"timezone": timezone}}
    assert api.records == original
    api.time.put.assert_not_called()
    assert client._changed is False
    api.nodes.get.assert_called_once_with()


@pytest.mark.parametrize("options,message", [
    ({"node": ""}, "node must not be empty"),
    ({"timezone": ""}, "non-empty timezone"),
    ({"timezone": " "}, "without whitespace"),
    ({"timezone": "Europe/Berlin\n"}, "without whitespace"),
    ({"timezone": "Europe /Berlin"}, "without whitespace"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_inputs_fail_before_api_access(node_environment, options, message, check_mode):
    parameters = dict(timezone="Europe/Berlin")
    parameters.update(options)
    module, client, api = node_environment(check_mode=check_mode, **parameters)
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.nodes.assert_not_called()
    api.nodes.get.assert_not_called()
    api.time.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_missing_node_never_attempts_creation(node_environment, check_mode):
    module, client, api = node_environment(node="missing", timezone="Europe/Berlin", check_mode=check_mode)
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Fail, match="Node missing does not exist"):
        handler.execute()
    api.time.get.assert_not_called()
    api.time.put.assert_not_called()


def test_server_timezone_validation_preserves_api_failure_and_original_settings(node_environment):
    module, client, api = node_environment(timezone="Unavailable/On_Node")
    original = copy.deepcopy(api.records)
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Fail, match="No such timezone") as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is False
    assert api.records == original
    api.time.put.assert_called_once_with(timezone="Unavailable/On_Node")
    assert api.time.get.call_count == 1


@pytest.mark.parametrize("stage", ["inventory", "read", "write", "readback", "permissions", "disappeared"])
def test_failures_report_whether_the_timezone_write_completed(node_environment, stage):
    module, client, api = node_environment(timezone="Europe/Berlin")
    error = RuntimeError("API unavailable")
    if stage == "inventory":
        api.nodes.get.side_effect = error
    elif stage == "read":
        api.time.get.side_effect = error
    elif stage == "write":
        api.time.put.side_effect = error
    elif stage == "readback":
        api.time.get.side_effect = [copy.deepcopy(api.records["time"]), error]
    elif stage == "disappeared":
        api.nodes.get.side_effect = [api.nodes.get.return_value, []]
    else:
        def check_permissions(*_args):
            if client._changed:
                client.fail_request("Permission revoked")
        client.validate_pve_permissions.side_effect = check_permissions
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    completed = stage in ("readback", "permissions", "disappeared")
    assert failure.value.args[0].get("changed", False) is completed
    assert api.records["time"]["timezone"] == ("Europe/Berlin" if completed else "UTC")
    if stage in ("inventory", "read"):
        api.time.put.assert_not_called()


@pytest.mark.parametrize("check_mode", [False, True])
def test_initial_permission_failure_prevents_all_reads_and_writes(node_environment, check_mode):
    module, client, api = node_environment(timezone="Europe/Berlin", check_mode=check_mode)
    client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    handler = ProxmoxNodeTimeModule(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.nodes.get.assert_not_called()
    api.time.get.assert_not_called()
    api.time.put.assert_not_called()


@pytest.mark.parametrize("options", [
    {}, {"node": "pve01"}, {"timezone": "Europe/Berlin"},
    {"node": "pve01", "timezone": "Europe/Berlin", "state": "present"},
    {"node": "pve01", "timezone": "Europe/Berlin", "state": "absent"},
    {"node": "pve01", "timezone": "Europe/Berlin", "time": 1770000000},
    {"node": "pve01", "timezone": "Europe/Berlin", "localtime": 1770000000},
])
def test_main_validates_required_fields_and_rejects_unsupported_options(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_node_time.main()
    fail.assert_called_once()


def test_main_reuses_shared_auth_task_arguments_and_handler(monkeypatch, node_environment):
    module, _client, _api = node_environment(timezone="Europe/Berlin")
    factory = Mock(return_value=module)
    execute = Mock()
    monkeypatch.setattr(proxmox_node_time, "AnsibleModule", factory)
    monkeypatch.setattr(ProxmoxNodeTimeModule, "execute", execute)
    proxmox_node_time.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" in arguments["argument_spec"]
    assert arguments["argument_spec"]["timezone"]["required"] is True
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    execute.assert_called_once()
