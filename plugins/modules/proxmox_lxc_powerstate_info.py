#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_powerstate_info
short_description: Retrieve LXC container power state
description:
  - Retrieves the current power state of a Proxmox VE LXC container without modifying it.
  - Discovers the node containing the container from its cluster-wide identifier.
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
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves the current power state normally in check mode.
notes:
  - The API identity must be able to discover the container and read its current status.
  - This module always reports C(changed=false).
"""

EXAMPLES = r"""
- name: Retrieve LXC container power state
  ansible.proxmox.proxmox_lxc_powerstate_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 1003
  register: lxc_power
"""

RETURN = r"""
powerstate:
  description: Current LXC container power state, either C(stopped) or C(running).
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
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPowerstateInfoModule


class ProxmoxLxcPowerstateInfoModule(ProxmoxPowerstateInfoModule):
    """Retrieve LXC container power state through the shared info lifecycle."""

    guest_type = "lxc"


def main():
    """Parse module arguments and return LXC power state without changes."""
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "vmid": {"type": "int", "required": True}
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcPowerstateInfoModule(module).execute_info_module()


if __name__ == "__main__":
    main()
