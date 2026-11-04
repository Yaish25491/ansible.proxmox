# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_vm
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_vm import ProxmoxVmModule


class FakeApi:
    def __init__(self):
        self.create_parameters = None
        self.created_config = {"name": "created-vm", "memory": 512}
        self.config_queries = 0
        self.update_parameters = None
        self.storage_queries = 0
        self.task_upids = []
        self.task_status_pending = False
        self.storage_entries = [{"storage": "local-lvm", "active": 1, "enabled": 1, "content": "images"}]
        self.storage_get_calls = []
        self.machine_get_calls = []
        self.machine_entries = [
            {"id": "pc-q35-10.0", "type": "q35", "version": "10.0"},
            {"id": "pc-q35-10.0+pve1", "type": "q35", "version": "10.0+pve1"},
            {"id": "pc-i440fx-10.0+pve1", "type": "i440fx", "version": "10.0+pve1"},
        ]

    def nodes(self, node):
        return self

    @property
    def qemu(self):
        return self

    @property
    def config(self):
        return self

    @property
    def storage(self):
        return self

    def get(self, uri=None, **parameters):
        if self.task_status_pending:
            self.task_status_pending = False
            return {"status": "stopped", "exitstatus": "OK"}
        if uri is not None:
            self.machine_get_calls.append(uri)
            return self.machine_entries
        if parameters.get("current") == 1:
            self.config_queries += 1
            return self.created_config
        self.storage_queries += 1
        self.storage_get_calls.append(parameters)
        return self.storage_entries

    def __call__(self, vmid):
        return self

    def create(self, **parameters):
        self.create_parameters = parameters
        return "UPID:pve01:task"

    def post(self, **parameters):
        self.update_parameters = parameters
        return "UPID:pve01:task"

    def delete(self):
        return "UPID:pve01:delete"

    def tasks(self, upid):
        self.task_upids.append(upid)
        self.task_status_pending = True
        return self

    @property
    def status(self):
        return self


class FakeClient:
    def __init__(self, api):
        self.api = api


def module_params(**updates):
    params = {
        "node": "pve01",
        "vmid": 100,
        "task_timeout": None,
        "power_cycle": "fail",
        "name": None,
        "memory": None,
        "cores": None,
        "sockets": None,
        "cpu": None,
        "bios": None,
        "efidisk": None,
        "machine": None,
        "ostype": None,
        "vga": None,
        "scsihw": None,
        "ide2": None,
        "network": None,
        "disk": None,
    }
    params.update(updates)
    return params


def make_vm(params=None, api=None):
    vm = ProxmoxVmModule(
        SimpleNamespace(params=params or module_params()),
        client=FakeClient(api or FakeApi()),
    )
    vm._vm_status = "stopped"
    return vm


def test_disk_argument_and_examples_use_structured_disk_entries(monkeypatch):
    constructor = Mock(return_value=SimpleNamespace(params=module_params()))
    monkeypatch.setattr(proxmox_vm, "AnsibleModule", constructor)
    monkeypatch.setattr(ProxmoxVmModule, "execute_resource_module", Mock())

    proxmox_vm.main()

    spec = constructor.call_args.kwargs["argument_spec"]
    assert "required_if" not in constructor.call_args.kwargs
    documentation = yaml.safe_load(proxmox_vm.DOCUMENTATION)
    assert "scsi0" not in spec
    assert "scsi0" not in module_params()
    assert set(documentation["options"]).issubset(spec)
    assert spec["power_cycle"]["choices"] == ["on", "off", "fail", "hotplug"]
    assert spec["power_cycle"]["default"] == "fail"
    assert documentation["options"]["machine"]["type"] == "dict"
    assert spec["machine"]["type"] == "dict"
    assert documentation["options"]["efidisk"]["type"] == "dict"
    assert spec["efidisk"]["type"] == "dict"
    assert documentation["options"]["vga"]["type"] == "str"
    assert "choices" not in documentation["options"]["vga"]
    assert spec["vga"]["type"] == "str"
    assert "choices" not in spec["vga"]
    assert documentation["options"]["network"]["suboptions"]["model"]["type"] == "str"
    assert "choices" not in documentation["options"]["network"]["suboptions"]["model"]
    assert spec["network"]["options"]["model"]["type"] == "str"
    assert "choices" not in spec["network"]["options"]["model"]
    assert documentation["options"]["scsihw"]["type"] == "str"
    assert "choices" not in documentation["options"]["scsihw"]
    assert spec["scsihw"]["type"] == "str"
    assert "choices" not in spec["scsihw"]
    assert set(documentation["options"]["machine"]["suboptions"]) == set(spec["machine"]["options"])
    assert set(spec["machine"]["options"]) == {"type", "version"}
    assert spec["disk"]["type"] == "list"
    assert spec["disk"]["elements"] == "dict"
    assert set(spec["disk"]["options"]) == {"device", "storage", "size"}
    assert set(documentation["options"]["disk"]["suboptions"]) == set(spec["disk"]["options"])
    for task in yaml.safe_load(proxmox_vm.EXAMPLES):
        arguments = task["ansible.proxmox.proxmox_vm"]
        assert set(arguments).issubset(spec)
        for name, value in arguments.items():
            if "choices" in spec[name]:
                assert value in spec[name]["choices"]
        for disk in arguments.get("disk", []):
            assert set(disk) == {"device", "storage", "size"}


