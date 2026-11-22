#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_refs_info
short_description: Retrieve Proxmox VE firewall references
description:
- Lists available alias and IP-set references for cluster or QEMU/LXC firewall rules.
- Cluster and guest results can include datacenter and SDN references according to API visibility.
version_added: 1.0.0
options:
  scope:
    type: str
    choices:
    - cluster
    - qemu
    - lxc
    default: cluster
    description: Select cluster references or guest references.
  node:
    type: str
    description: Required for guest scopes.
  vmid:
    type: int
    description: Required for QEMU or LXC scope. Guest ID between 100 and 999999999; omit for other scopes.
  type:
    type: str
    choices:
    - alias
    - ipset
    description: Optional reference type filter. Omit to include aliases and IP sets.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves current information without mutations.
notes:
- Requires the C(Administrator) role propagated on C(/) for cluster scope or C(/vms/{vmid}) for guest scope.
- Native API privileges are C(Sys.Audit) for cluster references and C(VM.Audit) for guest references.
- After required permission checks, a successfully discovered missing node, guest, guest type or guest location returns an empty list.
- Discovery/API failures and malformed discovery/reference responses are errors, including a guest disappearing after successful discovery.
- Reference names can repeat across origins or types. Use C(type), C(scope) and C(name) together, and use the native C(ref) value verbatim.
- Cluster origins are C(dc) and visible C(sdn); guest queries can also contain C(guest). SDN visibility is filtered by the server.
- Each invocation refreshes discovery and reference data. Server ordering is preserved but is not guaranteed stable between requests.
- Reads reference definitions only; does not create aliases, IP sets, rules or enable a firewall. Supports Proxmox VE 9.
"""

EXAMPLES = r"""
- name: Retrieve Proxmox VE firewall references
  ansible.proxmox.proxmox_firewall_refs_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: cluster
    type: alias

- name: Read references available to a virtual machine
  ansible.proxmox.proxmox_firewall_refs_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: qemu
    node: pve01
    vmid: 100
    type: ipset

- name: Read references available to a container without changing it
  ansible.proxmox.proxmox_firewall_refs_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: lxc
    node: pve01
    vmid: 101
  check_mode: true
"""

RETURN = r"""
references:
  description: Available references, preserving server fields and ordering. Empty for a missing node or guest, or no matching references.
  type: list
  elements: dict
  returned: always
  contains:
    type:
      type: str
      description: Reference type, alias or ipset.
      returned: when provided by the server
    name:
      type: str
      description: Reference name.
      returned: when provided by the server
    ref:
      type: str
      description: Native value usable in a firewall rule. Alias references use the name; IP-set references prefix the name with a plus sign.
      returned: when provided by the server
    scope:
      type: str
      description: Native reference origin, such as dc, sdn or guest.
      returned: when provided by the server
    comment:
      type: str
      description: Optional reference comment.
      returned: when provided by the server
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxFirewallRefsInfoModule(ProxmoxInfoModule):
    """Read scoped firewall information with checked node and guest discovery."""

    result_key = "references"

    def _validate_parameters(self):
        scope = self.module.params["scope"]
        node = self.module.params.get("node")
        vmid = self.module.params.get("vmid")
        if scope not in ("cluster", "qemu", "lxc"):
            self.module.fail_json(msg="Unsupported firewall scope.")
        if scope == "cluster":
            if node is not None or vmid is not None:
                self.module.fail_json(msg="cluster scope does not accept node or vmid.")
        else:
            if node is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", node) is None:
                self.module.fail_json(msg="node must be a nonempty node name without path separators.")
            if scope in ("qemu", "lxc") and (vmid is None or not 100 <= vmid <= 999999999):
                self.module.fail_json(msg="guest scope requires vmid between 100 and 999999999.")
        if self.module.params.get("type") not in (None, "alias", "ipset"):
            self.module.fail_json(msg="type must be alias or ipset.")

    def _endpoint(self):
        scope = self.module.params["scope"]
        if scope == "cluster":
            self.client.validate_pve_permissions(self.api, "/")
            return "cluster/firewall/refs"
        node = self.module.params["node"]
        vmid = self.module.params.get("vmid")
        permission_path = "/vms/{0}".format(vmid)
        self.client.validate_pve_permissions(self.api, permission_path)
        nodes = self.call_api("Listing Proxmox nodes", self.api.get, "nodes")
        if not isinstance(nodes, list) or any(not isinstance(entry, dict) or "node" not in entry for entry in nodes):
            return self.client.fail_request("Node discovery returned an invalid node list; refusing to report absence.")
        if not self.validate_proxmox_resource_exist("node", node, lambda: nodes, resource_key="node", fail_on_missing=False):
            return None
        prefix = "nodes/{0}".format(quote(node, safe=""))
        guests = self.call_api("Listing Proxmox guests", self.api.get, "cluster/resources", type="vm")
        if not isinstance(guests, list) or any(not isinstance(entry, dict) or not {"vmid", "type", "node"}.issubset(entry) for entry in guests):
            return self.client.fail_request("Guest discovery returned an invalid guest list; refusing to report absence.")
        if not any(str(guest.get("vmid")) == str(vmid) and guest.get("type") == scope and guest.get("node") == node for guest in guests):
            return None
        return "{0}/{1}/{2}/firewall/refs".format(prefix, scope, vmid)

    def get_info(self):
        self._validate_parameters()
        endpoint = self._endpoint()
        if endpoint is None:
            return []
        parameters = {name: self.module.params[name] for name in ("type",) if self.module.params.get(name) is not None}
        references = self.call_api("Reading firewall refs", self.api.get, endpoint, **parameters)
        if not isinstance(references, list) or any(not isinstance(entry, dict) for entry in references):
            return self.client.fail_request("Firewall API returned an invalid reference list.")
        return references


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "scope": {"type": "str", "choices": ["cluster", "qemu", "lxc"], "default": "cluster"},
                "node": {"type": "str"},
                "vmid": {"type": "int"},
                "type": {"type": "str", "choices": ["alias", "ipset"]},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallRefsInfoModule(module).execute()


if __name__ == "__main__":
    main()
