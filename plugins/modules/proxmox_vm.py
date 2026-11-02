#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm
short_description: Manage a QEMU/KVM virtual machine
description:
  - Creates, updates, and deletes a QEMU/KVM virtual machine in Proxmox VE.
  - The VM ID is the unique identifier used for idempotent lifecycle operations.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: The Proxmox VE node on which to manage the VM.
    type: str
    required: true
  vmid:
    description: The unique ID of the VM. Valid values range from 100 to 999999999.
    type: int
    required: true
  name:
    description: The name of the virtual machine.
    type: str
  memory:
    description: Amount of RAM in MiB. When omitted, defaults to 512 MiB.
    type: int
  cores:
    description: Number of CPU cores per socket. When omitted, defaults to 1.
    type: int
  sockets:
    description: Number of CPU sockets. When omitted, defaults to 1.
    type: int
  cpu:
    description: CPU type to emulate. When omitted, defaults to C(kvm64).
    type: str
  bios:
    description:
      - BIOS implementation. When omitted, defaults to C(seabios).
      - Selecting C(ovmf) requires C(efidisk). An existing EFI disk is preserved if BIOS is later changed.
    type: str
    choices: [ovmf, seabios]
  efidisk:
    description:
      - EFI disk settings required for C(state=present) with C(bios=ovmf).
      - EFI disk cannot be changed or removed after creation; an existing disk is preserved.
    type: dict
    suboptions:
      storage:
        description: Enabled node storage that supports disk images.
        type: str
      efitype:
        description: EFI variables disk format.
        type: str
        choices: [2m, 4m]
        default: 4m
      pre_enrolled_keys:
        description: Enable pre-enrolled keys for Secure Boot.
        type: bool
        default: true
  machine:
    description: QEMU machine model and version. When omitted, defaults to C(i440fx).
    type: dict
    suboptions:
      type:
        description: Machine chipset type.
        type: str
        required: true
        choices: [q35, i440fx]
      version:
        description:
          - Machine version, or C(latest) to use the current default version.
          - C(latest) removes an explicit version pin. For the default C(i440fx) chipset,
            this resets the API machine option, which can then be omitted from configuration responses.
        type: str
        default: latest
  ostype:
    description:
      - Proxmox guest operating system type. When omitted, defaults to C(other).
      - C(win10) is Microsoft Windows 10/2016/2019.
      - C(win11) is Microsoft Windows 11/2022/2025.
      - C(l24) is Linux 2.4 Kernel.
      - C(l26) is Linux 2.6 - 7.X Kernel.
      - C(solaris) is Solaris/OpenSolaris/OpenIndiana kernel.
    type: str
    choices: [win10, win11, l24, l26, solaris, other]
  vga:
    description: VGA device type. See notes and the Proxmox API documentation for available options.
    type: str
  scsihw:
    description: SCSI controller model. When omitted, defaults to C(lsi).
    type: str
  ide2:
    description: CD-ROM drive media.
    type: str
    choices: [none, cdrom]
  disk:
    description:
      - Disks to create with the VM. Disk definitions are only applied during VM creation.
      - Existing VM disks are not changed or removed during updates.
      - Each device may appear only once. The same index may be used for different device types.
    type: list
    elements: dict
    suboptions:
      device:
        description:
          - Proxmox disk device.
          - Valid ranges are C(ide0) through C(ide3), C(sata0) through C(sata5),
            C(virtio0) through C(virtio15), and C(scsi0) through C(scsi30).
          - C(ide2) cannot be used when the separate O(ide2) option is configured.
        type: str
        required: true
      storage:
        description: Proxmox storage identifier where the disk will be created.
        type: str
        required: true
      size:
        description: Disk size in GiB, such as C(32G).
        type: str
        required: true
  network:
    description: Network interfaces to configure on the VM.
    type: list
    elements: dict
    suboptions:
      name:
        description: Indexed Proxmox network parameter name, such as C(net0) or C(net1).
        type: str
        required: true
      model:
        description: Network device model.
        type: str
        required: true
      bridge:
        description: Bridge to attach the network device to.
        type: str
      firewall:
        description: Enable the Proxmox firewall on this network device.
        type: bool
      tag:
        description: VLAN tag for this network device.
        type: int
  task_timeout:
    description:
      - Maximum time in seconds to wait for each asynchronous VM create, update, delete, start, or stop task.
      - When omitted, the task-waiting function uses its default timeout of 300 seconds.
      - Set to C(0) to wait indefinitely. Negative values are not allowed.
      - Increase this value for large disks or slow storage when a finite timeout is preferred.
      - Reaching this timeout fails the module but does not cancel the task on the Proxmox server.
    type: int
  power_cycle:
    description:
      - C(fail) configuration changes and deletion require a stopped VM, fail if vm is not powered off.
      - C(on) starts a newly created VM, and stops, updates, then restarts a running VM when changes are needed.
        An existing stopped VM is started even if its configuration is unchanged.
      - C(off) stops a running VM before updating or deleting it and leaves a present VM stopped.
      - C(hotplug) updates the configuration without stopping a running VM; a newly created VM remains off.
        Proxmox determines which settings can take effect immediately; unsupported live changes may be
        pending until a reboot. Pending configuration is used for idempotency checks in this mode.
      - Only C(off) and C(fail) are valid with C(state=absent).
    type: str
    choices: ['on', 'off', fail, hotplug]
    default: fail
  state:
    description:
      - Whether the VM should be created/updated or deleted.
      - The power-state policy is controlled by O(power_cycle).
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
notes:
  - For Proxmox VE, the authenticated identity must have all privileges of the C(Administrator) role,
    with propagation enabled, on C(/vms), C(/sdn/zones), and C(/storage).
  - These permission checks run before VM lookup for every operation, including check mode,
    regardless of whether the requested configuration uses SDN or storage.
  - For QEMU VM configuration options, see U(https://pve.proxmox.com/pve-docs/api-viewer/index.html#/nodes/{node}/qemu).
  - With the default O(power_cycle=fail), an existing VM must report C(stopped) before configuration
    changes or deletion, including in check mode.
  - Power operations use the Proxmox stop and start APIs and wait for their tasks to finish.
"""

EXAMPLES = r"""
- name: Create or update a VM
  ansible.proxmox.proxmox_vm:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 100
    name: my-linux-vm
    memory: 4096
    cores: 2
    sockets: 2
    cpu: kvm64
    bios: ovmf
    efidisk:
      storage: local-lvm
      efitype: 4m
      pre_enrolled_keys: true
    machine:
      type: q35
      version: latest
    ostype: l26
    vga: std
    scsihw: virtio-scsi-single
    ide2: cdrom
    disk:
      - device: scsi0
        storage: local-lvm
        size: 32G
      - device: scsi1
        storage: local-lvm
        size: 32G
    network:
      - name: net0
        model: virtio
        bridge: vmbr0
        firewall: true
        tag: 106
    task_timeout: 900
    power_cycle: 'on'
    state: present

- name: Create a default VM with https token with no timeout
  ansible.proxmox.proxmox_vm:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_token_secret }}"
    ca_path: /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
    node: pve01
    vmid: 200
    task_timeout: 0

