#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_powerstate
short_description: Manage LXC container power state
description:
  - Manages an LXC container's power state through the Proxmox VE API.
  - Discovers the node containing the container from its cluster-wide identifier.
  - Waits for asynchronous power tasks and verifies the resulting state.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  vmid:
    description:
      - Identifier of the LXC container.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  powerstate:
    description:
      - Power action to perform.
      - V(start), V(stop), and V(shutdown) are idempotent when the target state is already reached.
      - V(stop) forcibly stops the container; V(shutdown) requests a graceful guest shutdown.
      - V(reboot) shuts down and starts the container to apply pending changes; it always executes and reports a change.
      - Actions that are not supported in the container's current state fail with the Proxmox API error.
    type: str
    required: true
    choices: [start, stop, shutdown, reboot]
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads the current power state and predicts changes without performing power actions.
notes:
  - The API identity must be able to discover the container, read its status, and use VM.PowerMgmt on the container.
  - Graceful shutdown and reboot require a guest that responds to the corresponding Proxmox request.
"""

EXAMPLES = r"""
- name: Start an LXC container
  ansible.proxmox.proxmox_lxc_powerstate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 1003
    powerstate: start

- name: Gracefully shut down an LXC container
  ansible.proxmox.proxmox_lxc_powerstate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_token_secret }}"
    vmid: 1003
    powerstate: shutdown
    task_timeout: 120
"""

RETURN = r"""
powerstate:
  description: Observed LXC container power state. In check mode, this is the unchanged current state.
  returned: success
  type: str
  sample: running
task_upid:
  description: Identifier of the completed asynchronous power task, when the API returns one.
  returned: changed and an asynchronous task was executed
  type: str
  sample: 'UPID:pve01:00000001:00000001:00000001:vzstart:100:root@pam:'
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


class ProxmoxLxcPowerstateModule(ProxmoxPowerstateActionModule):
    """Run LXC container power operations through the shared lifecycle."""

    guest_type = "lxc"


def main():
    """Parse module arguments, manage LXC power, and return the Ansible result."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec(
            {
                "vmid": {"type": "int", "required": True},
                "powerstate": {"type": "str", "required": True, "choices": list(ProxmoxPower.LXC_POWER_ACTIONS)},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcPowerstateModule(module).execute_action_module()


if __name__ == "__main__":
    main()
