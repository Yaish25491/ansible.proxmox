# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import hashlib
from unittest.mock import Mock, call

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_node_hosts
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_hosts import ProxmoxNodeHostsModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_node_info import ProxmoxNodeInfoModule


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("guarded", [False, True])
def test_matching_contents_do_not_write(node_environment, check_mode, guarded):
    module, client, api = node_environment(check_mode=check_mode)
    module.params["data"] = api.records["hosts"]["data"]
    if guarded:
        module.params["digest"] = api.records["hosts"]["digest"]
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "hosts": api.records["hosts"]}
    api.hosts.post.assert_not_called()
    api.nodes.get.assert_called_once_with()
    client.validate_pve_permissions.assert_called_once_with(api, "/")


@pytest.mark.parametrize("data", [
    "", "\n", "# comments only\n", "127.0.0.1 localhost", "127.0.0.1 localhost\n",
    "127.0.0.1\tlocalhost localhost.localdomain\n\n::1 ip6-localhost\n",
    "  # UTF-8 comment: caf\u00e9\n192.0.2.10 app.example.com app\n",
    "192.0.2.10\n", "127.0.0.1 localhost\r\n",
])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("guarded", [False, True])
def test_exact_replacement_and_prediction_including_empty_file(node_environment, data, check_mode, guarded):
    module, client, api = node_environment(data=data, check_mode=check_mode)
    original = copy.deepcopy(api.records["hosts"])
    if guarded:
        module.params["digest"] = original["digest"]
    handler = ProxmoxNodeHostsModule(module, client)
    expected = {"data": data, "digest": hashlib.sha1(data.encode("utf-8"), usedforsecurity=False).hexdigest()}
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": True, "hosts": expected}
    if check_mode:
        api.hosts.post.assert_not_called()
        assert api.records["hosts"] == original
        assert client._changed is False
    else:
        api.hosts.post.assert_called_once_with(data=data, digest=original["digest"])
        assert api.records["hosts"] == expected
        assert api.nodes.get.call_count == 2
        assert client.validate_pve_permissions.call_args_list == [call(api, "/"), call(api, "/")]
        # Repeat with the refreshed token, as a client would after a successful write.
        module.params["digest"] = expected["digest"] if guarded else None
        with pytest.raises(module.Exit) as repeated:
            handler.execute()
        assert repeated.value.args[0]["changed"] is False
        assert api.hosts.post.call_count == 1
        module.params["gather_subset"] = ["hosts"]
        with pytest.raises(module.Exit) as information:
            ProxmoxNodeInfoModule(module, client).execute()
        assert information.value.args[0]["nodes"][0]["hosts"] == expected


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("matches", [False, True])
def test_stale_digest_rejected_even_for_noop_and_check_mode(node_environment, check_mode, matches):
    module, client, api = node_environment(data="# new\n", digest="stale", check_mode=check_mode)
    if matches:
        module.params["data"] = api.records["hosts"]["data"]
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Fail, match="digest does not match") as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is False
    api.hosts.post.assert_not_called()


def test_concurrent_edit_is_not_overwritten_or_retried(node_environment):
    module, client, api = node_environment(data="# target\n")
    write = api.hosts.post.side_effect

    def intervening_edit(**parameters):
        api.records["hosts"] = {"data": "# somebody else's edit\n", "digest": "newer"}
        return write(**parameters)

    api.hosts.post.side_effect = intervening_edit
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Fail, match="checksum mismatch") as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is False
    assert api.hosts.post.call_count == 1
    assert api.records["hosts"]["data"] == "# somebody else's edit\n"


