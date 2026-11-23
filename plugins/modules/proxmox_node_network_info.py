#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_node_network_info
short_description: Retrieve effective Proxmox VE node network configuration
version_added: 1.0.0
description:
  - Lists effective node interface configuration, including pending changes, without modifying the node.
  - Provides independent observations for M(ansible.proxmox.proxmox_node_network).
options:
  node:
    description: Node to query. A missing discovered node returns an empty list.
    type: str
    required: true
  iface:
    description: Select one exact interface name. A missing interface returns an empty list. Omit to list interfaces.
    type: str
  type:
    description: Optional native interface-type filter. Can be combined with O(iface).
    type: str
    choices: [bridge, bond, eth, alias, vlan, OVSBridge, OVSBond, OVSPort, OVSIntPort, vnet, fabric, any_bridge, any_local_bridge, include_sdn]
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves current observations without writes.
notes:
  - Supports Proxmox VE 9. Native type-filter availability depends on the installed PVE 9 package version.
  - Requires the Administrator role propagated on C(/nodes/{node}), preserving collection permission policy.
  - The native list endpoint requires authentication and additionally filters bridge visibility by SDN access permissions.
  - Permission and API failures are errors, not empty successful results. Validate access before interpreting an absent interface.
  - Returns native records and fields without normalization; discovery and network data are refreshed on each invocation.
  - Effective configuration can come from C(/etc/network/interfaces.new). The active/exists observations do not prove pending changes were applied.
  - The native response-envelope changes attribute is not returned by the shared client's data-only serializer. No pending or applied flag is invented.
  - No pagination is exposed because the native list endpoint has none. Read-only OVS/SDN observations do not imply write support in the lifecycle module.
"""

EXAMPLES = r"""
- name: Independently inspect staged bridge configuration
  ansible.proxmox.proxmox_node_network_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    iface: vmbr100
  register: network_info

- name: Read Linux bonds without mutations in check mode
  ansible.proxmox.proxmox_node_network_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    type: bond
  check_mode: true
"""

RETURN = r"""
interfaces:
  description: Native effective configuration records; empty when the checked selection is missing or filtered out.
  type: list
  elements: dict
  returned: always
  contains:
    iface:
      description: Interface name.
      type: str
      returned: always
    type:
      description: Native interface type.
      type: str
      returned: always
    active:
      description: Native active-link observation, independent of pending configuration.
      type: bool
      returned: when provided by the server
    exists:
      description: Native physical-device existence observation, not configuration-stanza existence.
      type: bool
      returned: when provided by the server
    priority:
      description: Native configuration stanza ordering when a stanza is present.
      type: int
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


class ProxmoxNodeNetworkInfoModule(ProxmoxInfoModule):
    """Read the native network list and optionally select an exact interface."""

    result_key = "interfaces"

    def get_info(self):
        params = self.module.params
        node = params["node"]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", node) is None:
            self.module.fail_json(msg="node must be a nonempty node name without path separators.")
        iface = params.get("iface")
        if iface is not None and (not 2 <= len(iface) <= 20 or re.fullmatch(r"[a-zA-Z]\w{1,20}([:.]\d+)?", iface, flags=re.ASCII) is None):
            self.module.fail_json(msg="iface must be a valid interface name.")
        self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(node))
        nodes = self.call_api("Listing Proxmox nodes", self.api.get, "nodes")
        if not isinstance(nodes, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("node"), str) or not entry["node"] for entry in nodes):
            return self.client.fail_request("Node discovery returned an invalid node list.")
        if not self.validate_proxmox_resource_exist("node", node, lambda: nodes, resource_key="node", fail_on_missing=False):
            return []
        parameters = {"type": params["type"]} if params.get("type") is not None else {}
        records = self.call_api("Reading effective network configuration", self.api.get, "nodes/{0}/network".format(quote(node, safe="")), **parameters)
        if not isinstance(records, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("iface"), str) or not entry["iface"]
                                                or not isinstance(entry.get("type"), str) or not entry["type"] for entry in records):
            return self.client.fail_request("Network API returned an invalid interface list.")
        if len({entry["iface"] for entry in records}) != len(records):
            return self.client.fail_request("Network API returned duplicate interface names.")
        return records if iface is None else [entry for entry in records if entry["iface"] == iface]


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "node": {"type": "str", "required": True}, "iface": {"type": "str"},
            "type": {"type": "str", "choices": [
                "bridge", "bond", "eth", "alias", "vlan", "OVSBridge", "OVSBond", "OVSPort", "OVSIntPort", "vnet",
                "fabric", "any_bridge", "any_local_bridge", "include_sdn",
            ]},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeNetworkInfoModule(module).execute()


if __name__ == "__main__":
    main()
