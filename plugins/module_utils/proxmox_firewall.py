# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
from urllib.parse import quote

from .proxmox_base import ProxmoxBase


class ProxmoxFirewallScope(ProxmoxBase):
    """Resolve firewall configuration scope with checked, fresh discovery."""

    def firewall_endpoint(self, missing_ok=False):
        params = self.module.params
        scope, node, vmid = params.get("scope", "cluster"), params.get("node"), params.get("vmid")
        if scope == "cluster":
            if node is not None or vmid is not None:
                self.module.fail_json(msg="cluster scope does not accept node or vmid.")
            self.client.validate_pve_permissions(self.api, "/")
            return "cluster/firewall"
        if scope not in ("qemu", "lxc"):
            self.module.fail_json(msg="scope must be cluster, qemu or lxc.")
        if not isinstance(node, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", node) is None:
            self.module.fail_json(msg="Guest scope requires a valid node name.")
        if not isinstance(vmid, int) or not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="Guest scope requires vmid between 100 and 999999999.")
        self.client.validate_pve_permissions(self.api, "/vms/{0}".format(vmid))
        nodes = self.firewall_records("nodes", "node")
        node_exists = self.validate_proxmox_resource_exist("node", node, lambda: nodes, resource_key="node", fail_on_missing=False)
        if node_exists:
            guests = self.firewall_records("cluster/resources", "vmid", required=("node", "type"), type="vm")
            if any(str(g["vmid"]) == str(vmid) and g["node"] == node and g["type"] == scope for g in guests):
                return "nodes/{0}/{1}/{2}/firewall".format(quote(node, safe=""), scope, vmid)
        if missing_ok:
            return None
        self.client.fail_request("The selected firewall node/guest/type does not exist at this location.")

    def firewall_records(self, endpoint, key, required=(), **parameters):
        records = self.call_api("Reading {0}".format(endpoint), self.api.get, endpoint, **parameters)
        keys = {key}.union(required)
        if not isinstance(records, list) or any(not isinstance(row, dict) or not keys.issubset(row) for row in records):
            self.client.fail_request("Firewall discovery returned an invalid list; refusing to report absence.")
        for row in records:
            for field in keys:
                value = row[field]
                valid = isinstance(value, (int, str)) and str(value).isdigit() if field in ("vmid", "pos") else isinstance(value, str) and bool(value)
                if not valid:
                    self.client.fail_request("Firewall discovery returned invalid identity or reference fields.")
        return records

    def firewall_digest(self, records, check_expected=True):
        digest = records[0].get("digest") if records else None
        if records and (not isinstance(digest, str) or not digest or any(row.get("digest") != digest for row in records)):
            self.client.fail_request("Firewall API did not return a consistent configuration digest.")
        requested = self.module.params.get("digest")
        if check_expected and requested is not None and requested != digest:
            self.client.fail_request("Firewall configuration digest does not match; read the current configuration before retrying.")
        return digest

    def firewall_name(self, name, maximum=64):
        if not isinstance(name, str) or not 2 <= len(name) <= maximum or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]+", name) is None:
            self.module.fail_json(msg="Firewall name must contain 2 to {0} letters, digits, underscores or dashes and start with a letter.".format(maximum))
        return name

    def firewall_comment(self, value):
        if value is None:
            return None
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            self.module.fail_json(msg="Firewall comments must be strings without line breaks.")
        value = value.strip()
        if value == "0":
            self.module.fail_json(msg="A literal zero firewall comment cannot be persisted by Proxmox; use an empty string to clear it.")
        return value


def firewall_scope_spec():
    """Arguments shared by datacenter and guest firewall resource pairs."""
    return {
        "scope": {"type": "str", "choices": ["cluster", "qemu", "lxc"], "default": "cluster"},
        "node": {"type": "str"},
        "vmid": {"type": "int"},
    }
