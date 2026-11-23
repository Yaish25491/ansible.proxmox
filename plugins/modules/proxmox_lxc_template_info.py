#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_template_info
short_description: Retrieve LXC container templates
description:
  - Retrieves template status information for LXC containers on a Proxmox VE node.
  - When I(vmid) is omitted, all LXC templates on the node are returned.
  - When I(vmid) is provided, returns that container only if the node inventory marks it as a template.
  - Returns an empty list when the requested container is missing or is not a template.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description:
      - Proxmox VE node containing the LXC template(s).
    type: str
    required: true
  vmid:
    description:
      - Return information for only this container, if it is a template.
      - Valid values range from 100 to 999999999.
    type: int
    required: false
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
seealso:
  - module: ansible.proxmox.proxmox_lxc_template
    description: Convert an LXC container into a template.
  - module: ansible.proxmox.proxmox_lxc
    description: Update or delete LXC templates.
notes:
  - The module requires all privileges of the C(Administrator) role, propagated on C(/vms).
  - Use M(ansible.proxmox.proxmox_lxc) to update or delete templates, just as for regular containers.
"""

EXAMPLES = r"""
- name: List LXC templates on a node
  ansible.proxmox.proxmox_lxc_template_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01

- name: Check whether one container is a template
  ansible.proxmox.proxmox_lxc_template_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: mytoken
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    vmid: 100
"""

RETURN = r"""
templates:
  description:
    - Node inventory records for LXC templates on the requested node.
    - When C(vmid) is supplied, contains that container only if C(template=1) is reported.
  returned: always
  type: list
  elements: dict
  sample: [{"vmid": 101, "status": "stopped", "template": 1}]
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower


class ProxmoxLxcTemplateInfoModule(ProxmoxPower, ProxmoxInfoModule):
    """Retrieve template statuses for LXC containers on a node."""

    result_key = "templates"
    guest_type = "lxc"

    def __init__(self, module, client=None):
        super().__init__(module, client=client, guest_type=self.guest_type)

    @staticmethod
    def _is_template_status(status):
        """Return whether current LXC status marks the container as a template."""
        return isinstance(status, dict) and status.get("template") in (1, "1", True)

    def get_info(self):
        """Return node inventory records for matching LXC templates."""
        node = self.module.params["node"]
        vmid = self.module.params.get("vmid")
        if vmid is not None and not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        self.client.validate_pve_permissions(self.api, "/vms")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        containers = self.call_api(
            "Retrieving LXC containers from node {0}".format(node),
            self.api.nodes(node).lxc.get,
        )
        container_records = [
            container
            for container in containers or []
            if isinstance(container, dict)
            and container.get("vmid") is not None
            and self._is_template_status(container)
        ]
        if vmid is not None:
            container_records = [
                container for container in container_records if str(container["vmid"]) == str(vmid)
            ]

        return [dict(container) for container in container_records]


def main():
    """Parse arguments and return matching LXC templates."""
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
    ProxmoxLxcTemplateInfoModule(module).execute_info_module()


if __name__ == "__main__":
    main()
