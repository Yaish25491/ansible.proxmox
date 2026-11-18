#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_group_info
short_description: Retrieve Proxmox VE group information
description:
- Lists groups or reads one group's comment and member identities.
- Returns an empty list when a selected group does not exist.
version_added: 1.0.0
options:
  groupid:
    description:
    - Optional group identifier using letters, digits, dots, underscores or hyphens. Omit to list groups.
    - The reserved URL path segments C(.) and C(..) are not supported.
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
- Uses the native group read API. Does not create users or alter their group assignments.
- Group summaries and details are normalized to groupid, comment and sorted members.
"""

EXAMPLES = r"""
- name: Read a group
  ansible.proxmox.proxmox_group_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    groupid: application
"""

RETURN = r"""
groups:
  description: Group records. Empty when the selected resource does not exist.
  returned: always
  type: list
  contains:
    groupid:
      description: Group identifier.
      type: str
      returned: when the resource exists
    comment:
      description: Group comment, or an empty string.
      type: str
      returned: when the resource exists
    members:
      description: Sorted realm-qualified user identifiers.
      type: list
      returned: when the resource exists
      elements: str
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


class ProxmoxGroupInfoModule(ProxmoxInfoModule):
    """Read groups and membership independently from mutations."""

    result_key = "groups"

    def get_info(self):
        groupid = self.module.params.get("groupid")
        if groupid in (".", ".."):
            self.module.fail_json(msg="groupid cannot be the reserved URL path segments . or ..")
        if groupid is not None and re.fullmatch(r"[A-Za-z0-9._-]+", groupid) is None:
            self.module.fail_json(msg="groupid must contain only letters, digits, dots, underscores and hyphens.")
        self.client.validate_pve_permissions(self.api, "/access")
        groups = self.call_api("Listing groups", self.api.get, "access/groups")
        if groupid is not None:
            if not self.validate_proxmox_resource_exist("group", groupid, lambda: groups, resource_key="groupid", fail_on_missing=False):
                return []
            data = self.call_api("Reading group {0}".format(groupid), self.api.get, "access/groups/{0}".format(groupid))
            return [{"groupid": groupid, "comment": data.get("comment", ""), "members": sorted(set(data.get("members") or []))}]
        result = []
        for entry in groups:
            members = entry.get("users") or []
            result.append({"groupid": entry["groupid"], "comment": entry.get("comment", ""),
                           "members": sorted(set(members.split(",") if isinstance(members, str) else members))})
        return result


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"groupid": {"type": "str"}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxGroupInfoModule(module).execute()


if __name__ == "__main__":
    main()
