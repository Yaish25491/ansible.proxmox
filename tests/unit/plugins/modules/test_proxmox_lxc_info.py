# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc_info import ProxmoxLxcInfoModule


class ModuleFailure(Exception):
    def __init__(self, **result):
        self.result = result


class FakeClient:
    def __init__(self, api):
        self.api = api
        self.validate_pve_permissions = Mock()


def make_info(params, api=None):
    api = api or Mock()
    api.nodes.get.return_value = [{"node": "pve01"}]
    api.nodes.return_value.lxc.get.return_value = [{"vmid": 1003}, {"vmid": 1004}]
    api.nodes.return_value.lxc.return_value.config.get.return_value = {"hostname": "fedora"}
    module = SimpleNamespace(
        params=params,
        fail_json=Mock(side_effect=ModuleFailure),
    )
    return ProxmoxLxcInfoModule(module, client=FakeClient(api)), api, module


@pytest.mark.parametrize("module_name", ["proxmox_lxc", "proxmox_lxc_info"])
def test_lxc_modules_are_registered_for_group_defaults(module_name):
    """Keep LXC connection defaults in the collection's shared action group.

    Args:
        module_name: LXC module expected to inherit group defaults.
    """
    runtime_path = Path(__file__).resolve().parents[4] / "meta" / "runtime.yml"
    metadata = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    assert module_name in metadata["action_groups"]["proxmox"]


def test_info_returns_all_containers_for_node_only_request():
    info, api, client_module = make_info({"node": "pve01", "vmid": None})

    result = info.get_info()

    assert result == [{"vmid": 1003}, {"vmid": 1004}]
    api.nodes.return_value.lxc.get.assert_called_once_with()
    api.nodes.return_value.lxc.return_value.config.get.assert_not_called()
    assert info.client.validate_pve_permissions.call_args.args[1] == "/vms"


def test_info_returns_selected_current_container_config():
    info, api, _module = make_info({"node": "pve01", "vmid": 1003})

    assert info.get_info() == [{"hostname": "fedora", "vmid": "1003"}]

    api.nodes.return_value.lxc.get.assert_called_once_with()
    api.nodes.return_value.lxc.return_value.config.get.assert_called_once_with(current=1)


def test_info_returns_empty_list_for_unknown_vmid():
    info, api, _module = make_info({"node": "pve01", "vmid": 1005})

    assert info.get_info() == []
    api.nodes.return_value.lxc.return_value.config.get.assert_not_called()


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_info_rejects_vmid_outside_supported_range(vmid):
    info, api, module = make_info({"node": "pve01", "vmid": vmid})

    with pytest.raises(ModuleFailure):
        info.get_info()

    assert "VMID must be between 100 and 999999999" in module.fail_json.call_args.kwargs["msg"]
    api.nodes.get.assert_not_called()


def test_info_documentation_and_argument_spec_match(monkeypatch):
    constructor = Mock(return_value=SimpleNamespace(params={"node": "pve01", "vmid": None}))
    monkeypatch.setattr(proxmox_lxc_info, "AnsibleModule", constructor)
    monkeypatch.setattr(ProxmoxLxcInfoModule, "execute", Mock())

    proxmox_lxc_info.main()

    spec = constructor.call_args.kwargs["argument_spec"]
    documentation = yaml.safe_load(proxmox_lxc_info.DOCUMENTATION)
    assert set(documentation["options"]).issubset(spec)
    assert spec["node"]["required"] is True
    assert spec["vmid"].get("required", False) is False