- name: Remove a VM
  ansible.proxmox.proxmox_vm:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 100
    state: absent
"""

RETURN = r"""
vm:
  description:
    - VM configuration including the targeted VM ID as a string in C(vmid).
    - Existing VM configurations include C(machine=i440fx) when the API omits the default machine.
    - When the VM is absent or has been deleted, contains only C(vmid).
  returned: always
  type: dict
  sample: {"vmid": "100", "name": "my-linux-vm", "memory": 4096, "cores": 2}
task_upid:
  description: Proxmox task identifier for the last asynchronous create, update, delete, start, or stop operation.
  returned: when the API returns an asynchronous task UPID
  type: str
  sample: "UPID:pve01:003E0E8C:09394C3A:6ADF0AE7:qmcreate:102:ansible@pve!part:"
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


FORMATTED_PARAMETERS = ["network", "ide2"]
FORMATTED_PARAMETER_PREFIXES = {"network": "net"}
FORMATTED_PARAMETER_PRIMARY_FIELDS = {"network": "model", "ide2": "device"}
FORMATTED_PARAMETER_FIXED_OPTIONS = {"ide2": {"media": "cdrom"}}
DISK_DEVICE_MAX_INDEX = {"ide": 3, "sata": 5, "virtio": 15, "scsi": 30}


