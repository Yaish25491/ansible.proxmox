# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ansible_collections.ansible.proxmox.plugins.modules import proxmox_lxc
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_lxc import ProxmoxLxcModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ModuleExit(Exception):
    def __init__(self, **result):
        self.result = result


class ModuleFailure(Exception):
    def __init__(self, **result):
        self.result = result


def module_params(**updates):
    params = {
        "node": "pve01",
        "vmid": 1003,
        "task_timeout": None,
        "state": "present",
        "power_cycle": "fail",
        "ostemplate": None,
        "hostname": None,
        "password": None,
        "pool": None,
        "tags": None,
        "unprivileged": None,
        "storage": "local-lvm",
        "rootfs": None,
        "cores": None,
        "cpulimit": None,
        "cpuunits": None,
        "memory": None,
        "swap": None,
        "network": None,
        "nameserver": None,
        "searchdomain": None,
        "features": None,
        "arch": None,
        "ostype": None,
    }
    params.update(updates)
    return params


def make_lxc(params=None, api=None, check_mode=False):
    api = api or Mock()
    node = api.nodes.return_value
    api.nodes.get.return_value = [{"node": "pve01"}]
    node.lxc.get.return_value = [{"vmid": 1003}]
    node.lxc.return_value.status.current.get.return_value = {"status": "stopped"}
    node.lxc.return_value.config.get.return_value = {"vmid": 1003, "memory": 512}
    node.storage.return_value.content.get.return_value = [{"volid": "local:vztmpl/fedora-44-default_20260713_amd64.tar.xz"}]
    node.storage.get.return_value = [{"storage": "local-lvm"}]
    node.tasks.return_value.status.get.return_value = {"status": "stopped", "exitstatus": "OK"}

    def exit_json(**result):
        raise ModuleExit(**result)

    module = SimpleNamespace(
        params=params or module_params(),
        check_mode=check_mode,
        exit_json=Mock(side_effect=exit_json),
        fail_json=Mock(side_effect=ModuleFailure),
    )
    client = ProxmoxClient(module)
    client._api = api
    client.validate_pve_permissions = Mock()
    return ProxmoxLxcModule(module, client=client), api, module


def test_creation_uses_vmid_template_and_default_root_disk():
    lxc, api, _module = make_lxc(module_params(ostemplate="fedora-44-default_20260713_amd64.tar.xz"))
    api.nodes.return_value.lxc.return_value.config.get.return_value = {"vmid": 1003, "memory": 512}

    result = lxc.create_resource()

    api.nodes.return_value.lxc.create.assert_called_once_with(
        vmid=1003,
        ostemplate="local:vztmpl/fedora-44-default_20260713_amd64.tar.xz",
        rootfs="local-lvm:4",
    )
    api.nodes.return_value.storage.return_value.content.get.assert_called_once_with(content="vztmpl")
    api.nodes.return_value.storage.get.assert_called_once_with(content="rootdir", enabled=1)
    assert result == {"vmid": 1003, "memory": 512}


def test_creation_from_backup_sets_restore_mode():
    backup_volume = "local:backup/vzdump-lxc-1003-2026_11_17-12_00_00.tar.zst"
    lxc, api, _module = make_lxc(module_params(ostemplate=backup_volume))
    api.nodes.return_value.storage.return_value.content.get.return_value = [{"volid": backup_volume}]

    lxc.create_resource()

    api.nodes.return_value.lxc.create.assert_called_once_with(
        vmid=1003,
        ostemplate=backup_volume,
        rootfs="local-lvm:4",
        restore=1,
    )
    api.nodes.return_value.storage.return_value.content.get.assert_called_once_with(content="backup")


@pytest.mark.parametrize("cpuunits", [8, 10000])
def test_cpuunits_portable_bounds_are_accepted(cpuunits):
    lxc, _api, _module = make_lxc(module_params(cpuunits=cpuunits))

    assert lxc._validated_request() == ("present", "fail")


@pytest.mark.parametrize("cpuunits", [0, 1, 2, 7, 10001, 20000, 500000])
def test_cpuunits_outside_portable_range_are_rejected(cpuunits):
    lxc, _api, module = make_lxc(module_params(cpuunits=cpuunits))

    with pytest.raises(ModuleFailure):
        lxc._validated_request()

    assert module.fail_json.call_args.kwargs["msg"] == "cpuunits must be at least 8 and at most 10000."


def test_cpuunits_at_cgroup_v2_limit_is_idempotent_after_clamping():
    lxc, _api, _module = make_lxc(module_params(cpuunits=10000))

    assert lxc.get_update_parameters({"cpuunits": 10000}) == {}


