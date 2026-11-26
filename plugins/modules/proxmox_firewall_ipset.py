#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_ipset
short_description: Manage Proxmox VE firewall IP sets
description:
- Reconciles a named datacenter or guest IP set and its optional authoritative member list.
- Supports native rename, comments, IPv4/IPv6 and alias members, exclusion flags and guarded member updates/removal.
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
    description: Case-sensitive IP-set name, 2 to 64 letters, digits, underscores or dashes, starting with a letter.
  rename:
    type: str
    description: New name for an existing IP set. Repeated calls recognize the already-renamed target. Native rename does not update references in rules.
  comment:
    type: str
    description: Set description; omission preserves it and an empty string clears it. Whitespace is stripped; literal C(0) cannot be persisted and is rejected.
  entries:
    type: list
    elements: dict
    description:
    - Authoritative member list. Omission preserves membership; an empty list removes all members.
    - Unspecified member comments and exclusion flags are preserved for retained members. New members default to no comment and C(nomatch=false).
    - IP networks are normalized as Proxmox stores them; aliases must already exist in the selected or datacenter scope.
    - Inherited guest aliases are checked through available references; the native write API validates restrictions on their resolved addresses.
    suboptions:
      cidr:
        type: str
        required: true
        description: IPv4/IPv6 address, CIDR or alias, optionally prefixed by C(dc/) or C(guest/). Zero-prefix networks are not supported by native IP sets.
      comment:
        type: str
        description: Member comment; empty clears it. Literal C(0) and line breaks are rejected.
      nomatch:
        type: bool
        description: Exclude this member from the set.
  digest:
    type: str
    description:
    - Optional expected IP-set-list digest. Native list/member digests guard supported metadata and member writes.
    - Native member creation and whole-set deletion have no digest parameter.
  force:
    type: bool
    default: false
    description: With C(state=absent), permit native atomic deletion of a populated set. Otherwise populated deletion fails in both modes.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
- ansible.proxmox.proxmox_firewall
attributes:
  check_mode:
    support: full
    description: Validates scope, membership and deletion guards without writing.
notes:
- With C(state=absent), O(rename), O(comment) and O(entries) are ignored. Set deletion does not remove references from firewall rules.
- Native write privileges are C(Sys.Modify) for datacenter scope or C(VM.Config.Network) for guest scope, in addition to the collection role precheck.
- Operations on a member list are sequential, not transactional. Successful writes remain visible as C(changed=true) if a later step fails.
- Does not enable a firewall or modify rules; changing a referenced set can affect active rules.
"""

EXAMPLES = r"""
- name: Ensure a datacenter IP set and exact membership
  ansible.proxmox.proxmox_firewall_ipset:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: management_hosts
    entries:
      - cidr: 192.0.2.0/24
      - cidr: 192.0.2.25
        nomatch: true
        comment: Excluded host

- name: Clear members while preserving the set
  ansible.proxmox.proxmox_firewall_ipset:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: management_hosts
    entries: []

- name: Delete an unused empty IP set
  ansible.proxmox.proxmox_firewall_ipset:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: obsolete_hosts
    state: absent
"""

RETURN = r"""
ipset:
  description: Verified or check-mode-predicted IP set, or null after deletion. Additional native fields are retained.
  type: dict
  returned: always
  contains:
    name:
      type: str
      description: IP-set name.
    comment:
      type: str
      description: Optional set description.
    digest:
      type: str
      description: Native IP-set-list digest, omitted from changed check-mode predictions.
    entries:
      type: list
      elements: dict
      description: Native members, including cidr, optional comment/nomatch and member-list digest.
