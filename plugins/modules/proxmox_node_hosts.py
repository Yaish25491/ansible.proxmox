#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_node_hosts
short_description: Manage the Proxmox VE node hosts file
description:
  - Replaces the complete C(/etc/hosts) file on an existing Proxmox VE node.
  - Compares the exact contents, including comments, whitespace and final newlines, before writing.
  - Uses the configuration digest to prevent overwriting concurrent changes and reads the configuration after an update.
version_added: "1.0.0"
options:
  node:
    description: Proxmox VE node name.
    type: str
    required: true
  data:
    description:
      - Complete target contents of C(/etc/hosts), including any entries that must be retained.
      - An empty string clears the file. This can disrupt name resolution and cluster services.
      - Each nonempty, non-comment line must start with an IPv4 or IPv6 address followed by optional host names.
      - Host names may contain ASCII letters, digits, dots and hyphens. Proxmox accepts full-line comments, but rejects inline comments.
      - Comments, ordering, whitespace and final newlines are preserved; no entries are automatically merged or added.
    type: str
    required: true
  digest:
    description:
      - Expected configuration digest, obtained from the V(hosts) subset of M(ansible.proxmox.proxmox_node_info).
      - Fails if the current digest differs, including for an otherwise unchanged request and in check mode.
      - When omitted, the module uses the digest from its initial read to guard the write against concurrent changes.
      - Must not be empty. A successful update returns a new digest; read it again before a subsequent guarded update.
    type: str
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Validates the requested contents and predicts the configuration without writing the hosts file.
notes:
  - Requires all privileges of the C(Administrator) role propagated on C(/), because reading the hosts file checks root permissions.
  - Hosts settings apply to the entire node. Retain required localhost, node and cluster name mappings.
  - Updates are synchronous; O(task_timeout) does not affect this operation. O(api_timeout) controls each HTTPS request.
  - A digest conflict fails without retrying or overwriting the intervening change.
"""

EXAMPLES = r"""
- name: Manage the complete node hosts file from a template
  ansible.proxmox.proxmox_node_hosts:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    data: "{{ lookup('ansible.builtin.template', 'pve01-hosts.j2') }}"

- name: Read hosts contents and digest before a guarded change
  ansible.proxmox.proxmox_node_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    gather_subset: [hosts]
  register: node_hosts

- name: Replace hosts from a managed file using the captured digest
  ansible.proxmox.proxmox_node_hosts:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    data: "{{ lookup('ansible.builtin.file', 'pve01-hosts', rstrip=False) }}"
    digest: "{{ node_hosts.nodes[0].hosts.digest }}"
"""

RETURN = r"""
hosts:
  description: Hosts configuration returned by Proxmox, or predicted in check mode.
  returned: always
  type: dict
  contains:
    data:
      description: Complete hosts file contents.
      type: str
      returned: always
      sample: "127.0.0.1 localhost\n192.0.2.1 pve01.example.com pve01\n"
    digest:
      description: SHA-1 configuration digest, calculated from the target UTF-8 contents for a change in check mode.
      type: str
      returned: always
      sample: 97d45b371ab673c8a2295e9fd8d6e0db28defb13
"""

import hashlib
import ipaddress
import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxNodeHostsModule(ProxmoxResourceModule):
    """Reconcile the complete hosts file through the shared resource lifecycle."""

    result_key = "hosts"

    def _validate_parameters(self):
        if not self.module.params["node"]:
            self.module.fail_json(msg="node must not be empty.")
        digest = self.module.params.get("digest")
        if digest is not None and (not digest or len(digest) > 64):
            self.module.fail_json(msg="digest must contain between 1 and 64 characters.")
        for number, line in enumerate(self.module.params["data"].split("\n"), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            address, *names = re.split(r"\s+", line.rstrip())
            try:
                ipaddress.ip_address(address)
                if "%" in address:
                    raise ValueError("Scoped addresses are not supported")
            except ValueError:
                self.module.fail_json(msg="data line {0} must start with an IPv4 or IPv6 address.".format(number))
            if any(re.fullmatch(r"[.\-a-zA-Z0-9]+", name) is None for name in names):
                self.module.fail_json(msg="data line {0} contains an invalid host name or inline comment.".format(number))

    def get_resource(self):
        self._validate_parameters()
        node = self.module.params["node"]
        self.client.validate_pve_permissions(self.api, "/")
        if not self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node", fail_on_missing=False):
            self.client.fail_request("Node {0} does not exist.".format(node))
        return self.call_api("Retrieving hosts from node {0}".format(node), self.api.nodes(node).hosts.get)

    def get_update_parameters(self, resource):
        digest = self.module.params.get("digest")
        if digest is not None and digest != resource["digest"]:
            self.client.fail_request("Hosts configuration digest does not match; read the current configuration before retrying.")
        updates = self.get_parameter_updates(resource, {"data": self.module.params["data"]}, {})
        if updates:
            updates["digest"] = resource["digest"]
        return updates

    def update_resource(self, resource, updates):
        node = self.module.params["node"]
        self.call_mutation("Updating hosts on node {0}".format(node), self.api.nodes(node).hosts.post, **updates)
        return self.get_resource()

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        predicted = self.apply_parameter_updates(resource, updates)
        # PVE uses SHA-1 as a file-version token, not for authentication.
        predicted["digest"] = hashlib.sha1(predicted["data"].encode("utf-8"), usedforsecurity=False).hexdigest()
        return predicted

    def create_resource(self):
        self.module.fail_json(msg="Hosts configuration can only be updated on an existing node.")

    def delete_resource(self, resource):
        self.module.fail_json(msg="The Proxmox API does not support deleting node hosts configuration.")


def main():
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "node": {"type": "str", "required": True},
            "data": {"type": "str", "required": True},
            "digest": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeHostsModule(module).execute()


if __name__ == "__main__":
    main()
