# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm_info import ProxmoxVmInfoModule


class FakeNodes:
    def get(self):
        return [{"node": "pve01"}]


class FakeApi:
    def __init__(self, vmids=(100, 101, 102, 103), vm_config=None):
        self.nodes = FakeNodes()
        self.calls = []
        self.vmids = vmids
        self.vm_config = vm_config if vm_config is not None else {"vmid": 100, "name": "vm-test-01"}

    def get(self, uri, **params):
        self.calls.append((uri, params))
        if "config" in uri:
            return self.vm_config
        return [{"vmid": vmid} for vmid in self.vmids]


class FakeClient:
    def __init__(self, api):
        self.api = api
        self.permission_requirements = []

    def validate_pve_permissions(self, api, path, role="Administrator", require_propagation=True):
        self.permission_requirements.append((path, role, require_propagation))


@pytest.mark.parametrize(
    ("params", "expected_uri", "expected_params"),
    [
        ({"node": "pve01", "vmid": None}, "nodes/pve01/qemu", {}),
        (
            {"node": "pve01", "vmid": 100},
            "nodes/pve01/qemu/100/config",
            {},
        ),
    ],
)
def test_vm_info_uses_explicit_proxmox_uri(params, expected_uri, expected_params):
    api = FakeApi()
    client = FakeClient(api)
    module = ProxmoxVmInfoModule(SimpleNamespace(params=params), client=client)

    result = module.get_info()

    if params["vmid"] is None:
        assert api.calls == [(expected_uri, expected_params)]
    else:
        assert api.calls == [
            ("nodes/pve01/qemu", {}),
            (expected_uri, expected_params),
        ]
    assert client.permission_requirements == [("/vms", "Administrator", True)]
    if params["vmid"] is None:
        assert len(result) == 4
    else:
        assert result == [{"vmid": 100, "name": "vm-test-01", "machine": "i440fx"}]


@pytest.mark.parametrize(
    ("vm_config", "expected_machine"),
    [
        ({}, "i440fx"),
        ({"ostype": "l26"}, "i440fx"),
        ({"machine": "q35"}, "q35"),
        ({"machine": "pc-i440fx-10.2"}, "pc-i440fx-10.2"),
    ],
)
def test_vm_info_reports_default_machine_without_mutating_api_config(vm_config, expected_machine):
    original = dict(vm_config)
    info = ProxmoxVmInfoModule(
        SimpleNamespace(params={"node": "pve01", "vmid": 100}),
        client=FakeClient(FakeApi(vm_config=vm_config)),
    )

    result = info.get_info()

    assert result == [dict(vm_config, machine=expected_machine)]
    assert result[0] is not vm_config
    assert vm_config == original


def test_vm_info_returns_empty_list_for_unknown_vmid():
    api = FakeApi(vmids=(100, 101, 102, 103))
    client = FakeClient(api)
    module = ProxmoxVmInfoModule(
        SimpleNamespace(params={"node": "pve01", "vmid": 107}),
        client=client,
    )

    assert module.get_info() == []
    assert api.calls == [("nodes/pve01/qemu", {})]


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_vm_info_rejects_vmid_outside_supported_range(vmid):
    api = FakeApi()
    module = SimpleNamespace(
        params={"node": "pve01", "vmid": vmid},
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    info = ProxmoxVmInfoModule(module, client=FakeClient(api))

    with pytest.raises(RuntimeError, match="module failed"):
        info.get_info()

    assert "VMID must be between 100 and 999999999" in module.fail_json.call_args.kwargs["msg"]
    assert api.calls == []