@pytest.mark.parametrize("options,message", [
    ({"node": ""}, "node must not be empty"),
    ({"digest": ""}, "between 1 and 64"), ({"digest": "x" * 65}, "between 1 and 64"),
    ({"data": "bad localhost\n"}, "IPv4 or IPv6"),
    ({"data": " 127.0.0.1 localhost\n"}, "IPv4 or IPv6"),
    ({"data": "fe80::1%eth0 host\n"}, "IPv4 or IPv6"),
    ({"data": "127.0.0.1 localhost # comment\n"}, "inline comment"),
    ({"data": "127.0.0.1 bad_name\n"}, "invalid host name"),
    ({"data": "127.0.0.1 caf\u00e9\n"}, "invalid host name"),
])
@pytest.mark.parametrize("check_mode", [False, True])
def test_invalid_input_rejected_before_api_access(node_environment, options, message, check_mode):
    parameters = dict(data="# target\n", check_mode=check_mode)
    parameters.update(options)
    module, client, api = node_environment(**parameters)
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Fail, match=message):
        handler.execute()
    client.validate_pve_permissions.assert_not_called()
    api.nodes.get.assert_not_called()
    api.hosts.post.assert_not_called()


def test_missing_node_fails_without_creation(node_environment):
    module, client, api = node_environment(node="missing", data="# target\n")
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Fail, match="does not exist"):
        handler.execute()
    api.hosts.get.assert_not_called()
    api.hosts.post.assert_not_called()


def test_empty_existing_file_can_be_repopulated(node_environment):
    module, client, api = node_environment(data="127.0.0.1 localhost\n")
    api.records["hosts"] = {"data": "", "digest": hashlib.sha1(b"", usedforsecurity=False).hexdigest()}
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True
    assert result.value.args[0]["hosts"]["data"] == module.params["data"]
    api.hosts.post.assert_called_once_with(data=module.params["data"], digest=hashlib.sha1(b"", usedforsecurity=False).hexdigest())


@pytest.mark.parametrize("stage", ["inventory", "read", "write", "readback", "permissions", "disappeared"])
def test_failures_preserve_completed_writes(node_environment, stage):
    module, client, api = node_environment(data="# target\n")
    error = RuntimeError("API unavailable")
    if stage == "inventory":
        api.nodes.get.side_effect = error
    elif stage == "read":
        api.hosts.get.side_effect = error
    elif stage == "write":
        api.hosts.post.side_effect = error
    elif stage == "readback":
        api.hosts.get.side_effect = [copy.deepcopy(api.records["hosts"]), error]
    elif stage == "disappeared":
        api.nodes.get.side_effect = [api.nodes.get.return_value, []]
    else:
        def check_permissions(*_args):
            if client._changed:
                client.fail_request("Permission revoked")
        client.validate_pve_permissions.side_effect = check_permissions
    handler = ProxmoxNodeHostsModule(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is (stage in ("readback", "permissions", "disappeared"))
    if stage in ("inventory", "read"):
        api.hosts.post.assert_not_called()


@pytest.mark.parametrize("options", [
    {}, {"node": "pve01"}, {"data": "# target\n"},
    {"node": "pve01", "data": "", "state": "present"},
    {"node": "pve01", "data": "", "state": "absent"},
    {"node": "pve01", "data": []},
])
def test_main_validates_required_and_unsupported_arguments(monkeypatch, options):
    monkeypatch.setattr(basic, "_load_params", lambda: dict(api_host="pve.example.com", **options))
    fail = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        proxmox_node_hosts.main()
    fail.assert_called_once()


def test_main_reuses_shared_arguments_and_handler(monkeypatch, node_environment):
    module, _client, _api = node_environment(data="")
    factory = Mock(return_value=module)
    execute = Mock()
    monkeypatch.setattr(proxmox_node_hosts, "AnsibleModule", factory)
    monkeypatch.setattr(ProxmoxNodeHostsModule, "execute", execute)
    proxmox_node_hosts.main()
    arguments = factory.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert "state" not in arguments["argument_spec"]
    assert "task_timeout" in arguments["argument_spec"]
    assert ["api_token_id", "api_token_secret"] in arguments["required_together"]
    assert ["api_password", "api_token_id"] in arguments["mutually_exclusive"]
    execute.assert_called_once()
