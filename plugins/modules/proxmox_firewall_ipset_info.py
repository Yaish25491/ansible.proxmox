#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_ipset_info
short_description: Retrieve Proxmox VE firewall IP sets
description:
- Reads local datacenter or guest IP-set definitions and, optionally, their members and available rule references.
version_added: 1.0.0
options:
  name:
    type: str
    description: Optional case-sensitive IP-set name. Omit to list all definitions local to the selected scope.
  include_entries:
    type: bool
    default: true
    description: Read members of each selected IP set, including their comments, exclusion flags and native member-list digests.
  include_references:
    type: bool
    default: false
    description:
    - Also read available references, filtered server-side to IP sets. A selected O(name) also filters reference names.
    - Preserves native ordering, duplicate names, origin fields and C(+) reference prefixes. Origins can include C(dc), C(guest) and server-visible C(sdn).
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_firewall
attributes:
  check_mode:
    support: full
    description: Reads actual state without mutations.
notes:
- A missing node, guest, type, location or named set returns empty lists after successful checked discovery; API errors remain failures.
- Native read privileges are C(Sys.Audit) for datacenter scope or C(VM.Audit) for guests, in addition to the collection role precheck.
- Local definitions are distinct from inherited/generated available references. An API error while reading a selected set's members is not an empty set.
"""

EXAMPLES = r"""
- name: Read one datacenter set and its members
  ansible.proxmox.proxmox_firewall_ipset_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: management_hosts
    include_references: true

- name: List set metadata local to a VM
  ansible.proxmox.proxmox_firewall_ipset_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: qemu
    node: pve01
    vmid: 100
    include_entries: false
"""

RETURN = r"""
ipsets:
  description: Local IP sets with additional native fields preserved; empty for a missing selection.
  type: list
  elements: dict
  returned: always
  contains:
    name:
      type: str
      description: Case-sensitive set name.
    comment:
      type: str
      description: Optional description.
    digest:
      type: str
      description: IP-set-list digest.
    entries:
      type: list
      elements: dict
      description: Present when requested; native member dictionaries contain cidr, optional comment/nomatch and member-list digest.
references:
  description: Available IP-set references when requested, otherwise an empty list. Preserves extension fields and server order.
  type: list
  elements: dict
  returned: always
  contains:
    name:
      type: str
      description: Reference name.
    type:
      type: str
      description: Always ipset.
    ref:
      type: str
      description: Native reference spelling, including its plus prefix.
    scope:
      type: str
      description: Origin such as dc, guest or sdn.
    comment:
      type: str
      description: Optional native comment.
"""

from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope, firewall_scope_spec
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxFirewallIpsetInfoModule(ProxmoxFirewallScope, ProxmoxInfoModule):
    result_key = "ipsets"

    def get_info(self):
        selected = self.module.params.get("name")
        if selected is not None:
            self.firewall_name(selected)
        root = self.firewall_endpoint(missing_ok=True)
        self._references = []
        if root is None:
            return []
        records = self.firewall_records(root + "/ipset", "name")
        result = []
        for record in records:
            if selected is not None and record["name"] != selected:
                continue
            data = dict(record)
            if self.module.params.get("include_entries", True):
                endpoint = root + "/ipset/" + quote(record["name"], safe="")
                data["entries"] = self.firewall_records(endpoint, "cidr")
            result.append(data)
        if self.module.params.get("include_references"):
            references = self.firewall_records(root + "/refs", "name", required=("type", "ref", "scope"), type="ipset")
            if any(row["type"] != "ipset" for row in references):
                self.client.fail_request("Firewall API returned invalid IP-set references.")
            self._references = [row for row in references if selected is None or row["name"] == selected]
        return result

    def prepare_result(self, changed, resource=None, **values):
        return super().prepare_result(changed, resource, references=self._references, **values)


def main():
    options = firewall_scope_spec()
    options.update({"name": {"type": "str"}, "include_entries": {"type": "bool", "default": True}, "include_references": {"type": "bool", "default": False}})
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(options),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallIpsetInfoModule(module).execute()


if __name__ == "__main__":
    main()
