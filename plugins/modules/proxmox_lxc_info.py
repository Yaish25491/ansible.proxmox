#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_info
short_description: Retrieve LXC container information
description:
  - Retrieves LXC container information from a Proxmox VE node.
  - When I(vmid) is omitted, information for every container on the node is returned.
  - When I(vmid) is provided, the current configuration for that container is returned.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: The Proxmox VE node containing the container(s).
    type: str
    required: true
  vmid:
    description: Return information for only this container; valid values range from 100 to 999999999.
    type: int
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
notes:
  - The module requires propagated C(Administrator) privileges on C(/vms), matching the collection's VM info module.
"""

EXAMPLES = r"""
- name: Retrieve all containers on a node
  ansible.proxmox.proxmox_lxc_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01

- name: Retrieve one container configuration
  ansible.proxmox.proxmox_lxc_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_token_secret }}"
    node: pve01
    vmid: 1003
"""

RETURN = r"""
lxcs:
  description:
    - Container summaries, or a one-item list containing the selected current configuration.
    - An empty list is returned when the requested VMID does not exist on the node.
  returned: always
  type: list
  elements: dict
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxLxcInfoModule(ProxmoxInfoModule):
    """Retrieve container summaries or a selected current configuration."""

    result_key = "lxcs"

    def get_info(self):
        vmid = self.module.params.get("vmid")
        if vmid is not None and not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        node = self.module.params["node"]
        self.client.validate_pve_permissions(self.api, "/vms")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        containers = self.call_api(
            "Retrieving LXC containers from node {0}".format(node),
            self.api.nodes(node).lxc.get,
        )
        if vmid is None:
            return containers
        if not self.validate_proxmox_resource_exist(
            "LXC container",
            vmid,
            lambda: containers,
            resource_key="vmid",
            fail_on_missing=False,
        ):
            return []
        config = self.call_api(
            "Retrieving LXC container {0} configuration from node {1}".format(vmid, node),
            self.api.nodes(node).lxc(vmid).config.get,
            current=1,
        )
        config = dict(config)
        config["vmid"] = str(vmid)
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
    ProxmoxLxcInfoModule(module).execute()


if __name__ == "__main__":
    main()