@pytest.mark.parametrize(
    ("state", "efidisk", "should_fail"),
    [("present", None, True), ("absent", None, False), ("absent", {}, False)],
)
def test_ovmf_requires_efidisk_only_when_present(state, efidisk, should_fail):
    module = SimpleNamespace(
        params={"state": state, "bios": "ovmf", "efidisk": efidisk, "vmid": 100},
        fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    vm = ProxmoxVmModule(module, client=FakeClient(FakeApi()))
    vm.get_resource = Mock(side_effect=RuntimeError("lookup"))

    if should_fail:
        with pytest.raises(RuntimeError, match="module failed"):
            vm.execute_resource_module()
        assert "efidisk" in module.fail_json.call_args.kwargs["msg"]
        vm.get_resource.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="lookup"):
            vm.execute_resource_module()
        vm.get_resource.assert_called_once_with()


@pytest.mark.parametrize("vmid", [99, 1000000000])
def test_vm_rejects_vmid_outside_supported_range(vmid):
    vm = make_vm(module_params(vmid=vmid))
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.execute_resource_module()

    assert "VMID must be between 100 and 999999999" in vm.module.fail_json.call_args.kwargs["msg"]


def test_create_omits_parameters_equal_to_api_defaults():
    api = FakeApi()
    vm = make_vm(api=api)
    result = vm.create_resource()

    assert result == api.created_config
    assert api.create_parameters == {"vmid": 100}
    assert api.task_upids == ["UPID:pve01:task"]
    assert api.config_queries == 1
    assert vm.prepare_result(changed=True, resource=result) == {
        "changed": True,
        "vm": dict(api.created_config, vmid="100", machine="i440fx"),
        "task_upid": "UPID:pve01:task",
    }


def test_create_without_task_upid_reads_configuration():
    api = Mock()
    api.nodes.return_value.qemu.create.return_value = None
    config = {"name": "created-vm", "memory": 1024}
    api.nodes.return_value.qemu.return_value.config.get.return_value = config
    vm = make_vm(api=api)

    result = vm.create_resource()

    assert result == config
    assert vm.prepare_result(changed=True, resource=result) == {
        "changed": True,
        "vm": dict(config, vmid="100", machine="i440fx"),
    }
    api.nodes.return_value.qemu.return_value.config.get.assert_called_once_with(current=1)
    api.nodes.return_value.tasks.assert_not_called()


def test_create_task_failure_does_not_read_configuration(monkeypatch):
    api = Mock()
    api.nodes.return_value.qemu.create.return_value = "UPID:pve01:create"
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", Mock(side_effect=RuntimeError("task failed")))
    vm = make_vm(api=api)

    with pytest.raises(RuntimeError, match="task failed"):
        vm.create_resource()

    api.nodes.return_value.qemu.return_value.config.get.assert_not_called()


def test_check_mode_creation_does_not_return_task_upid():
    vm = make_vm()
    resource = vm.get_check_mode_resource("create")

    assert vm.prepare_result(changed=True, resource=resource) == {
        "changed": True,
        "vm": {"vmid": "100", "machine": "i440fx"},
    }


def test_unchanged_vm_does_not_return_task_upid():
    vm = make_vm()
    resource = {"name": "existing-vm"}

    assert vm.prepare_result(changed=False, resource=resource) == {
        "changed": False,
        "vm": dict(resource, vmid="100", machine="i440fx"),
    }


@pytest.mark.parametrize("vmid", [100, 98672])
@pytest.mark.parametrize(
    "resource",
    [None, {}, {"name": "existing-vm"}, {"vmid": "999", "name": "existing-vm"},
     {"machine": "q35"}, {"machine": "pc-i440fx-10.2"}],
)
def test_vm_result_includes_actual_vmid_inside_vm_without_mutating_resource(vmid, resource):
    vm = make_vm(module_params(vmid=vmid))
    original = dict(resource) if resource is not None else None

    result = vm.prepare_result(changed=True, resource=resource)

    expected_vm = dict(resource or {}, vmid=str(vmid))
    if resource is not None:
        expected_vm.setdefault("machine", "i440fx")
    assert result["vm"] == expected_vm
    assert set(result) == {"changed", "vm"}
    assert resource == original


def test_vm_return_documentation_matches_output_keys():
    documentation = yaml.safe_load(proxmox_vm.RETURN)

    assert set(documentation) == {"vm", "task_upid"}
    assert documentation["vm"]["sample"]["vmid"] == "100"


@pytest.mark.parametrize("parameters", [{}, {"task_timeout": 0}, {"task_timeout": 900}])
def test_vm_task_wait_uses_configured_timeout(monkeypatch, parameters):
    """Verify VM task waiting delegates timeout resolution to the shared utility.

    Args:
        monkeypatch: Pytest fixture for replacing task waiting.
        parameters: Module task-timeout parameters.
    """
    wait_for_task = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait_for_task)
    vm = make_vm(module_params(**parameters))

    vm._wait_for_task("UPID:pve01:task")

    wait_for_task.assert_called_once_with("UPID:pve01:task")


