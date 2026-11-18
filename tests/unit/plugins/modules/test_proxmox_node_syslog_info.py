# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox_base
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_syslog_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_syslog_info import ProxmoxNodeSyslogInfoModule


@pytest.fixture
def syslog_environment(node_environment):
    """Reuse the existing node client fixture at the API boundary."""
    def create(**parameters):
        module, client, api = node_environment(**parameters)
        api.get = Mock(return_value=[{"n": 1, "t": "Nov 17 10:00:00 pve01 example[12]: ready"}])
        return module, client, api
    return create


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("options", [
    {}, {"start": 0}, {"limit": 0}, {"start": 50, "limit": 25},
    {"since": "2026-11-01"}, {"until": "2026-11-02 01:02"},
    {"since": "2026-11-01 01:02:03", "until": "2026-11-02 04:05:06", "service": "pvedaemon", "start": 0, "limit": 10},
    {"service": ""}, {"service": "sshd"}, {"service": "postfix"}, {"service": "example@instance.service"},
    {"service": "x" * 128}, {"start": None, "limit": None, "since": None, "until": None, "service": None},
])
def test_syslog_forwards_only_supplied_filters_and_never_writes(syslog_environment, check_mode, options):
    module, client, api = syslog_environment(check_mode=check_mode, **options)
    original = copy.deepcopy(api.records)
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "syslog": api.get.return_value}
    api.get.assert_called_once_with("nodes/pve01/syslog", **{key: value for key, value in options.items() if value is not None})
    client.validate_pve_permissions.assert_called_once_with(api, "/nodes/pve01")
    api.nodes.get.assert_called_once_with()
    api.nodes.assert_not_called()
    api.dns.put.assert_not_called()
    api.time.put.assert_not_called()
    assert api.records == original
    assert client._changed is False


@pytest.mark.parametrize("records", [[], [{"n": 1, "t": "no content"}], [{"n": 51, "t": "  original text \u00e9  "}]])
def test_preserves_empty_pages_sentinel_numbering_and_text(syslog_environment, records):
    module, client, api = syslog_environment(start=50)
    api.get.return_value = records
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "syslog": records}
    assert "total" not in result.value.args[0]
    api.get.assert_called_once_with("nodes/pve01/syslog", start=50)


def test_repeated_reads_are_fresh_and_do_not_paginate(syslog_environment):
    module, client, api = syslog_environment(limit=1)
    first = [{"n": 1, "t": "first observation"}]
    second = [{"n": 1, "t": "journal rotated"}]
    api.get.side_effect = [first, second]
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Exit) as initial:
        handler.execute()
    assert initial.value.args[0] == {"changed": False, "syslog": first}
    with pytest.raises(module.Exit) as repeated:
        handler.execute()
    assert repeated.value.args[0] == {"changed": False, "syslog": second}
    assert api.get.call_count == 2
    assert api.nodes.get.call_count == 2
    assert client.validate_pve_permissions.call_count == 2


@pytest.mark.parametrize("check_mode", [False, True])
def test_missing_node_returns_empty_list_without_syslog_request(syslog_environment, check_mode):
    module, client, api = syslog_environment(node="missing", check_mode=check_mode)
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "syslog": []}
    api.nodes.get.assert_called_once_with()
    api.get.assert_not_called()


