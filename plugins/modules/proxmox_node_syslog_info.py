#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_node_syslog_info
short_description: Retrieve system log entries from a Proxmox VE node
description:
  - Reads one page of system log entries through the node syslog API.
  - Supports an offset, a line limit, a service filter and date-time bounds.
  - Returns the API line records without modifying the node, including in check mode.
  - Returns an empty list when the selected node does not exist.
version_added: "1.0.0"
options:
  node:
    description: Proxmox VE node name.
    type: str
    required: true
  start:
    description:
      - Zero-based offset into the filtered log output. Must be nonnegative.
      - When omitted, the server starts at offset zero.
    type: int
  limit:
    description:
      - Maximum number of lines to return. Must be nonnegative.
      - When omitted or set to zero, the server uses its default of 50 lines.
      - This is a page size, not a request for the most recent lines. The module does not fetch additional pages automatically.
    type: int
  since:
    description:
      - Lower date-time bound interpreted by the node in its local timezone.
      - Use C(YYYY-MM-DD), C(YYYY-MM-DD HH:MM) or C(YYYY-MM-DD HH:MM:SS).
      - Calendar validity and the relationship to O(until) are checked by the server.
      - Omit for no lower date-time bound.
    type: str
  until:
    description:
      - Upper date-time bound interpreted by the node in its local timezone.
      - Uses the same formats as O(since). Omit for no upper date-time bound.
    type: str
  service:
    description:
      - Service ID passed to the server's journal unit filter, such as V(pvedaemon).
      - The server accepts up to 128 characters and maps V(sshd) to V(ssh) and V(postfix) to V(postfix@-).
      - Omit or pass an empty string to retrieve entries without a service filter.
    type: str
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves system log entries without modifying the node.
notes:
  - Requires all privileges of the C(Administrator) role propagated on C(/nodes/{node}), including the native API privilege C(Sys.Syslog).
  - Reads the Proxmox VE syslog endpoint. Journal cursors, priority filters and streaming are not supported by this endpoint.
  - Entries follow the server's journal order, from older to newer. Line numbers are one-based within the filtered output, before pagination.
  - When the filtered journal has no output, Proxmox returns a single record with C(n=1) and C(t=no content). This record is preserved.
  - A page beyond existing output can be empty. Permission, discovery and endpoint failures are reported as errors rather than empty results.
  - Journal contents can change or rotate between calls; offsets do not identify a stable snapshot.
  - Returns one page only. The API envelope's total count is not exposed by the shared client and is not returned by this module.
"""

EXAMPLES = r"""
- name: Read up to 50 system log lines
  ansible.proxmox.proxmox_node_syslog_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
  register: node_syslog

- name: Read a page of service logs for a fixed period
  ansible.proxmox.proxmox_node_syslog_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    service: pvedaemon
    since: '2026-11-01 00:00:00'
    until: '2026-11-02 00:00:00'
    start: 50
    limit: 25

- name: Read logs in check mode
  ansible.proxmox.proxmox_node_syslog_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    limit: 10
  check_mode: true
"""

RETURN = r"""
syslog:
  description:
    - Unmodified log line records returned by Proxmox for the requested page.
    - Empty when the selected node is missing or the page is beyond existing output.
    - A journal without output is represented by the server's C(no content) record.
  returned: always
  type: list
  elements: dict
  contains:
    n:
      description: One-based line number in the filtered output, before applying the page offset.
      type: int
      returned: always
      sample: 1
    t:
      description: Log line text, or the server's C(no content) placeholder.
      type: str
      returned: always
      sample: Nov 17 10:00:00 pve01 pvedaemon[1234] starting server
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxNodeSyslogInfoModule(ProxmoxInfoModule):
    """Read a filtered syslog page through the shared information lifecycle."""

    result_key = "syslog"

    def _validate_parameters(self):
        if not self.module.params["node"]:
            self.module.fail_json(msg="node must not be empty.")
        for name in ("start", "limit"):
            value = self.module.params.get(name)
            if value is not None and value < 0:
                self.module.fail_json(msg="{0} must be zero or greater.".format(name))
        for name in ("since", "until"):
            value = self.module.params.get(name)
            if value is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?", value, flags=re.ASCII) is None:
                self.module.fail_json(msg="{0} must use YYYY-MM-DD, YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS.".format(name))
        service = self.module.params.get("service")
        if service is not None and len(service) > 128:
            self.module.fail_json(msg="service must contain at most 128 characters.")

    def get_info(self):
        self._validate_parameters()
        node = self.module.params["node"]
        self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(node))
        summaries = self.call_api("Listing Proxmox nodes", self.api.nodes.get)
        if not self.validate_proxmox_resource_exist("node", node, lambda: summaries, resource_key="node", fail_on_missing=False):
            return []
        parameters = {name: self.module.params[name] for name in ("start", "limit", "since", "until", "service")
                      if self.module.params.get(name) is not None}
        return self.call_api("Retrieving syslog from node {0}".format(node), self.api.get, "nodes/{0}/syslog".format(node), **parameters)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "node": {"type": "str", "required": True},
            "start": {"type": "int"},
            "limit": {"type": "int"},
            "since": {"type": "str"},
            "until": {"type": "str"},
            "service": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeSyslogInfoModule(module).execute()


if __name__ == "__main__":
    main()