def test_creation_fails_when_template_is_missing():
    lxc, api, module = make_lxc(module_params(ostemplate="missing.tar.xz"))
    api.nodes.return_value.storage.return_value.content.get.return_value = []

    with pytest.raises(ModuleFailure):
        lxc._creation_parameters()

    assert "does not exist" in module.fail_json.call_args.kwargs["msg"]
    api.nodes.return_value.lxc.create.assert_not_called()


def test_network_updates_use_device_identifier_and_preserve_unrequested_fields():
    lxc, _api, _module = make_lxc(module_params(network=[{"device": "net0", "name": "eth0", "firewall": True}]))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"net0": "name=eth0,bridge=vmbr0,ip=dhcp", "net1": "name=eth1,bridge=vmbr1"}) == {
        "net0": "name=eth0,bridge=vmbr0,ip=dhcp,firewall=1",
        "delete": "net1",
    }


@pytest.mark.parametrize("current_tags", ["ansible;nested", "nested;ansible", "ANSIBLE;NESTED"])
def test_tag_updates_are_idempotent_after_proxmox_normalization(current_tags):
    lxc, _api, _module = make_lxc(module_params(tags="ansible,nested"))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"tags": current_tags}) == {}


def test_tag_membership_changes_are_still_requested():
    lxc, _api, _module = make_lxc(module_params(tags="ansible,nested,updated"))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"tags": "ansible;nested"}) == {"tags": "ansible,nested,updated"}


def test_update_resource_uses_lxc_config_put():
    lxc, api, _module = make_lxc()
    resource = {"memory": 512}

    updated = lxc.update_resource(resource, {"memory": 1024})

    api.nodes.return_value.lxc.return_value.config.put.assert_called_once_with(memory=1024)
    assert updated == {"memory": 1024}


def test_password_is_creation_only():
    """Keep root passwords out of configuration comparisons and updates."""
    lxc, _api, _module = make_lxc(module_params(password="test-only-password"))
    lxc._vm_status = "stopped"

    assert lxc._desired_parameters(creating=True)["password"] == "test-only-password"
    assert lxc.get_update_parameters({"memory": 512}) == {}


def test_reset_memory_sends_explicit_required_value():
    """Reset required LXC memory without using the delete directive."""
    lxc, _api, _module = make_lxc(module_params(memory=512))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"memory": 1024}) == {"memory": 512}
    assert lxc.get_update_parameters({"memory": "512"}) == {}


def test_features_string_is_forwarded_and_updates_only_requested_flags():
    """Pass feature strings on create and preserve unspecified update flags."""
    lxc, _api, _module = make_lxc(
        module_params(
            ostemplate="fedora-44-default_20260713_amd64.tar.xz",
            features="nesting=1",
        )
    )
    lxc._vm_status = "stopped"

    assert lxc._creation_parameters()["features"] == "nesting=1"
    assert lxc.get_update_parameters({"features": "fuse=1,nesting=0"}) == {
        "features": "fuse=1,nesting=1",
    }
    assert lxc.get_update_parameters({"features": "fuse=1,nesting=1"}) == {}


def test_empty_features_string_deletes_existing_feature_property():
    """Clear the feature property through the shared delete directive."""
    lxc, _api, _module = make_lxc(module_params(features=""))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"features": "nesting=1"}) == {"delete": "features"}


@pytest.mark.parametrize("rootfs", ["local-lvm:4", "volume=local-lvm:4", "local-lvm:vm-1003-disk-0,size=4G"])
def test_rootfs_allocation_is_idempotent(rootfs):
    """Preserve an existing volume when its initial allocation is repeated.

    Args:
        rootfs: Equivalent requested root filesystem specification.
    """
    lxc, api, _module = make_lxc(module_params(rootfs=rootfs))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"rootfs": "local-lvm:vm-1003-disk-0,size=4096M,acl=1"}) == {}
    api.nodes.return_value.storage.get.assert_not_called()


def test_rootfs_options_preserve_volume_and_unrequested_fields():
    """Apply mount options without replacing the disk or losing its metadata."""
    lxc, _api, _module = make_lxc(module_params(rootfs="local-lvm:4,acl=1"))
    lxc._vm_status = "stopped"

    assert lxc.get_update_parameters({"rootfs": "local-lvm:vm-1003-disk-0,size=4G,acl=0,replicate=0"}) == {
        "rootfs": "local-lvm:vm-1003-disk-0,size=4G,acl=1,replicate=0",
    }


