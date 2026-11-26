#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_alias_info
short_description: Retrieve Proxmox VE firewall aliases
description:
- Reads configured aliases at datacenter, QEMU or LXC scope, independently of the lifecycle module.
- Optionally reads available alias references, including inherited datacenter and visible SDN definitions.
version_added: 1.0.0
options:
  name:
    type: str
    description: Optional alias name, matched case insensitively. Omit to read all local alias definitions.
  include_references:
    type: bool
    default: false
    description:
    - Also return available alias references in C(references), filtered server-side to aliases.
    - Reference origins can include C(dc), C(guest) and C(sdn). SDN visibility is filtered by the server.
    - Duplicate names across origins and native reference spelling/order are preserved; name selection also filters references.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_firewall
attributes:
  check_mode:
    support: full
    description: Retrieves current information without writes.
notes:
- Missing selections return empty lists only after successful permission and node/guest discovery.
- API errors, including a guest disappearing after discovery, fail.
- Native read privileges are C(Sys.Audit) at datacenter scope or C(VM.Audit) for guests; the collection retains its stronger role precheck.
- Configured aliases are local to the selected scope. Available references can include inherited or generated definitions that this resource cannot modify.
"""

EXAMPLES = r"""
- name: Read a datacenter alias and its available reference
  ansible.proxmox.proxmox_firewall_alias_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: management_net
    include_references: true

- name: Read aliases local to a container
  ansible.proxmox.proxmox_firewall_alias_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: lxc
    node: pve01
    vmid: 101
"""

RETURN = r"""
aliases:
  description: Local alias definitions, preserving additional native fields and ordering. Empty when absent.
  type: list
  elements: dict
  returned: always
  contains:
    name:
      type: str
      description: Stored alias name.
    cidr:
      type: str
      description: IPv4/IPv6 address or network.
    comment:
      type: str
      description: Optional native comment.
    digest:
      type: str
      description: Alias-list digest, suitable for the lifecycle module.
references:
  description: Available alias references when requested, otherwise an empty list. Native extension fields and ordering are preserved.
  type: list
  elements: dict
  returned: always
  contains:
    name:
      type: str
      description: Reference name.
    type:
      type: str
      description: Reference type, always alias.
    ref:
      type: str
      description: Native spelling to use in firewall rules.
    scope:
      type: str
      description: Origin such as dc, guest or sdn.
    comment:
      type: str
      description: Optional description.
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope, firewall_scope_spec
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxFirewallAliasInfoModule(ProxmoxFirewallScope, ProxmoxInfoModule):
    result_key = "aliases"

    def get_info(self):
        name = self.module.params.get("name")
        if name is not None:
            self.firewall_name(name)
        endpoint = self.firewall_endpoint(missing_ok=True)
        self._references = []
        if endpoint is None:
            return []
        aliases = self.firewall_records(endpoint + "/aliases", "name", required=("cidr",))
        if self.module.params.get("include_references"):
            self._references = self.firewall_records(endpoint + "/refs", "name", required=("type", "ref", "scope"), type="alias")
            if any(row["type"] != "alias" for row in self._references):
                self.client.fail_request("Firewall API returned invalid alias references.")
        if name is None:
            return aliases
        self._references = [row for row in self._references if row["name"].lower() == name.lower()]
        return [row for row in aliases if row["name"].lower() == name.lower()]

    def prepare_result(self, changed, resource=None, **values):
        return super().prepare_result(changed, resource, references=self._references, **values)


def main():
    options = firewall_scope_spec()
    options.update({"name": {"type": "str"}, "include_references": {"type": "bool", "default": False}})
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(options),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallAliasInfoModule(module).execute()


if __name__ == "__main__":
    main()
