# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox_base
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import (
    ProxmoxPower,
    ProxmoxPowerstateActionModule,
    ProxmoxPowerstateInfoModule,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_powerstate, proxmox_lxc_powerstate_info


@pytest.mark.parametrize("entrypoint", [proxmox_lxc_powerstate, proxmox_lxc_powerstate_info])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("status,expected", [
    ({"status": "running"}, "running"),
    ({"status": "stopped"}, "stopped"),
])
def test_lxc_module_entrypoints(entrypoint, check_mode, status, expected, monkeypatch):
    """Exercise LXC action and info entrypoints with real shared lifecycles.

    Args:
        entrypoint: LXC action or information module under test.
        check_mode: Whether the module predicts without performing writes.
        status: Initial LXC status returned by the API.
        expected: Normalized initial power state.
        monkeypatch: Pytest fixture for replacing module dependencies.
    """
    is_action = entrypoint is proxmox_lxc_powerstate
    params = {"vmid": 101}
    if is_action:
        params.update(powerstate="start", task_timeout=None)
    module = SimpleNamespace(
        params=params,
        check_mode=check_mode,
        exit_json=Mock(side_effect=SystemExit),
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    constructor = Mock(return_value=module)
    monkeypatch.setattr(entrypoint, "AnsibleModule", constructor)

    api = Mock()
    api.cluster.resources.get.return_value = [
        {"type": "qemu", "vmid": 101, "node": "pve-qemu"},
        {"type": "lxc", "vmid": "101", "node": "pve-lxc"},
    ]
    container = api.nodes.return_value.lxc.return_value
    perform_action = is_action and expected == "stopped" and not check_mode
    current_statuses = [status]
    if perform_action:
        current_statuses.append({"status": "running"})
    container.status.current.get.side_effect = current_statuses
    upid = "UPID:pve01:lxc-start"
    container.status.start.post.return_value = upid
    client = SimpleNamespace(api=api, mark_changed=Mock())
    monkeypatch.setattr(proxmox_base, "ProxmoxClient", Mock(return_value=client))
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        entrypoint.main()

    module.exit_json.assert_called_once()
    arguments = constructor.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert arguments["argument_spec"]["vmid"] == {"type": "int", "required": True}
    assert arguments["required_together"] == [["api_token_id", "api_token_secret"]]
    assert arguments["mutually_exclusive"] == [["api_password", "api_token_id"]]
    if is_action:
        assert arguments["argument_spec"]["powerstate"]["choices"] == list(ProxmoxPower.LXC_POWER_ACTIONS)
        assert arguments["argument_spec"]["task_timeout"] == {"type": "int"}
    else:
        assert "powerstate" not in arguments["argument_spec"]
        assert "task_timeout" not in arguments["argument_spec"]

    api.cluster.resources.get.assert_called_once_with(type="vm")
    api.nodes.assert_called_with("pve-lxc")
    api.nodes.return_value.qemu.assert_not_called()
    changed = is_action and expected == "stopped"
    result = module.exit_json.call_args.kwargs
    assert result["changed"] is changed
    if perform_action:
        assert result["powerstate"] == "running"
        assert result["task_upid"] == upid
        container.status.start.post.assert_called_once_with()
        wait.assert_called_once_with(upid, include_log_on_failure=True)
    else:
        assert result["powerstate"] == expected
        assert "task_upid" not in result
        container.status.start.post.assert_not_called()
        wait.assert_not_called()


@pytest.mark.parametrize(
    "entrypoint,lifecycle,module_params,method_name",
    [
        (
            proxmox_lxc_powerstate,
            ProxmoxPowerstateActionModule,
            {"vmid": 101, "powerstate": "start", "task_timeout": None},
            "execute_action_module",
        ),
        (
            proxmox_lxc_powerstate_info,
            ProxmoxPowerstateInfoModule,
            {"vmid": 101},
            "execute_info_module",
        ),
    ],
)
def test_lxc_module_script_entrypoint(entrypoint, lifecycle, module_params, method_name, monkeypatch):
    """Cover the module launch guard while isolating its lifecycle execution.

    Args:
        entrypoint: LXC action or information module under test.
        lifecycle: Shared handler class selected by the module.
        module_params: Parameters returned by the fake AnsibleModule.
        method_name: Lifecycle method called by main.
        monkeypatch: Pytest fixture for replacing module dependencies.
    """
    module = SimpleNamespace(params=module_params, check_mode=False)
    constructor = Mock(return_value=module)
    execution = Mock()
    monkeypatch.setattr(basic, "AnsibleModule", constructor)
    monkeypatch.setattr(lifecycle, method_name, execution)
    monkeypatch.setattr(proxmox_base, "ProxmoxClient", Mock(return_value=SimpleNamespace(api=Mock())))

    monkeypatch.delitem(sys.modules, entrypoint.__name__)
    runpy.run_module(entrypoint.__name__, run_name="__main__", alter_sys=True)

    constructor.assert_called_once()
    execution.assert_called_once()
