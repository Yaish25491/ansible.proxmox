#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_log_info
short_description: Retrieve Proxmox VE firewall logs
description:
- Reads one page of node or QEMU/LXC firewall logs without modifying firewall configuration.
- Supports pagination and integer UNIX epoch bounds; preserves returned records and placeholders.
version_added: 1.0.0
options:
  scope:
    type: str
    choices:
    - node
    - qemu
    - lxc
    default: node
    description: Select node or guest firewall logs.
  node:
    required: true
    type: str
    description: Required node name for every scope.
  vmid:
    type: int
    description: Required for QEMU or LXC scope. Guest ID between 100 and 999999999; omit for other scopes.
  start:
    type: int
    description: Nonnegative offset into the filtered output. The server defaults to zero when omitted.
  limit:
    type: int
    description:
    - Nonnegative page size. The server defaults to 50 when omitted; zero returns all remaining matching records after the offset.
    - A zero limit can return a large response on nodes with extensive log history.
  since:
    type: int
    description: Nonnegative lower UNIX epoch bound in seconds. Omit or use zero to disable the lower comparison.
  until:
    type: int
    description: Nonnegative upper UNIX epoch bound in seconds. Omit or use zero to disable the upper comparison.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves current information without mutations.
notes:
- Requires the C(Administrator) role propagated on C(/nodes/{node}) for node scope or C(/vms/{vmid}) for guest scope.
- Native API privileges are C(Sys.Syslog) for node logs and C(VM.Console) for guest logs.
- Reads the firewall log API, not the node journal. Available records depend on the configured firewall logging backend.
- Offsets do not identify stable snapshots; logs may rotate between requests. Only one page is fetched.
- Supplying either epoch bound also searches rotated logs; a zero bound disables its timestamp comparison.
- The API response-envelope total is not exposed by the shared client and is not returned.
- After required permission checks, missing discovered nodes, guests or guest locations/types return an empty list.
- API failures and malformed discovery/log-list shapes are structured errors, including a guest disappearing after successful discovery.
- Native C(no content) and file-access diagnostic records are preserved. A diagnostic record does not establish that real log content was read.
- Log text, ordering, line numbers and any additional server fields are preserved. Discovery and data are refreshed on each invocation.
- Supports Proxmox VE 9.
"""

EXAMPLES = r"""
- name: Retrieve Proxmox VE firewall logs
  ansible.proxmox.proxmox_firewall_log_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    limit: 10
    since: 1793491200

- name: Read a container firewall log page
  ansible.proxmox.proxmox_firewall_log_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: lxc
    node: pve01
    vmid: 101
    limit: 10

- name: Read a virtual machine log page with an unbounded upper timestamp
  ansible.proxmox.proxmox_firewall_log_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: qemu
    node: pve01
    vmid: 100
    start: 1
    limit: 10
    since: 1793491200
    until: 0
"""

RETURN = r"""
firewall_log:
  description:
  - Unmodified line records for one requested page. Empty for a missing node or guest or an empty page.
  - Additional server fields are preserved, so records can contain keys beyond those documented below.
  type: list
  elements: dict
  returned: always
  contains:
    n:
      description: One-based server line number in filtered output, rather than a stable physical-file position.
      type: int
      returned: when provided by the server
    t:
      description: Native log text, including any no-content placeholder or backend diagnostic.
      type: str
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


class ProxmoxFirewallLogInfoModule(ProxmoxInfoModule):
    """Read scoped firewall information with checked node and guest discovery."""

    result_key = "firewall_log"

    def _validate_parameters(self):
        scope = self.module.params["scope"]
        node = self.module.params.get("node")
        vmid = self.module.params.get("vmid")
        # Also protect callers that construct the handler without argument parsing.
        if scope not in ("node", "qemu", "lxc"):
            self.module.fail_json(msg="Unsupported firewall scope.")
        if node is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", node) is None:
            self.module.fail_json(msg="node must be a nonempty node name without path separators.")
        if scope == "node" and vmid is not None:
            self.module.fail_json(msg="node scope does not accept vmid.")
        if scope in ("qemu", "lxc") and (vmid is None or not 100 <= vmid <= 999999999):
            self.module.fail_json(msg="guest scope requires vmid between 100 and 999999999.")
        for name in ("start", "limit", "since", "until"):
            value = self.module.params.get(name)
            if value is not None and value < 0:
                self.module.fail_json(msg="{0} must be zero or greater.".format(name))
        since, until = self.module.params.get("since"), self.module.params.get("until")
        # Zero disables the native bound, so it is not compared.
        if since and until and since > until:
            self.module.fail_json(msg="since must not be greater than until.")

    def _endpoint(self):
        scope = self.module.params["scope"]
        node = self.module.params["node"]
        vmid = self.module.params.get("vmid")
        permission_path = "/nodes/{0}".format(node) if scope == "node" else "/vms/{0}".format(vmid)
        self.client.validate_pve_permissions(self.api, permission_path)
        nodes = self.call_api("Listing Proxmox nodes", self.api.get, "nodes")
        if not isinstance(nodes, list) or any(not isinstance(entry, dict) or "node" not in entry for entry in nodes):
            return self.client.fail_request("Node discovery returned an invalid node list; refusing to report absence.")
        if not self.validate_proxmox_resource_exist("node", node, lambda: nodes, resource_key="node", fail_on_missing=False):
            return None
        prefix = "nodes/{0}".format(quote(node, safe=""))
        if scope == "node":
            return prefix + "/firewall/log"
        guests = self.call_api("Listing Proxmox guests", self.api.get, "cluster/resources", type="vm")
        if not isinstance(guests, list) or any(not isinstance(entry, dict) or not {"vmid", "type", "node"}.issubset(entry) for entry in guests):
            return self.client.fail_request("Guest discovery returned an invalid guest list; refusing to report absence.")
        if not any(str(guest.get("vmid")) == str(vmid) and guest.get("type") == scope and guest.get("node") == node for guest in guests):
            return None
        return "{0}/{1}/{2}/firewall/log".format(prefix, scope, vmid)

    def get_info(self):
        self._validate_parameters()
        endpoint = self._endpoint()
        if endpoint is None:
            return []
        parameters = {name: self.module.params[name] for name in ("start", "limit", "since", "until") if self.module.params.get(name) is not None}
        records = self.call_api("Reading firewall log", self.api.get, endpoint, **parameters)
        if not isinstance(records, list) or any(not isinstance(entry, dict) for entry in records):
            return self.client.fail_request("Firewall API returned an invalid firewall log list.")
        return records


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "scope": {"type": "str", "choices": ["node", "qemu", "lxc"], "default": "node"},
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int"},
                "start": {"type": "int"},
                "limit": {"type": "int"},
                "since": {"type": "int"},
                "until": {"type": "int"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallLogInfoModule(module).execute()


if __name__ == "__main__":
    main()