@pytest.mark.parametrize("options,message", [
    ({"node": ""}, "node must not be empty"),
    ({"start": -1}, "start must be zero or greater"),
    ({"limit": -1}, "limit must be zero or greater"),
    ({"since": ""}, "since must use"),
    ({"until": ""}, "until must use"),
    ({"since": "today"}, "since must use"),
    ({"until": "2026-1-01"}, "until must use"),
    ({"since": "2026-11-01T01:02:03Z"}, "since must use"),
    ({"since": "2026-11-01\n"}, "since must use"),
    ({"since": "\u0662\u0660\u0662\u0666-11-01"}, "since must use"),
    ({"until": "2026-11-01 01:02:03+00:00"}, "until must use"),
    ({"until": "2026-11-01 \uff10\uff11:\uff10\uff12"}, "until must use"),
    ({"service": "x" * 129}, "service must contain at most 128"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_values_fail_before_api_access(syslog_environment, options, message, check_mode):
    module, client, api = syslog_environment(check_mode=check_mode, **options)
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.nodes.get.assert_not_called()
    api.get.assert_not_called()


@pytest.mark.parametrize("stage", ["discovery", "syslog"])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_api_failures_are_not_hidden_as_empty_results(syslog_environment, stage, status):
    module, client, api = syslog_environment(since="2026-02-31")
    error = ResourceException(status, "request failed", "server detail")
    if stage == "discovery":
        api.nodes.get.side_effect = error
    else:
        api.get.side_effect = error
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    result = failure.value.args[0]
    assert result["status_code"] == status
    assert "server detail" in result["api_error"]
    assert result["error_type"] == "ResourceException"
    assert result.get("changed", False) is False
    if stage == "discovery":
        api.get.assert_not_called()
    else:
        api.get.assert_called_once_with("nodes/pve01/syslog", since="2026-02-31")


@pytest.mark.parametrize("check_mode", [False, True])
def test_permission_failure_precedes_discovery_and_log_read(syslog_environment, check_mode):
    module, client, api = syslog_environment(check_mode=check_mode)
    client.validate_pve_permissions.side_effect = lambda *_args: client.fail_request("Permission denied")
    handler = ProxmoxNodeSyslogInfoModule(module, client)
    with pytest.raises(module.Fail, match="Permission denied"):
        handler.execute()
    api.nodes.get.assert_not_called()
    api.get.assert_not_called()


@pytest.mark.parametrize("options,message", [
    ({}, "missing required arguments: node"),
    ({"node": "pve01", "state": "present"}, "Unsupported parameters"),
    ({"node": "pve01", "task_timeout": 1}, "Unsupported parameters"),
    ({"node": "pve01", "priority": "err"}, "Unsupported parameters"),
    ({"node": "pve01", "startcursor": "cursor"}, "Unsupported parameters"),
    ({"node": "pve01", "start": "not-an-integer"}, "cannot be converted to an int"),
    ({"node": "pve01", "limit": []}, "cannot be converted to an int"),
    ({"node": "pve01", "api_token_id": "user@pve!token"}, "required together"),
    ({"node": "pve01", "api_password": "EXAMPLE", "api_token_id": "user@pve!token", "api_token_secret": "EXAMPLE"}, "mutually exclusive"),
])
def test_real_argument_validation(monkeypatch, options, message):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_node_syslog_info.main()
    fail.assert_called_once()
    assert message in fail.call_args.kwargs["msg"]


@pytest.mark.parametrize("check_mode", [False, True])
def test_main_with_real_arguments_and_shared_client(monkeypatch, syslog_environment, check_mode):
    module, client, api = syslog_environment()
    arguments = dict(api_host="pve.example.com", api_token_id="user@pve!token", api_token_secret="EXAMPLE",
                     node="pve01", start="0", limit="2", service="", _ansible_check_mode=check_mode)
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    monkeypatch.setattr(basic.AnsibleModule, "exit_json", lambda _self, **result: module.exit_json(**result))
    monkeypatch.setattr(proxmox_base, "ProxmoxClient", Mock(return_value=client))
    with pytest.raises(module.Exit) as result:
        proxmox_node_syslog_info.main()
    assert result.value.args[0] == {"changed": False, "syslog": api.get.return_value}
    api.get.assert_called_once_with("nodes/pve01/syslog", start=0, limit=2, service="")


def test_main_reuses_shared_info_arguments_and_auth_constraints(monkeypatch, syslog_environment):
    module, _client, _api = syslog_environment()
    factory = Mock(return_value=module)
    execute = Mock()
    monkeypatch.setattr(proxmox_node_syslog_info, "AnsibleModule", factory)
    monkeypatch.setattr(ProxmoxNodeSyslogInfoModule, "execute", execute)
    proxmox_node_syslog_info.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" not in arguments["argument_spec"]
    assert arguments["argument_spec"]["node"]["required"] is True
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    execute.assert_called_once()
