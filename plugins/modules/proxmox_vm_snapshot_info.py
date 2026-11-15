#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_snapshot_info
short_description: Retrieve QEMU/KVM virtual machine snapshot information
description:
  - Returns all snapshot records for a QEMU/KVM virtual machine when O(snapname) is omitted.
  - The complete snapshot list includes the special V(current) entry representing the current VM configuration.
  - When O(snapname) is provided, retrieves only that saved snapshot's configuration.
  - Returns an empty list when the VM or requested snapshot does not exist.
  - Optional snapshot fields are returned when supplied by Proxmox.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Proxmox VE node containing the virtual machine.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the QEMU/KVM virtual machine.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  snapname:
    description:
      - Retrieve the saved configuration of this snapshot.
      - When omitted, retrieves all snapshot summaries, including the V(current) entry.
      - Returns an empty list when the VM or requested snapshot does not exist.
    type: str
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
seealso:
  - module: ansible.proxmox.proxmox_vm_snapshot
notes:
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: List all snapshots of a VM
  ansible.proxmox.proxmox_vm_snapshot_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
  register: vm_snapshots

- name: Retrieve one saved snapshot configuration
  ansible.proxmox.proxmox_vm_snapshot_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
  register: vm_snapshot
"""

RETURN = r"""
snapshots:
  description:
    - All snapshot summaries, including the current VM entry, when O(snapname) is omitted.
    - A single snapshot's saved configuration when O(snapname) is provided.
    - An empty list when the VM or requested snapshot does not exist.
  returned: success
  type: list
  elements: dict
  contains:
    description:
      description: Snapshot description.
      returned: when reported by Proxmox
      type: str
    name:
      description: Snapshot identifier; V(current) identifies the current VM.
      returned: always
      type: str
    parent:
      description: Parent snapshot identifier.
      returned: when reported by Proxmox
      type: str
    snaptime:
      description: Snapshot creation time as a Unix timestamp.
      returned: when reported by Proxmox
      type: int
    vmstate:
      description: Whether the snapshot includes RAM.
      returned: when reported by Proxmox
      type: bool
  sample:
    - name: before_upgrade
      description: Before the application upgrade
      snaptime: 1700000000
      vmstate: true
    - name: current
      description: You are here!
      parent: before_upgrade
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxVmSnapshotInfoModule(ProxmoxInfoModule):
    """Retrieve snapshot information through the shared read-only lifecycle."""

    result_key = "snapshots"

    def get_info(self):
        """Retrieve all snapshot summaries or one saved snapshot configuration.

        Returns:
            list: Selected snapshot records with normalized boolean RAM flags.
        """
        params = self.module.params
        vmid = params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        self.client.validate_pve_permissions(self.api, "/vms")
        node = params["node"]
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        if not self.validate_proxmox_resource_exist(
            "VM",
            vmid,
            lambda: self.api.get("nodes/{0}/qemu".format(node)),
            resource_key="vmid",
            fail_on_missing=False,
        ):
            return []

        snapshot_api = self.api.nodes(params["node"]).qemu(vmid).snapshot
        snapname = params.get("snapname")
        if snapname is not None:
            if not self.validate_proxmox_resource_exist(
                "snapshot",
                snapname,
                snapshot_api.get,
                resource_key="name",
                fail_on_missing=False,
            ):
                return []
            snapshot = self.call_api(
                "Retrieving snapshot {0} of VM {1} on node {2}".format(snapname, vmid, params["node"]),
                snapshot_api(snapname).config.get,
            )
            snapshots = [dict(snapshot, name=snapname)] if isinstance(snapshot, dict) else []
        else:
            snapshots = self.call_api(
                "Retrieving snapshots of VM {0} on node {1}".format(vmid, params["node"]),
                snapshot_api.get,
            ) or []
        for snapshot in snapshots:
            if "vmstate" in snapshot:
                snapshot["vmstate"] = bool(snapshot["vmstate"]) and snapshot["vmstate"] != "0"
        return snapshots


def main():
    """Parse arguments and return the selected VM snapshot information."""
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "node": {"type": "str", "required": True},
            "vmid": {"type": "int", "required": True},
            "snapname": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmSnapshotInfoModule(module).execute_info_module()


if __name__ == "__main__":
    main()
