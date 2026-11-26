#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_security_group_info
short_description: Retrieve Proxmox VE firewall security groups
description:
- Lists datacenter security groups and optionally reads their ordered rules through the native API.
version_added: 1.0.0
options:
  name:
    type: str
    description: Optional case-sensitive group name. Omit to list all groups.
  include_rules:
    type: bool
    default: true
    description: Include each selected group's complete ordered native rule list.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Reads actual information without mutations.
notes:
- Supports Proxmox VE 9 and requires propagated C(Administrator) on C(/), following the collection precheck policy.
- Native group listing allows authenticated users; reading group rules requires C(Sys.Audit) on C(/).
- A successfully discovered missing group returns an empty list. API errors and malformed discovery fail explicitly.
- Definitions are cluster-wide; guest/host rules that apply them are separate resources.
"""

EXAMPLES = r"""
- name: Read a security group and its rules
  ansible.proxmox.proxmox_firewall_security_group_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: web_servers

- name: List security-group metadata without retrieving rules
  ansible.proxmox.proxmox_firewall_security_group_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    include_rules: false
"""

RETURN = r"""
security_groups:
  description: Selected native groups; empty when absent. Additional native fields and ordering are preserved.
  type: list
  elements: dict
  returned: always
  contains:
    group:
      type: str
      description: Case-sensitive group name.
    comment:
      type: str
      description: Optional description.
    digest:
      type: str
      description: Native group-list digest.
    rules:
      type: list
      elements: dict
      description:
      - Present when requested. Native ordered rules include type/action/enable and optional match/log/comment fields.
      - Native position, digest and derived IP version are preserved.
"""

from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxFirewallSecurityGroupInfoModule(ProxmoxFirewallScope, ProxmoxInfoModule):
    result_key = "security_groups"

    def get_info(self):
        name = self.module.params.get("name")
        if name is not None:
            self.firewall_name(name, maximum=18)
        self.client.validate_pve_permissions(self.api, "/")
        records = self.firewall_records("cluster/firewall/groups", "group")
        selected = []
        for row in records:
            if name is not None and row["group"] != name:
                continue
            result = dict(row)
            if self.module.params.get("include_rules", True):
                endpoint = "cluster/firewall/groups/" + quote(row["group"], safe="")
                rules = self.firewall_records(endpoint, "pos", required=("type", "action"))
                if [rule["pos"] for rule in rules] != list(range(len(rules))):
                    self.client.fail_request("Security-group API returned inconsistent rule positions.")
                result["rules"] = rules
            selected.append(result)
        return selected


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"name": {"type": "str"}, "include_rules": {"type": "bool", "default": True}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallSecurityGroupInfoModule(module).execute()


if __name__ == "__main__":
    main()
