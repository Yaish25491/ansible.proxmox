#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_powerstate
short_description: Manage QEMU/KVM virtual machine power state
description:
  - Manages a virtual machine's power state through the Proxmox VE API.
  - Discovers the node containing the VM from its cluster-wide identifier.
  - Waits for asynchronous power tasks and verifies the resulting state.
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
  powerstate:
    description:
      - Power action to perform.
      - V(start) and V(resume) target a running VM.
      - V(stop) forcibly stops the VM; V(shutdown) requests a graceful guest shutdown.
      - V(pause) suspends execution in memory; V(hibernate) suspends to disk.
      - All actions except V(reset) and V(reboot) are idempotent when the target state is already reached.
      - V(reset) and V(reboot) always execute and report a change.
      - Actions that are not supported in the VM's current state fail with the Proxmox API error.
    type: str
    required: true
    choices: [start, stop, shutdown, reset, reboot, pause, hibernate, resume]
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads the current power state and predicts changes without performing power actions.
notes:
  - The API identity must be able to discover the VM and read its status and must have VM.PowerMgmt on the VM.
  - Graceful shutdown and reboot require a guest that responds to the corresponding Proxmox request.
  - Hibernation requires storage suitable for saving the VM's memory state.
"""

EXAMPLES = r"""
- name: Start a VM
  ansible.proxmox.proxmox_vm_powerstate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 100
    powerstate: start

- name: Hibernate a VM
  ansible.proxmox.proxmox_vm_powerstate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 100
    powerstate: hibernate
    task_timeout: 900
"""

RETURN = r"""
powerstate:
  description: Observed VM power state. In check mode, this is the unchanged current state.
  returned: success
  type: str
  sample: running
task_upid:
  description: Identifier of the completed asynchronous power task, when the API returns one.
  returned: changed and an asynchronous task was executed
  type: str
  sample: 'UPID:pve01:00000001:00000001:00000001:qmstart:100:root@pam:'
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import (
    ProxmoxPower,
    ProxmoxPowerstateActionModule,
)


class ProxmoxVmPowerstateModule(ProxmoxPowerstateActionModule):
    """Run QEMU VM power operations through the shared lifecycle."""

    guest_type = "qemu"


def main():
    """Parse module arguments, manage VM power, and return the Ansible result."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "vmid": {"type": "int", "required": True},
            "powerstate": {"type": "str", "required": True, "choices": list(ProxmoxPower.POWER_STATES)},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmPowerstateModule(module).execute_action_module()


if __name__ == "__main__":
    main()