@pytest.mark.parametrize("efitype", ["2m", "4m"])
def test_create_with_ovmf_allocates_basic_efi_disk(efitype):
    api = FakeApi()
    efidisk = {"storage": "company-lvm", "efitype": efitype, "pre_enrolled_keys": True}
    api.storage_entries = [
        {"storage": "company-lvm", "active": 1, "enabled": 1, "content": "images"}
    ]
    vm = make_vm(module_params(bios="ovmf", efidisk=efidisk), api)

    vm.create_resource()

    assert api.create_parameters["bios"] == "ovmf"
    assert api.create_parameters["efidisk0"] == "company-lvm:1,efitype={0},pre-enrolled-keys=1".format(efitype)
    assert api.storage_queries == 1
    assert api.storage_get_calls == [{"content": "images", "enabled": 1}]


@pytest.mark.parametrize(
    ("storage_entries", "message"),
    [([], "not enabled for images"), ([{"storage": "company-lvm", "active": 0}], "not active")],
)
def test_create_with_ovmf_rejects_unavailable_or_inactive_efi_storage(storage_entries, message):
    api = FakeApi()
    api.storage_entries = storage_entries
    vm = make_vm(
        module_params(
            bios="ovmf",
            efidisk={"storage": "company-lvm", "efitype": "4m", "pre_enrolled_keys": True},
        ),
        api,
    )
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.create_resource()

    assert message in vm.module.fail_json.call_args.kwargs["msg"]
    assert api.create_parameters is None


@pytest.mark.parametrize("size", ["32G", "32g", "32"])
def test_disk_allocation_is_formatted_only_for_creation(size):
    api = FakeApi()
    vm = make_vm(
        module_params(disk=[{"device": "scsi0", "storage": "local-lvm", "size": size}]),
        api,
    )

    vm.create_resource()

    assert api.create_parameters == {"vmid": 100, "scsi0": "local-lvm:32"}
    assert api.storage_queries == 1


def test_disk_definitions_are_ignored_by_update_comparison():
    vm = make_vm(module_params(disk=[{"device": "scsi0", "storage": "missing", "size": "999G"}]))
    resource = {
        "scsi0": "local-lvm:vm-100-disk-0,size=32G",
        "scsi1": "local-lvm:vm-100-disk-1,size=64G",
    }

    assert "disk" not in vm._desired_parameters()
    assert vm.get_update_parameters(resource) == {}


def test_existing_vm_does_not_validate_creation_disk_storage():
    api = Mock()
    api.nodes.get.return_value = [{"node": "pve01"}]
    api.nodes.return_value.qemu.get.return_value = [{"vmid": 100}]
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = {
        "status": "running",
        "qmpstatus": "running",
    }
    api.nodes.return_value.qemu.return_value.config.get.return_value = {"name": "existing"}
    vm = make_vm(
        module_params(disk=[{"device": "scsi0", "storage": "missing", "size": "32G"}]),
        api,
    )
    vm.client.validate_pve_permissions = Mock()

    assert vm.get_resource() == {"name": "existing"}
    assert vm._vm_status == "running"
    api.nodes.get.assert_called_once_with()
    api.nodes.return_value.qemu.get.assert_called_once_with()
    api.nodes.return_value.qemu.return_value.status.current.get.assert_called_once_with()
    api.nodes.return_value.storage.get.assert_not_called()


def test_hotplug_reads_pending_configuration_for_idempotency():
    api = Mock()
    api.nodes.get.return_value = [{"node": "pve01"}]
    api.nodes.return_value.qemu.get.return_value = [{"vmid": 100}]
    qemu = api.nodes.return_value.qemu.return_value
    qemu.status.current.get.return_value = {"status": "running"}
    qemu.config.get.return_value = {"name": "pending-name"}
    vm = make_vm(module_params(power_cycle="hotplug", name="pending-name"), api)
    vm.client.validate_pve_permissions = Mock()

    resource = vm.get_resource()

    assert vm.get_update_parameters(resource) == {}
    qemu.config.get.assert_called_once_with()


def test_missing_vm_is_detected_before_status_lookup():
    api = Mock()
    api.nodes.get.return_value = [{"node": "pve01"}]
    api.nodes.return_value.qemu.get.return_value = [{"vmid": 100}, {"vmid": 101}]
    vm = make_vm(module_params(vmid=107), api)
    vm.client.validate_pve_permissions = Mock()

    assert vm.get_resource() is None

    api.nodes.get.assert_called_once_with()
    api.nodes.return_value.qemu.get.assert_called_once_with()
    api.nodes.return_value.qemu.return_value.status.current.get.assert_not_called()
    api.nodes.return_value.qemu.return_value.config.get.assert_not_called()


def test_creation_check_mode_includes_disk_allocation_without_storage_queries():
    api = FakeApi()
    vm = make_vm(
        module_params(disk=[{"device": "scsi0", "storage": "local-lvm", "size": "32G"}]),
        api,
    )

    assert vm.get_check_mode_resource("create") == {"vmid": 100, "scsi0": "local-lvm:32"}
    assert api.storage_queries == 0


@pytest.mark.parametrize("device", ["ide0", "ide3", "sata0", "sata5", "virtio0", "virtio15", "scsi0", "scsi30"])
def test_creation_accepts_disk_device_boundaries(device):
    vm = make_vm(module_params(disk=[{"device": device, "storage": "local-lvm", "size": "32G"}]))

    assert vm._creation_parameters() == {"vmid": 100, device: "local-lvm:32"}


