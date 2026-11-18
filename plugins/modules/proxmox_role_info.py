#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_role_info
short_description: Retrieve Proxmox VE role information
description:
- Lists built-in and custom role definitions or retrieves one role.
- Returns sorted privilege lists; returns an empty list for an absent selected role.
version_added: 1.0.0
options:
  roleid:
    description: Optional role identifier. Omit to list all roles.
    type: str
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Reads state and validates inputs without changing configuration.
notes:
- Requires all privileges of the C(Administrator) role propagated on C(/access), including for reads and check mode.
- Includes built-in roles and their special flag. Does not modify definitions or ACL assignments.
"""

EXAMPLES = r"""
- name: Read a role
  ansible.proxmox.proxmox_role_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    roleid: ApplicationReader
"""

RETURN = r"""
roles:
  description: Role records. Empty when the selected resource does not exist.
  returned: always
  type: list
  contains:
    roleid:
      description: Role identifier.
      type: str
      returned: when the resource exists
    privileges:
      description: Sorted privilege set.
      type: list
      returned: when the resource exists
      elements: str
    special:
      description: Whether the role is a built-in role.
      type: bool
      returned: when the resource exists
  elements: dict
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxRoleInfoModule(ProxmoxInfoModule):
    """Read role definitions independently of the lifecycle module."""

    result_key = "roles"

    def get_info(self):
        roleid = self.module.params.get("roleid")
        if roleid is not None and re.fullmatch(r"[A-Za-z0-9._-]+", roleid) is None:
            self.module.fail_json(msg="roleid must contain only letters, digits, dots, underscores and hyphens.")
        self.client.validate_pve_permissions(self.api, "/access")
        roles = self.call_api("Listing roles", self.api.get, "access/roles")
        result = []
        for summary in roles:
            if roleid is not None and summary["roleid"] != roleid:
                continue
            result.append({"roleid": summary["roleid"], "privileges": sorted(filter(None, summary.get("privs", "").split(","))),
                           "special": bool(summary.get("special", 0))})
        return result


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"roleid": {"type": "str"}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxRoleInfoModule(module).execute()


if __name__ == "__main__":
    main()