def test_fail_power_cycle_rejects_update_of_running_container():
    lxc, _api, module = make_lxc(module_params(memory=1024))
    lxc._vm_status = "running"

    with pytest.raises(ModuleFailure):
        lxc.get_update_parameters({"memory": 512})

    assert "running" in module.fail_json.call_args.kwargs["msg"]


def test_hotplug_reads_pending_configuration():
    lxc, api, _module = make_lxc(module_params(power_cycle="hotplug"))

    assert lxc.get_resource() == {"vmid": 1003, "memory": 512}

    api.nodes.return_value.lxc.return_value.config.get.assert_called_once_with()


def test_absent_fail_policy_rejects_running_container_before_delete():
    lxc, api, module = make_lxc(module_params(state="absent"))
    api.nodes.return_value.lxc.return_value.status.current.get.return_value = {"status": "running"}

    with pytest.raises(ModuleFailure):
        lxc.execute_resource_module()

    api.nodes.return_value.lxc.return_value.delete.assert_not_called()
    assert "power it off" in module.fail_json.call_args.kwargs["msg"]


def test_power_cycle_off_stops_before_delete():
    lxc, api, module = make_lxc(module_params(state="absent", power_cycle="off"))
    container = api.nodes.return_value.lxc.return_value
    container.status.current.get.side_effect = [{"status": "running"}, {"status": "stopped"}]
    events = []
    container.status.stop.post.side_effect = lambda: events.append("stop") or "UPID:pve01:stop"
    container.delete.side_effect = lambda: events.append("delete")

    with pytest.raises(ModuleExit) as result:
        lxc.execute_resource_module()

    assert events == ["stop", "delete"]
    assert result.value.result["lxc"] == {"vmid": "1003"}
    module.exit_json.assert_called_once()


def test_power_cycle_on_starts_new_container(monkeypatch):
    lxc, api, module = make_lxc(
        module_params(
            ostemplate="fedora-44-default_20260713_amd64.tar.xz",
            power_cycle="on",
        )
    )
    api.nodes.return_value.lxc.get.return_value = []
    api.nodes.return_value.lxc.return_value.config.get.return_value = {"hostname": "fedora"}
    api.nodes.return_value.lxc.return_value.status.current.get.return_value = {"status": "running"}
    api.nodes.return_value.lxc.return_value.status.start.post.return_value = "UPID:pve01:start"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock())

    with pytest.raises(ModuleExit) as result:
        lxc.execute_resource_module()

    api.nodes.return_value.lxc.return_value.status.start.post.assert_called_once_with()
    assert result.value.result["changed"] is True
    assert result.value.result["lxc"] == {"hostname": "fedora", "vmid": "1003"}


def test_future_arch_and_ostype_values_are_forwarded():
    """Forward future API values unchanged during creation and updates."""
    requested = {"arch": "future-architecture", "ostype": "future-operating-system"}
    lxc, _api, _module = make_lxc(
        module_params(
            ostemplate="fedora-44-default_20260713_amd64.tar.xz",
            **requested,
        )
    )
    lxc._vm_status = "stopped"

    creation = lxc._creation_parameters()
    assert {name: creation[name] for name in requested} == requested
    assert lxc.get_update_parameters({"arch": "amd64", "ostype": "fedora"}) == requested
    assert lxc.get_update_parameters(dict(requested)) == {}


def test_module_documentation_and_argument_spec_cover_lxc_options(monkeypatch):
    constructor = Mock(return_value=SimpleNamespace(params=module_params()))
    monkeypatch.setattr(proxmox_lxc, "AnsibleModule", constructor)
    monkeypatch.setattr(ProxmoxLxcModule, "execute_resource_module", Mock())

    proxmox_lxc.main()

    spec = constructor.call_args.kwargs["argument_spec"]
    documentation = yaml.safe_load(proxmox_lxc.DOCUMENTATION)
    assert set(documentation["options"]).issubset(spec)
    for parameter in ("arch", "ostype"):
        assert spec[parameter] == {"type": "str"}
        assert documentation["options"][parameter]["type"] == "str"
        assert "choices" not in documentation["options"][parameter]
    assert spec["node"]["required"] is True
    assert spec["vmid"]["required"] is True
    assert spec["ostemplate"].get("required", False) is False
    assert spec["password"]["no_log"] is True
    assert spec["network"]["options"]["device"]["required"] is True
    assert spec["network"]["options"]["name"]["required"] is True
    assert spec["features"] == {"type": "str"}
    assert "choices" not in documentation["options"]["features"]
    assert spec["power_cycle"]["choices"] == ["on", "off", "fail", "hotplug"]
