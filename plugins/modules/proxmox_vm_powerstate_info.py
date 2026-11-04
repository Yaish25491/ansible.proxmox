#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_powerstate_info
short_description: Retrieve QEMU/KVM virtual machine power state
description:
  - Retrieves the current power state of a Proxmox VE virtual machine without modifying it.
  - Discovers the node containing the VM from its cluster-wide identifier.
  - Distinguishes a paused VM from a running VM and a hibernated VM from a stopped VM.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  vmid:
    description:
      - Identifier of the QEMU/KVM virtual machine.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves the current power state normally in check mode.
notes:
  - The API identity must be able to discover the VM and read its current status.
  - This module always reports C(changed=false).
"""

EXAMPLES = r"""
- name: Retrieve VM power state
  ansible.proxmox.proxmox_vm_powerstate_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 100
  register: vm_power
"""

RETURN = r"""
powerstate:
  description:
    - Current VM power state, one of C(stopped), C(running), C(paused), or C(suspended).
    - C(suspended) means the VM is hibernated to disk.
  returned: success
  type: str
  sample: stopped
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower


class ProxmoxVmPowerstateInfoModule(ProxmoxPower, ProxmoxInfoModule):
    """Retrieve VM power state through the shared information lifecycle."""

    result_key = "powerstate"

    def get_info(self):
        """Retrieve the VM's normalized current power state.

        Returns:
            One of stopped, running, paused, or suspended.
        """
        return self.get_powerstate()


def main():
    """Parse module arguments and return the VM power state without changes."""
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"vmid": {"type": "int", "required": True}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmPowerstateInfoModule(module).execute_info_module()


if __name__ == "__main__":
    main()