def test_creation_accepts_same_disk_index_for_different_types():
    devices = ["ide0", "sata0", "virtio0", "scsi0"]
    disks = [{"device": device, "storage": "local-lvm", "size": "32G"} for device in devices]
    vm = make_vm(module_params(disk=disks))

    parameters = vm._creation_parameters()

    assert parameters["vmid"] == 100
    assert all(parameters[device] == "local-lvm:32" for device in devices)


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    "device",
    ["vmid", "name", "net0", "scsi", "scsi-1", "ide4", "sata6", "virtio16", "scsi31", "scsi00", "scsi1x", "scsi1\u0662", "scsi0 ", ""],
)
def test_creation_rejects_invalid_disk_devices_before_allocation(device, check_mode):
    """Reject invalid disk devices before creating or allocating a VM.

    Args:
        device: Invalid disk device name to request.
        check_mode: Whether to predict creation without changing the VM.
    """
    api = FakeApi()
    vm = make_vm(module_params(disk=[{"device": device, "storage": "local-lvm", "size": "32G"}]), api)
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    operation = vm.get_check_mode_resource if check_mode else vm.create_resource
    arguments = ("create",) if check_mode else ()

    with pytest.raises(RuntimeError, match="module failed"):
        operation(*arguments)

    assert "Invalid disk device" in vm.module.fail_json.call_args.kwargs["msg"]
    assert api.create_parameters is None
    assert api.storage_queries == 0


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("device", ["ide0", "sata0", "virtio0", "scsi0"])
def test_creation_rejects_duplicate_disk_devices_before_allocation(device, check_mode):
    """Reject duplicate disk devices before creating or allocating a VM.

    Args:
        device: Disk device name requested more than once.
        check_mode: Whether to predict creation without changing the VM.
    """
    api = FakeApi()
    disks = [
        {"device": device, "storage": "local-lvm", "size": "32G"},
        {"device": device, "storage": "local-lvm", "size": "64G"},
    ]
    vm = make_vm(module_params(disk=disks), api)
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    operation = vm.get_check_mode_resource if check_mode else vm.create_resource
    arguments = ("create",) if check_mode else ()

    with pytest.raises(RuntimeError, match="module failed"):
        operation(*arguments)

    assert vm.module.fail_json.call_args.kwargs["msg"] == "Duplicate disk device {0}.".format(device)
    assert api.create_parameters is None
    assert api.storage_queries == 0


@pytest.mark.parametrize("ide2", ["none", "cdrom"])
def test_creation_rejects_disk_conflicting_with_cdrom_option(ide2):
    vm = make_vm(module_params(ide2=ide2, disk=[{"device": "ide2", "storage": "local-lvm", "size": "32G"}]))
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm._creation_parameters()

    assert "Disk device ide2 conflicts" in vm.module.fail_json.call_args.kwargs["msg"]


@pytest.mark.parametrize("ide2", ["none", "cdrom"])
def test_ide2_fixed_media_option_is_configured_by_vm_module(ide2):
    vm = make_vm(module_params(ide2=ide2))
    expected = "{0},media=cdrom".format(ide2)

    assert vm._creation_parameters()["ide2"] == expected
    assert vm.get_update_parameters({"ide2": expected}) == {}


def test_create_keeps_non_default_parameters():
    api = FakeApi()
    params = module_params(
        machine={"type": "q35", "version": "latest"},
        memory=4096,
        name="test-vm",
        vga="qxl",
        scsihw="virtio-scsi-pci",
        network=[
            {"name": "net0", "model": "virtio", "bridge": "vmbr0", "firewall": True, "tag": 42},
            {"name": "net1", "model": "e1000", "bridge": "vmbr1", "firewall": False},
        ],
    )

    make_vm(params, api).create_resource()

    assert api.create_parameters == {
        "vmid": 100,
        "name": "test-vm",
        "memory": 4096,
        "machine": "q35",
        "vga": "qxl",
        "scsihw": "virtio-scsi-pci",
        "net0": "virtio,bridge=vmbr0,firewall=1,tag=42",
        "net1": "e1000,bridge=vmbr1,firewall=0",
    }


@pytest.mark.parametrize("version", ["10.0", "10.0+pve1"])
def test_create_serializes_pinned_machine_version(version):
    api = FakeApi()
    params = module_params(machine={"type": "q35", "version": version})

    make_vm(params, api).create_resource()

    assert api.create_parameters["machine"] == "pc-q35-{0}".format(version)
    assert api.machine_get_calls == ["nodes/pve01/capabilities/qemu/machines"]


def test_create_rejects_unsupported_machine_version_before_creating_vm():
    api = FakeApi()
    vm = make_vm(
        module_params(machine={"type": "q35", "version": "10.4"}),
        api,
    )
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.create_resource()

    assert "pc-q35-10.4 is not available" in vm.module.fail_json.call_args.kwargs["msg"]
    assert api.create_parameters is None
    assert api.machine_get_calls == ["nodes/pve01/capabilities/qemu/machines"]


def test_cpu_argument_matches_api_parameter_name():
    vm = make_vm(module_params(cpu="host"))

    assert vm._desired_parameters() == {"cpu": "host"}