VM_PARAMETER_DEFAULT = {
    "name": "",
    "memory": 512,
    "cores": 1,
    "sockets": 1,
    "cpu": "kvm64",
    "bios": "seabios",
    "machine": "i440fx",
    "ostype": "other",
    "vga": "",
    "scsihw": "lsi",
    "ide2": "",
    "network": "",
}


class ProxmoxVmModule(ProxmoxResourceModule):
    result_key = "vm"

    @property
    def power(self):
        return ProxmoxPower(self.module, client=self.client)

    def _vm(self):
        return self.api.nodes(self.module.params["node"]).qemu(self.module.params["vmid"])

    def _validate_efidisk_parameters(self):
        if self.module.params.get("state", "present") != "present":
            return
        if self.module.params.get("bios") != "ovmf":
            return
        efidisk = self.module.params.get("efidisk")
        if not isinstance(efidisk, dict) or not efidisk.get("storage"):
            self.module.fail_json(msg="bios is ovmf but all of the following are missing: efidisk")

    def execute_resource_module(self):
        """Validate the request and reconcile the VM and its power policy."""
        state, power_cycle = self._validated_request()
        resource = self.get_resource()
        if state == "absent":
            self._execute_vm_deletion(resource, power_cycle)
            return
        self._execute_vm_present(resource, power_cycle)

    def _validated_request(self):
        """Validate VM identifier, power policy, and creation-only options.

        Returns:
            Requested VM state and power-cycle policy, in that order.
        """
        vmid = self.module.params.get("vmid")
        if vmid is not None and not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        state = self.module.params.get("state", "present")
        power_cycle = self.module.params.get("power_cycle", "fail")
        self.power.validate_policy(state, power_cycle)
        self._validate_efidisk_parameters()
        return state, power_cycle

    def _execute_vm_deletion(self, resource, power_cycle):
        """Delete a VM, stopping it first only when the requested policy allows.

        Args:
            resource: Current VM configuration, or None when the VM is absent.
            power_cycle: Requested power-cycle policy.
        """
        if resource is None:
            self.exit(changed=False, resource=None)
        self.power.validate_delete(self._vm_status, power_cycle)
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("delete", resource=resource))
        if self.power.should_stop(self._vm_status, power_cycle, False):
            self._apply_power_state("stop")
        self.exit(changed=True, resource=self.delete_resource(resource))

    def _execute_vm_present(self, resource, power_cycle):
        """Create a missing VM or reconcile an existing one.

        Args:
            resource: Current VM configuration, or None when the VM is absent.
            power_cycle: Requested power-cycle policy.
        """
        if resource is None:
            self._create_vm_for_policy(power_cycle)
            return
        self._update_vm_for_policy(resource, power_cycle)

    def _create_vm_for_policy(self, power_cycle):
        """Create a VM and apply its requested initial power state.

        Args:
            power_cycle: Requested power-cycle policy.
        """
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("create"))
        resource = self.create_resource()
        if self.power.should_start("stopped", power_cycle):
            self._apply_power_state("start")
        self.exit(changed=True, resource=resource)

    def _update_vm_for_policy(self, resource, power_cycle):
        """Reconcile existing VM configuration and requested power state.

        Args:
            resource: Current VM configuration.
            power_cycle: Requested power-cycle policy.
        """
        updates = self.get_update_parameters(resource)
        power_change = self.power.needs_change(self._vm_status, power_cycle)
        if not updates and not power_change:
            self.exit(changed=False, resource=resource)
        if self.module.check_mode:
            self.exit(
                changed=True,
                resource=self.get_check_mode_resource("update", resource=resource, updates=updates),
            )
        self._apply_existing_vm_updates(resource, updates, power_cycle)

    def _apply_existing_vm_updates(self, resource, updates, power_cycle):
        """Stop, update, and start an existing VM as requested.

        Args:
            resource: Current VM configuration.
            updates: Configuration changes to apply.
            power_cycle: Requested power-cycle policy.
        """
        if self.power.should_stop(self._vm_status, power_cycle, bool(updates)):
            self._apply_power_state("stop")
        if updates:
            resource = self.update_resource(resource, updates)
        if self.power.should_start(self._vm_status, power_cycle):
            self._apply_power_state("start")
        self.exit(changed=True, resource=resource)

    def _apply_power_state(self, action):
        self._task_upid, self._vm_status = self.power.set_state(action)

    def get_resource(self):
        node = self.module.params["node"]
        vmid = self.module.params["vmid"]
        self._vm_status = None
        self.client.validate_pve_permissions(self.api, "/vms")
        self.client.validate_pve_permissions(self.api, "/sdn/zones")
        self.client.validate_pve_permissions(self.api, "/")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        if not self.validate_proxmox_resource_exist(
            "VM",
            vmid,
            self.api.nodes(node).qemu.get,
            resource_key="vmid",
            fail_on_missing=False,
        ):
            return None
        status = self.power.get_status(return_none_on_404=True)
        if status is None:
            return None
        self._vm_status = status.get("status")
        config_options = self.power.configuration_options(self.module.params.get("power_cycle", "fail"))
        return self.call_api(
            "Retrieving VM {0} configuration from node {1}".format(vmid, node),
            self._vm().config.get,
            **config_options
        )

    def _desired_parameters(self):
        params = self.module.params
        desired = {
            parameter: params[parameter]
            for parameter in VM_PARAMETER_DEFAULT
            if params.get(parameter) is not None
        }
        if isinstance(desired.get("machine"), dict):
            machine = desired["machine"]
            machine_type = machine["type"]
            version = machine.get("version", "latest")
            if version.lower() == "latest":
                desired["machine"] = machine_type
            else:
                desired["machine"] = "pc-{0}-{1}".format(machine_type, version)
        return desired

    def _validate_machine_version(self):
        machine = self.module.params.get("machine")
        if not isinstance(machine, dict) or machine.get("version", "latest").lower() == "latest":
            return

        node = self.module.params["node"]
        requested_machine = self._desired_parameters()["machine"]
        if not self.validate_proxmox_resource_exist(
            "machine version",
            requested_machine,
            lambda: self.api.get("nodes/{0}/capabilities/qemu/machines".format(node)),
            resource_key="id",
            fail_on_missing=False,
        ):
            self.module.fail_json(
                msg="Machine version {0} is not available on node {1}.".format(requested_machine, node)
            )

    def _formatted_settings(self):
        return (
            FORMATTED_PARAMETERS,
            FORMATTED_PARAMETER_PREFIXES,
            FORMATTED_PARAMETER_PRIMARY_FIELDS,
        )

    def _creation_parameters(self):
        """Build validated API parameters for VM creation.

        Returns:
            Creation parameters including the VM ID and requested devices.
        """
        parameters = {"vmid": self.module.params["vmid"]}
        formatted_parameters, prefixes, primary_fields = self._formatted_settings()
        desired_parameters = self.serialize_formatted_parameters(
            self._desired_parameters(),
            formatted_parameters,
            prefixes,
            primary_fields,
            FORMATTED_PARAMETER_FIXED_OPTIONS,
        )
        parameters.update(
            self.omit_default_parameters(desired_parameters, VM_PARAMETER_DEFAULT)
        )
        if self.module.params.get("bios") == "ovmf":
            parameters["efidisk0"] = self._efidisk_parameter()
        disk_devices = set()
        for disk in self.module.params.get("disk") or []:
            device = disk["device"]
            match = re.fullmatch(r"(ide|sata|virtio|scsi)(0|[1-9]\d*)", device, flags=re.ASCII) if isinstance(device, str) else None
            if match is None or int(match.group(2)) > DISK_DEVICE_MAX_INDEX[match.group(1)]:
                self.module.fail_json(
                    msg="Invalid disk device {0}; use ide0..ide3, sata0..sata5, virtio0..virtio15, or scsi0..scsi30.".format(device)
                )
            if device in disk_devices:
                self.module.fail_json(msg="Duplicate disk device {0}.".format(device))
            if device in parameters:
                self.module.fail_json(msg="Disk device {0} conflicts with another VM creation parameter.".format(device))
            disk_devices.add(device)
            size = str(disk["size"])
            size = size[:-1] if size.lower().endswith("g") else size
            parameters[device] = "{0}:{1}".format(disk["storage"], size)
        return parameters

    def _wait_for_task(self, result):
        self._task_upid = None
        if not isinstance(result, str) or not result.startswith("UPID:"):
            return
        self._task_upid = result
        task = ProxmoxTask(self.module, client=self.client)
        if self.module.params["task_timeout"] is None:
            task.wait_for_task(result)
        else:
            task.wait_for_task(result, timeout=self.module.params["task_timeout"])

    def prepare_result(self, changed, resource=None, **values):
        vm = dict(resource or {})
        if resource is not None:
            vm.setdefault("machine", "i440fx")
        vm["vmid"] = str(self.module.params["vmid"])
        result = super().prepare_result(changed, resource=vm, **values)
        task_upid = getattr(self, "_task_upid", None)
        if task_upid is not None:
            result["task_upid"] = task_upid
        return result

    def create_resource(self):
        self._validate_machine_version()
        parameters = self._creation_parameters()
        node = self.module.params["node"]
        disks = self.module.params.get("disk") or []
        for storage in dict.fromkeys(disk["storage"] for disk in disks):
            self.validate_proxmox_resource_exist(
                "storage",
                storage,
                self.api.nodes(node).storage.get,
                resource_key="storage",
            )
        if self.module.params.get("bios") == "ovmf":
            self._validate_efi_storage()
        resource = self.call_api(
            "Creating VM {0} on node {1}".format(self.module.params["vmid"], self.module.params["node"]),
            self.api.nodes(self.module.params["node"]).qemu.create,
            **parameters
        )
        self._wait_for_task(resource)
        return self.call_api(
            "Retrieving created VM {0} configuration on node {1}".format(self.module.params["vmid"], node),
            self._vm().config.get,
            current=1,
        )

    def _efidisk_parameter(self):
        efidisk = self.module.params["efidisk"]
        pre_enrolled_keys = "1" if efidisk.get("pre_enrolled_keys", True) else "0"
        return "{0}:1,efitype={1},pre-enrolled-keys={2}".format(
            efidisk["storage"], efidisk["efitype"], pre_enrolled_keys
        )

    def _validate_efi_storage(self):
        node = self.module.params["node"]
        storage = self.module.params["efidisk"]["storage"]
        storages = self.call_api(
            "Retrieving enabled image storages on node {0}".format(node),
            self.api.nodes(node).storage.get,
            content="images",
            enabled=1,
        )
        storage_info = next(
            (
                item
                for item in storages or []
                if isinstance(item, dict) and item.get("storage") == storage
            ),
            None,
        )
        if storage_info is None:
            self.module.fail_json(
                msg="EFI disk storage {0} is not enabled for images on node {1}.".format(storage, node)
            )
        if not storage_info.get("active"):
            self.module.fail_json(
                msg="EFI disk storage {0} is not active on node {1}.".format(storage, node)
            )

    def get_update_parameters(self, resource):
        desired = self._desired_parameters()
        updates = self.get_parameter_updates(
            resource,
            {key: value for key, value in desired.items() if key not in FORMATTED_PARAMETERS},
            VM_PARAMETER_DEFAULT,
        )
        formatted_parameters, prefixes, primary_fields = self._formatted_settings()
        formatted_updates = self.get_formatted_parameter_updates(
            resource,
            desired,
            formatted_parameters,
            prefixes,
            primary_fields,
            fixed_options=FORMATTED_PARAMETER_FIXED_OPTIONS,
        )
        if "delete" in formatted_updates:
            existing = [key for key in updates.get("delete", "").split(",") if key]
            existing.extend(
                key for key in formatted_updates.pop("delete").split(",") if key not in existing
            )
            updates["delete"] = ",".join(existing)
        updates.update(formatted_updates)
        efidisk = self.module.params.get("efidisk")
        if (
            self.module.params.get("bios") == "ovmf"
            and isinstance(efidisk, dict)
            and not resource.get("efidisk0")
        ):
            self._validate_efi_storage()
            updates["efidisk0"] = self._efidisk_parameter()
        if updates:
            self.power.validate_update(getattr(self, "_vm_status", None), self.module.params.get("power_cycle", "fail"))
        if "machine" in updates:
            self._validate_machine_version()
        return updates

    def update_resource(self, resource, updates):
        result = self.call_api(
            "Updating VM {0} on node {1}".format(self.module.params["vmid"], self.module.params["node"]),
            self._vm().config.post,
            **updates
        )
        self._wait_for_task(result)
        return self.apply_parameter_updates(resource, updates)

    def delete_resource(self, resource):
        self.power.require_stopped(getattr(self, "_vm_status", None), "delete")
        result = self.call_api(
            "Deleting VM {0} from node {1}".format(self.module.params["vmid"], self.module.params["node"]),
            self._vm().delete,
        )
        self._wait_for_task(result)
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            return self._creation_parameters()
        if operation == "delete" and resource is not None:
            self.power.validate_delete(getattr(self, "_vm_status", None), self.module.params.get("power_cycle", "fail"))
        return super().get_check_mode_resource(operation, resource, updates)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int", "required": True},
                "task_timeout": {"type": "int"},
                "power_cycle": {
                    "type": "str", "choices": ["on", "off", "fail", "hotplug"],
                    "default": "fail",
                },
                "name": {"type": "str"},
                "memory": {"type": "int"},
                "cores": {"type": "int"},
                "sockets": {"type": "int"},
                "cpu": {"type": "str"},
                "bios": {"type": "str", "choices": ["ovmf", "seabios"]},
                "efidisk": {
                    "type": "dict",
                    "options": {
                        "storage": {"type": "str"},
                        "efitype": {"type": "str", "choices": ["2m", "4m"], "default": "4m"},
                        "pre_enrolled_keys": {"type": "bool", "default": True},
                    },
                },
                "machine": {
                    "type": "dict",
                    "options": {
                        "type": {"type": "str", "required": True, "choices": ["q35", "i440fx"]},
                        "version": {"type": "str", "default": "latest"},
                    },
                },
                "ostype": {
                    "type": "str",
                    "choices": ["win10", "win11", "l24", "l26", "solaris", "other"],
                },
                "vga": {"type": "str"},
                "scsihw": {"type": "str"},
                "ide2": {"type": "str", "choices": ["none", "cdrom"]},
                "disk": {
                    "type": "list",
                    "elements": "dict",
                    "options": {
                        "device": {"type": "str", "required": True},
                        "storage": {"type": "str", "required": True},
                        "size": {"type": "str", "required": True},
                    },
                },
                "network": {
                    "type": "list",
                    "elements": "dict",
                    "options": {
                        "name": {"type": "str", "required": True},
                        "model": {"type": "str", "required": True},
                        "bridge": {"type": "str"},
                        "firewall": {"type": "bool"},
                        "tag": {"type": "int"},
                    },
                },
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmModule(module).execute_resource_module()


if __name__ == "__main__":
    main()
