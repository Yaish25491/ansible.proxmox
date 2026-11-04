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
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower


class ProxmoxVmPowerstateModule(ProxmoxPower, ProxmoxActionModule):
    """Run VM power operations through the shared action lifecycle."""

    result_key = "powerstate"

    def is_action_needed(self):
        """Read the current state and decide whether the action is necessary.

        Returns:
            Whether the requested power action must execute.
        """
        self._current_powerstate = self.get_powerstate()
        return self.is_powerstate_action_needed(self.module.params["powerstate"], self._current_powerstate)

    def perform_action(self):
        """Perform the power action and retain its completed task identifier.

        Returns:
            The verified final VM power state.
        """
        self._task_upid, powerstate = self.perform_powerstate_action(
            self.module.params["powerstate"], self._current_powerstate,
        )
        return powerstate

    def get_action_result(self):
        """Return the observed state without modifying the VM.

        Returns:
            The current power state for an idempotent no-op or check-mode run.
        """
        return self._current_powerstate

    def prepare_result(self, changed, resource=None, **values):
        """Include a completed power-task identifier in the lifecycle result.

        Args:
            changed: Whether the action changed or would change the VM.
            resource: Observed VM power state.
            **values: Additional top-level result fields.

        Returns:
            A result mapping with changed, powerstate, and an optional task_upid.
        """
        result = super().prepare_result(changed, resource, **values)
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result


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