def test_empty_config_values_match_parameter_defaults():
    vm = make_vm(
        module_params(
            memory=512,
            cores=1,
            sockets=1,
            cpu="kvm64",
            bios="seabios",
            ostype="other",
            scsihw="lsi",
        )
    )
    resource = {
        "memory": "",
        "cores": None,
        "cpu": "",
        "ostype": "",
        "scsihw": "",
    }

    assert vm.get_update_parameters(resource) == {}


def test_scsi_controller_default_is_omitted_on_create():
    api = FakeApi()
    vm = make_vm(module_params(scsihw="lsi"), api)

    vm.create_resource()

    assert api.create_parameters == {"vmid": 100}


def test_numeric_config_strings_match_integer_parameters():
    vm = make_vm(module_params(memory=512))

    assert vm.get_update_parameters({"memory": "512"}) == {}


@pytest.mark.parametrize(
    ("requested", "current", "expected"),
    [
        ({"type": "q35", "version": "latest"}, "pc-q35-latest", {"machine": "q35"}),
        ({"type": "q35", "version": "latest"}, "q35", {}),
        ({"type": "i440fx", "version": "latest"}, "i440fx", {}),
        ({"type": "q35", "version": "latest"}, "pc-q35-5.1", {"machine": "q35"}),
        ({"type": "i440fx", "version": "latest"}, "pc-i440fx-5.1", {"delete": "machine"}),
        ({"type": "q35", "version": "10.0"}, "pc-q35-10.0", {}),
        ({"type": "q35", "version": "10.0+pve1"}, "pc-q35-10.0+pve1", {}),
        ({"type": "q35", "version": "10.0"}, "pc-q35-10.0+pve1", {"machine": "pc-q35-10.0"}),
        ({"type": "q35", "version": "10.0+pve1"}, "pc-q35-10.0", {"machine": "pc-q35-10.0+pve1"}),
        ({"type": "i440fx", "version": "10.0+pve1"}, "pc-i440fx-10.0+pve1", {}),
        ({"type": "q35", "version": "latest"}, "pc-q35-10.0+pve1", {"machine": "q35"}),
    ],
)
def test_machine_comparison_handles_latest_and_pinned_versions(requested, current, expected):
    vm = make_vm(module_params(machine=requested))

    assert vm.get_update_parameters({"machine": current}) == expected


def test_machine_comparison_still_detects_chipset_change():
    vm = make_vm(module_params(machine={"type": "q35", "version": "latest"}))

    assert vm.get_update_parameters({"machine": "pc-i440fx-11.0"}) == {"machine": "q35"}


def test_i440fx_latest_removes_machine_pin_and_is_idempotent():
    api = FakeApi()
    vm = make_vm(
        module_params(machine={"type": "i440fx", "version": "latest"}, ostype="l26"),
        api,
    )
    current = {"machine": "pc-i440fx-10.2", "ostype": "l26"}

    updates = vm.get_update_parameters(current)

    assert updates == {"delete": "machine"}
    updated = vm.update_resource(current, updates)
    assert api.update_parameters == {"delete": "machine"}
    assert "machine" not in updated
    assert vm.get_update_parameters(updated) == {}


@pytest.mark.parametrize(
    ("requested_bios", "current_bios"),
    [("seabios", "ovmf"), ("ovmf", "seabios")],
)
def test_bios_change_preserves_existing_efi_disk(requested_bios, current_bios):
    api = FakeApi()
    vm = make_vm(
        module_params(
            bios=requested_bios,
            efidisk={"storage": "company-lvm", "efitype": "4m", "pre_enrolled_keys": True},
        ),
        api,
    )
    resource = {
        "bios": current_bios,
        "efidisk0": "local-lvm:vm-100-disk-1,efitype=4m,ms-cert=2023k,pre-enrolled-keys=1,size=4M",
    }

    expected = {"delete": "bios"} if requested_bios == "seabios" else {"bios": "ovmf"}
    updates = vm.get_update_parameters(resource)
    assert updates == expected

    vm.update_resource(resource, updates)

    assert api.update_parameters == expected
    assert "efidisk0" not in api.update_parameters


def test_ovmf_update_adds_efi_disk_only_when_missing():
    api = FakeApi()
    vm = make_vm(
        module_params(
            bios="ovmf",
            efidisk={"storage": "company-lvm", "efitype": "4m", "pre_enrolled_keys": True},
        ),
        api,
    )
    api.storage_entries = [
        {"storage": "company-lvm", "active": 1, "enabled": 1, "content": "images"}
    ]

    updates = vm.get_update_parameters({"bios": "seabios"})
    resource = {"bios": "seabios"}
    vm.update_resource(resource, updates)

    assert updates == {
        "bios": "ovmf",
        "efidisk0": "company-lvm:1,efitype=4m,pre-enrolled-keys=1",
    }
    assert api.update_parameters == updates
    assert api.storage_get_calls == [{"content": "images", "enabled": 1}]


@pytest.mark.parametrize("status", ["running", "paused"])
def test_config_update_requires_vm_to_be_stopped(status):
    vm = make_vm(module_params(memory=4096))
    vm._vm_status = status
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.get_update_parameters({"memory": 2048})

    assert "power it off" in vm.module.fail_json.call_args.kwargs["msg"]


