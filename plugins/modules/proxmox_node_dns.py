#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_node_dns
short_description: Manage Proxmox VE node DNS settings
description:
  - Configures the search domain and up to three ordered IPv4 or IPv6 name servers on an existing node.
  - Preserves omitted settings and avoids writing when the requested configuration already matches.
  - Reads the configuration after a successful update.
version_added: "1.0.0"
options:
  node:
    description: Proxmox VE node name.
    type: str
    required: true
  search:
    description:
      - Search domain for host-name lookup. Omit to preserve the current value.
      - An empty string removes the search domain.
      - Supply a single domain without whitespace; Proxmox reads only the first domain from a search line.
      - The literal value V(0) is rejected because Proxmox silently drops it when writing.
    type: str
  dns_servers:
    description:
      - Complete ordered list of up to three distinct name server IP addresses, corresponding to API fields C(dns1), C(dns2) and C(dns3).
      - Omit to preserve the current servers. An empty list removes all name servers.
      - Addresses are compared by their IP value, so equivalent IPv6 spellings do not trigger an update.
      - V(0.0.0.0) is rejected because Proxmox treats it as an omitted server. Use an empty list to clear servers.
    type: list
    elements: str
  state:
    description:
      - Reconcile the DNS configuration of an existing node.
      - The API has no DNS deletion operation. Use empty values to clear individual settings.
    type: str
    choices: [present]
    default: present
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Returns the predicted configuration without writing DNS settings.
notes:
  - Requires all privileges of the C(Administrator) role propagated on C(/nodes/{node}), including in check mode.
  - DNS settings apply to the entire Proxmox VE node.
  - DNS updates are synchronous; O(task_timeout) does not affect this operation. O(api_timeout) controls each HTTPS request.
  - The API rewrites resolver settings without a concurrency digest. Avoid concurrent DNS changes to the same node.
  - Other resolver-file directives are preserved by the API. Resolver comments and formatting are not returned by the DNS endpoint.
  - Running the integration tests requires a disposable node with recovery access independent of the Proxmox API.
  - >-
    After generating C(tests/integration/integration_config.yml), add C(dns_test_allow_reconfiguration: true) to that file
    to enable DNS mutation scenarios. Then run C(ansible-test integration proxmox_node_dns) from the collection root.
    This integration-test variable defaults to false.
  - >-
    The integration tests temporarily replace and clear DNS settings, then restore and independently verify the captured original API settings.
    Restoration requires continued API access.
"""

EXAMPLES = r"""
- name: Configure a node search domain and both address families
  ansible.proxmox.proxmox_node_dns:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    search: example.com
    dns_servers: [192.0.2.53, '2001:db8::53']

- name: Change only the search domain while retaining name servers
  ansible.proxmox.proxmox_node_dns:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    search: corp.example.com

- name: Clear the search domain while preserving name servers
  ansible.proxmox.proxmox_node_dns:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    search: ''
"""

RETURN = r"""
dns:
  description: DNS configuration returned by Proxmox, or predicted in check mode. Unconfigured fields are omitted.
  returned: always
  type: dict
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
"""

import ipaddress

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxNodeDnsModule(ProxmoxResourceModule):
    """Reconcile singleton DNS configuration using the shared resource lifecycle."""

    result_key = "dns"

    def _validate_parameters(self):
        if not self.module.params["node"]:
            self.module.fail_json(msg="node must not be empty.")
        search = self.module.params.get("search")
        if search == "0" or (search is not None and any(character.isspace() for character in search)):
            self.module.fail_json(msg="search must be a single domain without whitespace, or an empty string; '0' cannot be persisted by Proxmox.")
        servers = self.module.params.get("dns_servers")
        if servers is None:
            return
        if len(servers) > 3:
            self.module.fail_json(msg="dns_servers accepts at most three IP addresses.")
        normalized = []
        for server in servers:
            try:
                address = ipaddress.ip_address(server)
            except ValueError:
                self.module.fail_json(msg="dns_servers must contain IPv4 or IPv6 addresses: {0}".format(server))
            if str(address) == "0.0.0.0" or "%" in str(address):
                self.module.fail_json(msg="dns_servers cannot contain 0.0.0.0 or scoped IPv6 addresses.")
            if address in normalized:
                self.module.fail_json(msg="dns_servers must contain distinct IP addresses.")
            normalized.append(address)

    def get_resource(self):
        self._validate_parameters()
        node = self.module.params["node"]
        self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(node))
        if not self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node", fail_on_missing=False):
            self.client.fail_request("Node {0} does not exist.".format(node))
        return self.call_api("Retrieving DNS from node {0}".format(node), self.api.nodes(node).dns.get)

    def get_update_parameters(self, resource):
        desired = dict(resource)
        search = self.module.params.get("search")
        if search is not None:
            desired.pop("search", None)
            if search:
                desired["search"] = search
        servers = self.module.params.get("dns_servers")
        if servers is not None:
            for index in range(1, 4):
                desired.pop("dns{0}".format(index), None)
            desired.update({"dns{0}".format(index): server for index, server in enumerate(servers, 1)})
        comparable = dict(resource)
        comparable_desired = dict(desired)
        for key in ("dns1", "dns2", "dns3"):
            if key in comparable:
                comparable[key] = str(ipaddress.ip_address(comparable[key]))
            if key in comparable_desired:
                comparable_desired[key] = str(ipaddress.ip_address(comparable_desired[key]))
        if not self.get_parameter_updates(comparable, comparable_desired, {}) and comparable.keys() == comparable_desired.keys():
            return {}
        return dict(desired, search=desired.get("search", ""))

    def update_resource(self, resource, updates):
        node = self.module.params["node"]
        self.call_mutation("Updating DNS on node {0}".format(node), self.api.nodes(node).dns.put, **updates)
        return self.get_resource()

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        return {key: value for key, value in updates.items() if value}

    def create_resource(self):
        self.module.fail_json(msg="DNS configuration can only be updated on an existing node.")

    def delete_resource(self, resource):
        self.module.fail_json(msg="The Proxmox API does not support deleting node DNS configuration.")


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "node": {"type": "str", "required": True},
            "search": {"type": "str"},
            "dns_servers": {"type": "list", "elements": "str"},
            "state": {"type": "str", "choices": ["present"], "default": "present"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeDnsModule(module).execute()


if __name__ == "__main__":
    main()
