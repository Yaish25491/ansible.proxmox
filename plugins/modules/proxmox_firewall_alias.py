#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_alias
short_description: Manage Proxmox VE firewall aliases
description:
- Reconciles one named IPv4/IPv6 alias in datacenter or guest firewall configuration.
- Supports creation, address/comment updates, native rename and deletion without replacing neighboring aliases.
version_added: 1.0.0
options:
  state:
    type: str
    choices: [present, absent]
    default: present
    description: Whether the selected definition should exist.
  name:
    type: str
    required: true
    description: Alias name, 2 to 64 letters, digits, underscores or dashes, starting with a letter. Identity is case insensitive.
  cidr:
    type: str
    description: IPv4/IPv6 address or CIDR. Required when creating an alias; omission preserves an existing address.
  comment:
    type: str
    description:
    - Optional description. Omission preserves it; an empty string clears it. Leading/trailing whitespace is stripped.
    - Literal C(0) is rejected because the native configuration writer cannot persist it.
  rename:
    type: str
    description:
    - Desired new name for an existing alias. A repeated call recognizes the already-renamed alias.
    - Native rename changes the definition only; update any firewall rule references separately.
  digest:
    type: str
    description: Optional expected alias-list digest. Updates/deletion also send the freshly read native digest to prevent concurrent replacement.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
- ansible.proxmox.proxmox_firewall
attributes:
  check_mode:
    support: full
    description: Validates discovery and predicts the result without writes.
notes:
- With C(state=absent), O(cidr), O(comment) and O(rename) are ignored. Deletion removes only the selected local alias, not referencing rules.
- Native write privileges are C(Sys.Modify) for datacenter aliases or C(VM.Config.Network) for guest aliases; the collection retains its stronger role precheck.
- Does not enable a firewall or modify rules. Existing active rules can be affected by alias changes.
"""

EXAMPLES = r"""
- name: Ensure a datacenter alias exists
  ansible.proxmox.proxmox_firewall_alias:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: management_net
    cidr: 192.0.2.0/24

- name: Rename an alias local to a VM
  ansible.proxmox.proxmox_firewall_alias:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    scope: qemu
    node: pve01
    vmid: 100
    name: old_network
    rename: application_net

- name: Remove an unused alias
  ansible.proxmox.proxmox_firewall_alias:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: obsolete_net
    state: absent
"""

RETURN = r"""
alias:
  description: Verified local alias, predicted alias in check mode, or null after deletion. Additional native fields are preserved.
  type: dict
  returned: always
  contains:
    name:
      type: str
      description: Alias spelling stored by Proxmox.
    cidr:
      type: str
      description: Address or CIDR. Host prefixes may be omitted by the server.
    comment:
      type: str
      description: Nonempty description when configured.
    digest:
      type: str
      description: Current alias-list digest, when returned by the server. Not predicted after a check-mode change.
"""

import ipaddress
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope, firewall_scope_spec
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxFirewallAliasModule(ProxmoxFirewallScope, ProxmoxResourceModule):
    result_key = "alias"

    def _address(self, value):
        try:
            if not isinstance(value, str) or "%" in value or ("/" in value and not value.rsplit("/", 1)[1].isdigit()):
                raise ValueError()
            address = ipaddress.ip_interface(value)
        except ValueError:
            self.client.fail_request("cidr must be an IPv4/IPv6 address or CIDR with a numeric prefix.")
        return str(address.ip) if address.network.prefixlen == address.max_prefixlen else str(address)

    def _alias_request(self):
        params = self.module.params
        name = self.firewall_name(params["name"])
        self._target = name
        if params.get("state", "present") == "present":
            self._target = self.firewall_name(params["rename"]) if params.get("rename") is not None else name
            self._comment = self.firewall_comment(params.get("comment"))
            self._cidr = self._address(params["cidr"]) if params.get("cidr") is not None else None
        return name

    def get_resource(self, readback=False):
        params = self.module.params
        name = self._alias_request()
        root = self.firewall_endpoint(missing_ok=params.get("state") == "absent" and not readback)
        if root is None:
            return None
        self._aliases_endpoint = root + "/aliases"
        aliases = self.firewall_records(self._aliases_endpoint, "name", required=("cidr",))
        self._digest = self.firewall_digest(aliases, check_expected=not readback)
        keys = [row["name"].lower() for row in aliases]
        if len(keys) != len(set(keys)):
            self.client.fail_request("Alias discovery returned invalid or duplicate names.")
        selected = next((row for row in aliases if row["name"].lower() == name.lower()), None)
        target = next((row for row in aliases if row["name"].lower() == self._target.lower()), None)
        if selected is not None and target is not None and selected is not target:
            self.client.fail_request("The requested alias rename target already exists.")
        if selected is None and self._target.lower() != name.lower():
            if target is None:
                self.client.fail_request("Neither the source alias nor the requested rename target exists.")
            selected = target
        if selected is None and params.get("state", "present") == "present" and self._cidr is None:
            self.module.fail_json(msg="cidr is required when creating an alias.")
        return dict(selected) if selected is not None else None

    def get_update_parameters(self, resource):
        updates = {}
        if self._cidr is not None and self._address(resource["cidr"]) != self._cidr:
            updates["cidr"] = self._cidr
        if self._comment is not None and resource.get("comment", "") != self._comment:
            updates["comment"] = self._comment
        if self.module.params.get("rename") is not None and resource["name"] != self._target:
            updates["rename"] = self._target
        return updates

    def _verified_alias(self, expected):
        result = self.get_resource(readback=True)
        if result is None or self.get_update_parameters(result) or result["name"].lower() != expected.lower():
            self.client.fail_request("The requested firewall alias change could not be verified.")
        return result

    def create_resource(self):
        values = {"name": self.module.params["name"], "cidr": self._cidr}
        if self._comment is not None:
            values["comment"] = self._comment
        self.call_mutation("Creating firewall alias", self.api.post, self._aliases_endpoint, **values)
        return self._verified_alias(values["name"])

    def update_resource(self, resource, updates):
        source = self._target if "rename" in updates and resource["name"].lower() == self._target.lower() else resource["name"]
        self.call_mutation("Updating firewall alias", self.api.put, self._aliases_endpoint + "/" + quote(source, safe=""), digest=self._digest, **updates)
        return self._verified_alias(self._target)

    def delete_resource(self, resource):
        self.call_mutation("Deleting firewall alias", self.api.delete, self._aliases_endpoint + "/" + quote(resource["name"], safe=""), digest=self._digest)
        if self.get_resource(readback=True) is not None:
            self.client.fail_request("Firewall alias deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "delete":
            return None
        result = dict(resource or {"name": self.module.params["name"], "cidr": self._cidr})
        if self._comment is not None:
            result["comment"] = self._comment
        if self._cidr is not None:
            result["cidr"] = self._cidr
        if updates and "rename" in updates:
            result["name"] = updates["rename"]
        result.pop("digest", None)
        return result


def main():
    options = firewall_scope_spec()
    options.update(
        {"name": {"type": "str", "required": True}, "cidr": {"type": "str"}, "comment": {"type": "str"}, "rename": {"type": "str"}, "digest": {"type": "str"}}
    )
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(options),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallAliasModule(module).execute()


if __name__ == "__main__":
    main()