def test_config_update_fails_closed_when_vm_status_is_unknown():
    vm = make_vm(module_params(memory=4096))
    vm._vm_status = None
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.get_update_parameters({"memory": 2048})

    assert "Unable to verify VM power state" in vm.module.fail_json.call_args.kwargs["msg"]


def test_running_vm_with_no_config_changes_is_allowed():
    vm = make_vm(module_params(memory=4096))
    vm._vm_status = "running"

    assert vm.get_update_parameters({"memory": 4096}) == {}


def test_create_does_not_require_existing_vm_power_state():
    vm = make_vm()
    vm.module.check_mode = False
    vm.module.exit_json = Mock(side_effect=SystemExit)
    vm.get_resource = Mock(return_value=None)
    vm.create_resource = Mock(return_value={"name": "created-vm"})

    with pytest.raises(SystemExit):
        vm.execute_resource_module()

    vm.create_resource.assert_called_once_with()


def test_absent_state_rejects_running_vm_before_delete():
    api = Mock()
    vm = make_vm(module_params(state="absent"), api)
    vm.module.check_mode = False
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    vm.get_resource = Mock(return_value={"vmid": 100})
    vm._vm_status = "running"

    with pytest.raises(RuntimeError, match="module failed"):
        vm.execute_resource_module()

    assert "VM is running; power it off before deleting it." in vm.module.fail_json.call_args.kwargs["msg"]
    api.nodes.return_value.qemu.return_value.delete.assert_not_called()


def test_absent_state_check_mode_rejects_running_vm():
    vm = make_vm(module_params(state="absent"))
    vm._vm_status = "running"
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.get_check_mode_resource("delete", resource={"vmid": 100})

    assert "power it off before deleting it" in vm.module.fail_json.call_args.kwargs["msg"]


def test_absent_state_deletes_stopped_vm():
    api = Mock()
    api.nodes.return_value.qemu.return_value.delete.return_value = "UPID:pve01:delete"
    api.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    vm = make_vm(module_params(state="absent"), api)
    vm._vm_status = "stopped"

    assert vm.delete_resource({"vmid": 100}) is None
    assert vm.prepare_result(changed=True) == {
        "changed": True,
        "vm": {"vmid": "100"},
        "task_upid": "UPID:pve01:delete",
    }
    api.nodes.return_value.tasks.assert_called_once_with("UPID:pve01:delete")


def test_omitted_parameters_do_not_change_existing_values():
    vm = make_vm()
    resource = {
        "memory": 4096,
        "cores": 4,
        "cpu": "host",
        "machine": "q35",
        "ostype": "win10",
        "vga": "qxl",
        "net0": "virtio,bridge=vmbr0",
    }

    assert vm.get_update_parameters(resource) == {}


def test_network_list_serializes_fields_and_deletes_stale_devices():
    vm = make_vm(
        module_params(
            network=[
                {"name": "net0", "model": "virtio", "bridge": "vmbr0", "firewall": True, "tag": 42}
            ]
        )
    )
    resource = {
        "net0": "e1000,bridge=vmbr0",
        "net1": "e1000,bridge=stale-bridge",
    }

    assert vm.get_update_parameters(resource) == {
        "net0": "virtio,bridge=vmbr0,firewall=1,tag=42",
        "delete": "net1",
    }


def test_formatted_value_parser_returns_literal_fields():
    assert ProxmoxResourceModule._parse_formatted_options(
        "virtio=BC:24:11:E4:07:3D,bridge=vmbr0,firewall=1"
    ) == {
        "virtio": "BC:24:11:E4:07:3D",
        "bridge": "vmbr0",
        "firewall": "1",
    }


def test_network_comparison_checks_only_requested_suboptions():
    vm = make_vm(
        module_params(
            network=[
                {"name": "net0", "model": "virtio", "bridge": "vmbr0", "firewall": True, "tag": 42}
            ]
        )
    )
    resource = {"net0": "virtio=BC:24:11:E4:07:3D,bridge=vmbr0,firewall=1,tag=42"}

    assert vm.get_update_parameters(resource) == {}


def test_network_model_change_is_detected_with_server_generated_mac():
    vm = make_vm(
        module_params(network=[{"name": "net0", "model": "virtio", "bridge": "vmbr0"}])
    )
    resource = {"net0": "e1000=BC:24:11:E4:07:3D,bridge=vmbr0"}

    assert vm.get_update_parameters(resource) == {"net0": "virtio,bridge=vmbr0"}


def test_network_create_leaves_mac_generation_to_proxmox():
    api = FakeApi()
    vm = make_vm(
        module_params(network=[{"name": "net0", "model": "virtio", "bridge": "vmbr0"}]),
        api,
    )

    vm.create_resource()

    assert api.create_parameters == {"vmid": 100, "net0": "virtio,bridge=vmbr0"}


def test_network_entry_requires_the_declared_prefix():
    vm = make_vm(module_params(network=[{"name": "foo0", "model": "virtio"}]))
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm._creation_parameters()

    assert "net<index>" in vm.module.fail_json.call_args.kwargs["msg"]


