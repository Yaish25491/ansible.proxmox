#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_node_info
short_description: Retrieve Proxmox VE node information and configuration
description:
  - Lists node summaries or retrieves selected information for one node.
  - Reads node status, DNS settings, the hosts file and time settings through their respective API endpoints.
  - Provides the read counterpart for node DNS, hosts and time configuration modules.
  - Returns an empty list when the selected node does not exist.
version_added: "1.0.0"
options:
  node:
    description:
      - Proxmox VE node name.
      - Omit to list node summaries without making per-node configuration requests.
    type: str
  gather_subset:
    description:
      - Information to retrieve for O(node). Requires O(node).
      - Omit to retrieve all supported subsets for a selected node.
      - V(status) reads node status and hardware information.
      - V(dns) reads the search domain and up to three name servers.
      - V(hosts) reads the complete hosts file and its configuration digest.
      - V(time) reads timezone, UTC time and local time.
      - V(all) selects all supported subsets. An empty list retrieves only the node summary.
    type: list
    elements: str
    choices: [all, status, dns, hosts, time]
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves information without modifying the node.
notes:
  - Selected-node reads require all privileges of the C(Administrator) role propagated on C(/nodes/{node}).
  - The V(hosts) subset additionally requires those privileges propagated on C(/), because its API read checks root permissions.
  - Listing nodes requires an authenticated identity. Permission and endpoint failures are reported rather than hidden as empty results.
  - Syslog entries are outside these configuration subsets.
"""

EXAMPLES = r"""
- name: List nodes
  ansible.proxmox.proxmox_node_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"

- name: Retrieve status DNS hosts and time for a node
  ansible.proxmox.proxmox_node_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01

- name: Read DNS independently after a configuration change
  ansible.proxmox.proxmox_node_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    gather_subset: [dns]
  register: node_dns
"""

RETURN = r"""
nodes:
  description: Node records with a summary and any requested subsets. Empty when the selected node does not exist.
  returned: always
  type: list
  elements: dict
  contains:
    node:
      description: Node name.
      type: str
      returned: always
    summary:
      description: Unmodified node summary from the cluster node listing, including availability and resource usage when supplied.
      type: dict
      returned: always
    status:
      description: Unmodified node status, including CPU, memory, kernel and uptime information supplied by Proxmox.
      type: dict
      returned: when requested
    dns:
      description: DNS settings. Unconfigured fields are omitted by Proxmox.
      type: dict
      returned: when requested
      contains:
        search:
          description: Search domain.
          type: str
          returned: when configured
        dns1:
          description: First name server.
          type: str
          returned: when configured
        dns2:
          description: Second name server.
          type: str
          returned: when configured
        dns3:
          description: Third name server.
          type: str
          returned: when configured
    hosts:
      description: Hosts file configuration.
      type: dict
      returned: when requested
      contains:
        data:
          description: Complete hosts file contents.
          type: str
          returned: always
        digest:
          description: Configuration digest usable for optimistic concurrency checks when updating the hosts file.
          type: str
          returned: always
    time:
      description: Time settings and observations.
      type: dict
      returned: when requested
      contains:
        timezone:
          description: Configured timezone.
          type: str
          returned: always
        time:
          description: UTC Unix timestamp.
          type: int
          returned: always
        localtime:
          description: Local time represented as a Unix timestamp by Proxmox.
          type: int
          returned: always
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxNodeInfoModule(ProxmoxInfoModule):
    """Read selected node endpoints through the shared information lifecycle."""

    result_key = "nodes"
    subsets = ("status", "dns", "hosts", "time")

    def get_info(self):
        node = self.module.params.get("node")
        requested = self.module.params.get("gather_subset")
        if node is None and requested is not None:
            self.module.fail_json(msg="gather_subset requires node.")
        if node is not None and not node:
            self.module.fail_json(msg="node must not be empty.")
        if node is not None:
            self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(node))
        summaries = self.call_api("Listing Proxmox nodes", self.api.nodes.get)
        if node is None:
            return [{"node": item["node"], "summary": item} for item in summaries]
        if not self.validate_proxmox_resource_exist("node", node, lambda: summaries, resource_key="node", fail_on_missing=False):
            return []
        summary = next(item for item in summaries if item["node"] == node)
        selected = self.subsets if requested is None or "all" in requested else requested
        if "hosts" in selected:
            self.client.validate_pve_permissions(self.api, "/")
        result = {"node": node, "summary": summary}
        for subset in self.subsets:
            if subset in selected:
                result[subset] = self.call_api(
                    "Retrieving {0} from node {1}".format(subset, node),
                    self.api.get, "nodes/{0}/{1}".format(node, subset),
                )
        return [result]


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "node": {"type": "str"},
            "gather_subset": {"type": "list", "elements": "str", "choices": ["all", "status", "dns", "hosts", "time"]},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeInfoModule(module).execute()


if __name__ == "__main__":
    main()
