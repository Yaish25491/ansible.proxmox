#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_info
short_description: Retrieve QEMU/KVM virtual machine information
description:
  - Retrieves virtual machine information from a Proxmox VE node.
  - When I(vmid) is omitted, information for every VM on the node is returned.
  - When I(vmid) is provided, the VM configuration is returned.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description:
      - The Proxmox VE node containing the virtual machine(s).
    type: str
    required: true
  vmid:
    description:
      - Return information for only this virtual machine.
      - Valid values range from 100 to 999999999.
    type: int
    required: false
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
notes:
  - For Proxmox VE, the authenticated identity must have all privileges of the C(Administrator) role,
    with propagation enabled, on C(/vms), even when retrieving information for a single VM.
  - The module enforces this requirement before retrieving information; read-only VM privileges alone
    do not satisfy the current permission check.
"""

EXAMPLES = r"""
- name: Retrieve all VMs on a node
  ansible.proxmox.proxmox_vm_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: secret
    node: pve01

- name: Retrieve one VM configuration
  ansible.proxmox.proxmox_vm_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: mytoken
    api_token_secret: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    node: pve01
    vmid: 100
"""

RETURN = r"""
vms:
  description:
    - VM summaries or the selected VM configuration returned by Proxmox.
    - Selected VM configurations include C(machine=i440fx) when the API omits the default machine.
  returned: always
  type: list
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxVmInfoModule(ProxmoxInfoModule):
    result_key = "vms"

    def get_info(self):
        vmid = self.module.params.get("vmid")
        if vmid is not None and not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        node = self.module.params["node"]
        self.client.validate_pve_permissions(self.api, "/vms")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")

        if vmid is None:
            return self.call_api(
                "Retrieving VMs from node {0}".format(node),
                self.api.get,
                "nodes/{0}/qemu".format(node),
            )

        if not self.validate_proxmox_resource_exist(
            "VM",
            vmid,
            lambda: self.api.get("nodes/{0}/qemu".format(node)),
            resource_key="vmid",
            fail_on_missing=False,
        ):
            return []

        config = self.call_api(
            "Retrieving VM {0} configuration from node {1}".format(vmid, node),
            self.api.get,
            "nodes/{0}/qemu/{1}/config".format(node, vmid),
        )
        config = dict(config)
        config.setdefault("machine", "i440fx")
        return [config]


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmInfoModule(module).execute()


if __name__ == "__main__":
    main()