def test_unspecified_network_suboptions_do_not_trigger_updates():
    vm = make_vm(
        module_params(network=[{
            "name": "net0", "model": "virtio", "bridge": None,
            "firewall": None, "tag": None,
        }])
    )

    assert vm.get_update_parameters({
        "net0": "virtio=BC:24:11:E4:07:3D,bridge=vmbr0,firewall=1"
    }) == {}


def test_formatted_parameter_helpers_support_other_indexed_families():
    vm = make_vm()
    formatted = {
        "disks": [
            {"name": "scsi0", "file": "local-lvm:vm-100-disk-0", "size": "32G"},
        ]
    }
    formatted_parameters = ["disks"]
    prefixes = {"disks": "scsi"}
    primary_fields = {"disks": "file"}

    assert vm.serialize_formatted_parameters(
        formatted,
        formatted_parameters,
        prefixes,
        primary_fields,
    ) == {"scsi0": "local-lvm:vm-100-disk-0,size=32G"}
    assert vm.get_formatted_parameter_updates(
        {"scsi0": "local-lvm:vm-100-disk-0,size=32G"},
        formatted,
        formatted_parameters,
        prefixes,
        primary_fields,
    ) == {}


def test_empty_network_list_deletes_all_existing_devices():
    vm = make_vm(module_params(network=[]))

    assert vm.get_update_parameters({"net0": "virtio,bridge=vmbr0", "net1": "e1000"}) == {
        "delete": "net0,net1"
    }


def test_update_uses_delete_for_defaults_and_updates_other_values():
    vm = make_vm(module_params(ostype="other"))
    resource = {"machine": "q35", "ostype": "win10"}

    assert vm.get_update_parameters(resource) == {"delete": "ostype"}


def run_power_cycle(params, status=None, resource=None, check_mode=False, monkeypatch=None):
    """Run one VM reconciliation and record API mutations, task waits, and status checks."""
    api = Mock()
    qemu = api.nodes.return_value.qemu.return_value
    events = []

    def mutation(action, upid):

        def invoke(**kwargs):
            events.append((action, kwargs))
            return upid

        return invoke

    api.nodes.return_value.qemu.create.side_effect = mutation("create", "UPID:pve01:create")
    qemu.config.post.side_effect = mutation("update", "UPID:pve01:update")
    qemu.delete.side_effect = mutation("delete", "UPID:pve01:delete")
    qemu.status.stop.post.side_effect = mutation("stop", "UPID:pve01:stop")
    qemu.status.start.post.side_effect = mutation("start", "UPID:pve01:start")

    def current_status():
        action = next(event[0] for event in reversed(events) if event[0] in ("start", "stop"))
        events.append(("status", {}))
        return {"status": "running" if action == "start" else "stopped"}

    qemu.status.current.get.side_effect = current_status
    qemu.config.get.return_value = {"memory": params.get("memory") or 512}
    monkeypatch.setattr(
        ProxmoxTask, "wait_for_task",
        lambda self, upid, **kwargs: events.append(("wait", upid)),
    )
    vm = make_vm(module_params(**params), api)
    vm._vm_status = status
    vm.get_resource = Mock(return_value=resource)
    vm.module.check_mode = check_mode
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    vm.module.exit_json = Mock(side_effect=SystemExit)

    with pytest.raises(SystemExit):
        vm.execute_resource_module()
    return vm.module.exit_json.call_args.kwargs, events


@pytest.mark.parametrize(
    ("power_cycle", "expected"),
    [
        ("on", ["create", "wait", "start", "wait", "status"]),
        ("off", ["create", "wait"]),
        ("fail", ["create", "wait"]),
        ("hotplug", ["create", "wait"]),
    ],
)
def test_power_cycle_creation(power_cycle, expected, monkeypatch):
    result, events = run_power_cycle({"power_cycle": power_cycle}, monkeypatch=monkeypatch)

    assert result["changed"] is True
    assert [event[0] for event in events] == expected
    assert result["task_upid"] == ("UPID:pve01:start" if power_cycle == "on" else "UPID:pve01:create")


@pytest.mark.parametrize(
    ("power_cycle", "status", "memory", "expected"),
    [
        ("on", "running", 2048, ["stop", "wait", "status", "update", "wait", "start", "wait", "status"]),
        ("on", "stopped", 2048, ["update", "wait", "start", "wait", "status"]),
        ("on", "stopped", 512, ["start", "wait", "status"]),
        ("on", "running", 512, []),
        ("off", "running", 2048, ["stop", "wait", "status", "update", "wait"]),
        ("off", "stopped", 2048, ["update", "wait"]),
        ("off", "running", 512, ["stop", "wait", "status"]),
        ("off", "stopped", 512, []),
        ("hotplug", "running", 2048, ["update", "wait"]),
        ("hotplug", "running", 512, []),
        ("fail", "stopped", 2048, ["update", "wait"]),
        ("fail", "running", 512, []),
    ],
)
def test_power_cycle_update_and_idempotency(power_cycle, status, memory, expected, monkeypatch):
    result, events = run_power_cycle(
        {"power_cycle": power_cycle, "memory": memory},
        status=status, resource={"memory": 512}, monkeypatch=monkeypatch,
    )

    assert [event[0] for event in events] == expected
    assert result["changed"] is bool(expected)
    assert result["vm"]["memory"] == (memory if expected and memory != 512 else 512)
    if expected:
        assert result["task_upid"] == "UPID:pve01:{0}".format(
            next(action for action in reversed(expected) if action in ("start", "stop", "update"))
        )
    else:
        assert "task_upid" not in result