"""

import ipaddress
import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope, firewall_scope_spec
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

CLUSTER_FIREWALL = "cluster/firewall"


class ProxmoxFirewallIpsetModule(ProxmoxFirewallScope, ProxmoxResourceModule):
    result_key = "ipset"

    def _member_key(self, value):
        if isinstance(value, str) and re.fullmatch(r"(?:dc/|guest/)?[A-Za-z][A-Za-z0-9_-]+", value):
            return value
        try:
            if not isinstance(value, str) or "%" in value or ("/" in value and not value.rsplit("/", 1)[1].isdigit()):
                raise ValueError()
            address = ipaddress.ip_network(value, strict=False)
            if address.prefixlen == 0:
                raise ValueError()
        except ValueError:
            self.client.fail_request("IP-set members require a valid address, nonzero numeric CIDR or alias.")
        return str(address.network_address) if address.prefixlen == address.max_prefixlen else str(address)

    def _validate_alias_members(self, entries, root):
        aliases = [row["cidr"] for row in entries if re.fullmatch(r"(?:dc/|guest/)?[A-Za-z][A-Za-z0-9_-]+", row["cidr"])]
        if not aliases:
            return
        local = self.firewall_records(root + "/aliases", "name", required=("cidr",))
        inherited = {}
        for alias in aliases:
            found = self._alias_definition(alias, root, local, inherited)
            if found is None:
                self.client.fail_request("IP-set member alias does not exist: {0}.".format(alias))
            if "cidr" in found:
                self._member_key(found["cidr"])

    def _alias_definition(self, alias, root, local, inherited):
        prefix, separator, name = alias.partition("/")
        scope = prefix if separator else ""
        name = name if separator else prefix
        if scope == "guest" and root == CLUSTER_FIREWALL:
            self.client.fail_request("A datacenter IP set cannot reference a guest alias.")
        candidates = [] if scope == "dc" and root != CLUSTER_FIREWALL else local
        found = next((row for row in candidates if row["name"].lower() == name.lower()), None)
        if found is not None:
            return found
        if root == CLUSTER_FIREWALL or scope == "guest":
            return None
        if "records" not in inherited:
            inherited["records"] = self.firewall_records(root + "/refs", "name", required=("type", "scope", "ref"), type="alias")
        return next((row for row in inherited["records"] if row["scope"] == "dc" and row["name"].lower() == name.lower()), None)

    def _requested_entries(self, current):
        requested = self.module.params.get("entries")
        if requested is None:
            return None
        existing = {self._member_key(row["cidr"]): row for row in current}
        desired = {}
        for row in requested:
            key = self._member_key(row["cidr"])
            if key in desired:
                self.client.fail_request("Duplicate IP-set member after normalization: {0}.".format(key))
            entry = {"cidr": key, "nomatch": bool(existing.get(key, {}).get("nomatch", False)), "comment": existing.get(key, {}).get("comment", "")}
            if row.get("nomatch") is not None:
                entry["nomatch"] = bool(row["nomatch"])
            if row.get("comment") is not None:
                entry["comment"] = self.firewall_comment(row["comment"])
            desired[key] = entry
        return [desired[key] for key in sorted(desired)]

    def _member_values(self, rows):
        return sorted((self._member_key(row["cidr"]), bool(row.get("nomatch", False)), row.get("comment", "")) for row in rows)

    def get_resource(self, readback=False):
        params = self.module.params
        name = self.firewall_name(params["name"])
        present = params.get("state", "present") == "present"
        self._target = self.firewall_name(params["rename"]) if present and params.get("rename") is not None else name
        self._comment = self.firewall_comment(params.get("comment")) if present else None
        root = self.firewall_endpoint(missing_ok=not present and not readback)
        if root is None:
            return None
        self._sets_endpoint = root + "/ipset"
        records = self.firewall_records(self._sets_endpoint, "name")
        self._sets_digest = self.firewall_digest(records, check_expected=not readback)
        record = self._select_ipset(records, name)
        entries = [] if record is None else self.firewall_records(self._sets_endpoint + "/" + quote(record["name"], safe=""), "cidr")
        if len({r["cidr"] for r in entries}) != len(entries):
            self.client.fail_request("IP-set discovery returned invalid or duplicate members.")
        if not present and entries and not params.get("force", False):
            self.client.fail_request("IP set is not empty; clear entries or explicitly set force=true.")
        if present:
            desired = self._requested_entries(entries)
            if desired is not None:
                self._validate_alias_members(desired, root)
        return dict(record, entries=entries) if record is not None else None

    def _select_ipset(self, records, name):
        if len({row["name"] for row in records}) != len(records):
            self.client.fail_request("IP-set discovery returned invalid or duplicate names.")
        original = next((row for row in records if row["name"] == name), None)
        target = next((row for row in records if row["name"] == self._target), None)
        if original is not None and target is not None and original is not target:
            self.client.fail_request("The requested IP-set rename target already exists.")
        selected = original or target
        if selected is None and self._target != name:
            self.client.fail_request("Neither the source IP set nor its rename target exists.")
        return selected

    def get_update_parameters(self, resource):
        updates = {}
        if resource["name"] != self._target:
            updates["name"] = self._target
        if self._comment is not None and resource.get("comment", "") != self._comment:
            updates["comment"] = self._comment
        desired = self._requested_entries(resource["entries"])
        if desired is not None and self._member_values(desired) != self._member_values(resource["entries"]):
            updates["entries"] = desired
        return updates

    def _observed(self):
        result = self.get_resource(readback=True)
        if result is None:
            self.client.fail_request("IP set disappeared while applying its requested state.")
        return result

    def create_resource(self):
        values = {"name": self.module.params["name"]}
        if self._comment is not None:
            values["comment"] = self._comment
        self.call_mutation("Creating firewall IP set", self.api.post, self._sets_endpoint, **values)
        resource = self._observed()
        return self.update_resource(resource, self.get_update_parameters(resource))

    def _reconcile_entries(self, resource, desired):
        expected = {
            self._member_key(row["cidr"]): {
                "cidr": self._member_key(row["cidr"]),
                "comment": row.get("comment", ""),
                "nomatch": bool(row.get("nomatch", False)),
            }
            for row in resource["entries"]
        }
        wanted = {row["cidr"]: row for row in desired}
        for key in sorted(set(expected).union(wanted)):
            endpoint = self._sets_endpoint + "/" + quote(resource["name"], safe="")
            if key not in wanted:
                digest = self.firewall_digest(resource["entries"], check_expected=False)
                self.call_mutation("Removing firewall IP-set member", self.api.delete, endpoint + "/" + quote(key, safe=""), digest=digest)
                del expected[key]
            elif key not in expected:
                values = dict(wanted[key], nomatch=int(wanted[key]["nomatch"]))
                self.call_mutation("Adding firewall IP-set member", self.api.post, endpoint, **values)
                expected[key] = wanted[key]
            elif self._member_values([expected[key]]) != self._member_values([wanted[key]]):
                digest = self.firewall_digest(resource["entries"], check_expected=False)
                self.call_mutation(
                    "Updating firewall IP-set member",
                    self.api.put,
                    endpoint + "/" + quote(key, safe=""),
                    digest=digest,
                    comment=wanted[key]["comment"],
                    nomatch=int(wanted[key]["nomatch"]),
                )
                expected[key] = wanted[key]
            else:
                continue
            resource = self._observed()
            if self._member_values(resource["entries"]) != self._member_values(expected.values()):
                self.client.fail_request("IP-set members changed concurrently or a member write could not be verified.")
        return resource

    def update_resource(self, resource, updates):
        if "name" in updates or "comment" in updates:
            old_members = self._member_values(resource["entries"])
            values = {"name": self._target, "rename": resource["name"], "digest": self._sets_digest}
            if self._comment is not None:
                values["comment"] = self._comment
            self.call_mutation("Renaming/updating firewall IP set", self.api.post, self._sets_endpoint, **values)
            resource = self._observed()
            if self._member_values(resource["entries"]) != old_members:
                self.client.fail_request("IP-set members changed during metadata reconciliation.")
        if "entries" in updates:
            resource = self._reconcile_entries(resource, updates["entries"])
        if self.get_update_parameters(resource):
            self.client.fail_request("The requested IP-set state could not be verified.")
        return resource

    def delete_resource(self, resource):
        self.call_mutation(
            "Deleting firewall IP set",
            self.api.delete,
            self._sets_endpoint + "/" + quote(resource["name"], safe=""),
            force=int(self.module.params.get("force", False)),
        )
        if self.get_resource(readback=True) is not None:
            self.client.fail_request("IP-set deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "delete":
            return None
        result = dict(resource or {"name": self.module.params["name"], "entries": []})
        result["name"] = self._target
        if self._comment is not None:
            result["comment"] = self._comment
        desired = self._requested_entries(result["entries"])
        if desired is not None:
            result["entries"] = desired
        result.pop("digest", None)
        return result


def main():
    options = firewall_scope_spec()
    options.update(
        {
            "name": {"type": "str", "required": True},
            "rename": {"type": "str"},
            "comment": {"type": "str"},
            "digest": {"type": "str"},
            "force": {"type": "bool", "default": False},
            "entries": {
                "type": "list",
                "elements": "dict",
                "options": {"cidr": {"type": "str", "required": True}, "comment": {"type": "str"}, "nomatch": {"type": "bool"}},
            },
        }
    )
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(options),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallIpsetModule(module).execute()


if __name__ == "__main__":
    main()