@pytest.mark.parametrize("power_cycle", ["on", "off", "fail", "hotplug"])
def test_power_cycle_check_mode_predicts_create_without_mutation(power_cycle, monkeypatch):
    result, events = run_power_cycle({"power_cycle": power_cycle}, check_mode=True, monkeypatch=monkeypatch)

    assert result["changed"] is True
    assert "task_upid" not in result
    assert events == []


@pytest.mark.parametrize("power_cycle,status,memory,changed", [
    ("on", "running", 2048, True), ("on", "stopped", 512, True),
    ("off", "running", 512, True), ("hotplug", "running", 2048, True),
    ("fail", "stopped", 512, False),
])
def test_power_cycle_check_mode_predicts_updates_without_mutation(power_cycle, status, memory, changed, monkeypatch):
    result, events = run_power_cycle(
        {"power_cycle": power_cycle, "memory": memory}, status=status,
        resource={"memory": 512}, check_mode=True, monkeypatch=monkeypatch,
    )

    assert result["changed"] is changed
    assert result["vm"]["memory"] == (memory if changed and memory != 512 else 512)
    assert "task_upid" not in result
    assert events == []


@pytest.mark.parametrize("status", ["running", "stopped"])
def test_power_cycle_off_deletes_vm_and_waits_for_stop(status, monkeypatch):
    result, events = run_power_cycle(
        {"state": "absent", "power_cycle": "off"}, status=status,
        resource={"vmid": 100}, monkeypatch=monkeypatch,
    )

    assert result["changed"] is True
    assert result["vm"] == {"vmid": "100"}
    assert [event[0] for event in events] == (
        ["stop", "wait", "status", "delete", "wait"] if status == "running" else ["delete", "wait"]
    )
    assert result["task_upid"] == "UPID:pve01:delete"


def test_power_cycle_off_delete_check_mode_keeps_running_vm(monkeypatch):
    result, events = run_power_cycle(
        {"state": "absent", "power_cycle": "off"}, status="running",
        resource={"vmid": 100}, check_mode=True, monkeypatch=monkeypatch,
    )

    assert result["changed"] is True
    assert events == []


def test_power_cycle_off_absent_vm_is_idempotent(monkeypatch):
    result, events = run_power_cycle(
        {"state": "absent", "power_cycle": "off"}, monkeypatch=monkeypatch,
    )

    assert result["changed"] is False
    assert events == []


def test_default_fail_policy_rejects_running_updates_before_mutation(monkeypatch):
    api = Mock()
    vm = make_vm(module_params(memory=2048), api)
    vm._vm_status = "running"
    vm.get_resource = Mock(return_value={"memory": 512})
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))

    with pytest.raises(RuntimeError, match="module failed"):
        vm.execute_resource_module()

    api.nodes.return_value.qemu.return_value.config.post.assert_not_called()
    api.nodes.return_value.qemu.return_value.status.stop.post.assert_not_called()


@pytest.mark.parametrize("power_cycle", ["on", "hotplug"])
@pytest.mark.parametrize("check_mode", [False, True])
def test_power_cycle_rejects_invalid_absent_policy_before_api_lookup(power_cycle, check_mode):
    vm = make_vm(module_params(state="absent", power_cycle=power_cycle))
    vm.module.check_mode = check_mode
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    vm.get_resource = Mock()

    with pytest.raises(RuntimeError, match="module failed"):
        vm.execute_resource_module()

    assert "power_cycle must be off or fail" in vm.module.fail_json.call_args.kwargs["msg"]
    vm.get_resource.assert_not_called()


@pytest.mark.parametrize("power_cycle", ["on", "off", "hotplug", "fail"])
def test_power_cycle_refuses_unknown_status_before_mutation(power_cycle, monkeypatch):
    vm = make_vm(module_params(power_cycle=power_cycle, memory=2048))
    vm.module.check_mode = False
    vm.module.fail_json = Mock(side_effect=RuntimeError("module failed"))
    vm.get_resource = Mock(return_value={"memory": 512})
    vm._vm_status = None
    vm.update_resource = Mock()

    with pytest.raises(RuntimeError, match="module failed"):
        vm.execute_resource_module()

    assert "Unable to verify VM power state" in vm.module.fail_json.call_args.kwargs["msg"]
    vm.update_resource.assert_not_called()


def test_update_and_check_mode_remove_deleted_default_key():
    api = FakeApi()
    vm = make_vm(api=api)
    resource = {"vmid": 100, "machine": "q35"}
    updates = {"delete": "machine"}

    assert vm.update_resource(resource, updates) == {"vmid": 100}
    assert api.update_parameters == updates
    assert api.task_upids == ["UPID:pve01:task"]
    assert vm.get_check_mode_resource("update", resource, updates) == {"vmid": 100}


def test_default_filter_leaves_parameters_without_default_unchanged():
    parameters = {"name": "", "machine": "i440fx", "vga": "qxl", "custom": ""}
    defaults = {"name": "", "machine": "i440fx", "vga": ""}

    assert ProxmoxResourceModule.omit_default_parameters(parameters, defaults) == {
        "name": "",
        "vga": "qxl",
        "custom": "",
    }
