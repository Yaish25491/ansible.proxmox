#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_ref_fixture
short_description: Manage only owned integration fixtures
description:
- Role-local fixture support; never enables a firewall or changes controller authentication.
- Active operations require matching kind, ownership comment and original alias address or empty IP-set contents.
- Alias cleanup uses the native list digest; IP-set cleanup explicitly disables forced member removal.
version_added: 1.0.0
options:
  resource_id:
    type: str
    required: true
    description: Owned alias or empty IP-set name.
  owned_ids:
    type: list
    elements: str
    required: true
    description: Names preflighted absent by this invocation.
  kind:
    type: str
    required: true
    choices:
    - alias
    - ipset
    description: Unreferenced definition to create.
  read_only:
    type: bool
    default: false
    description: Inspect without mutations.
  state:
    type: str
    choices:
    - present
    - absent
    default: present
    description: Whether the owned fixture should exist.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
"""

EXAMPLES = r"""
- name: Inspect only an owned fixture
  proxmox_firewall_ref_fixture:
    resource_id: ansible-ref-1000020000-alias
    owned_ids: [ansible-ref-1000020000-alias]
    kind: alias
    read_only: true
"""

RETURN = r"""
fixture:
  description: Owned fixture metadata, or null when absent.
  type: dict
  returned: always
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class FixtureModule(ProxmoxResourceModule):
    """Create unreferenced definitions without enabling a firewall or rule."""

    result_key = "fixture"
    comment = "Owned Ansible reference fixture"
    address = "192.0.2.1"
    cidr = address + "/32"

    def _endpoint(self):
        identifier = self.module.params["resource_id"]
        kind = self.module.params["kind"]
        if (
            identifier not in self.module.params["owned_ids"]
            or kind not in ("alias", "ipset")
            or re.fullmatch(r"ansible-ref-[0-9]{10}-" + re.escape(kind), identifier) is None
        ):
            self.client.fail_request("Refusing a reference outside this invocation's owned identifiers.")
        self.client.validate_pve_permissions(self.api, "/")
        return "cluster/firewall/" + ("aliases" if self.module.params["kind"] == "alias" else "ipset")

    def get_resource(self):
        endpoint = self._endpoint()
        entries = self.call_api("Listing reference fixtures", self.api.get, endpoint)
        if not isinstance(entries, list) or any(not isinstance(entry, dict) or "name" not in entry for entry in entries):
            return self.client.fail_request("Reference fixture discovery returned an invalid definition list.")
        entry = next((entry for entry in entries if entry["name"] == self.module.params["resource_id"]), None)
        if entry is None or self.module.params["read_only"]:
            return dict(entry) if entry is not None else None
        if entry.get("comment") != self.comment:
            return self.client.fail_request("Refusing to modify an owned name with an unrecognized fixture comment.")
        if self.module.params["kind"] == "alias":
            if entry.get("cidr") not in (self.address, self.cidr):
                return self.client.fail_request("Refusing to modify a changed fixture alias value.")
        else:
            members = self.call_api("Inspecting owned IP-set contents", self.api.get, endpoint + "/" + quote(entry["name"], safe=""))
            if not isinstance(members, list):
                return self.client.fail_request("Cannot verify fixture IP-set emptiness from an invalid member list.")
            if members:
                return self.client.fail_request("Refusing to modify a nonempty fixture IP set.")
        return dict(entry)

    def create_resource(self):
        values = {"name": self.module.params["resource_id"], "comment": self.comment}
        if self.module.params["kind"] == "alias":
            values["cidr"] = self.cidr
        self.call_mutation("Creating unused reference fixture", self.api.post, self._endpoint(), **values)
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Reference fixture creation could not be verified.")
        return result

    def update_resource(self, resource, updates):
        return resource

    def delete_resource(self, resource):
        parameters = {"force": 0}
        if self.module.params["kind"] == "alias":
            digest = resource.get("digest")
            if not isinstance(digest, str) or not digest:
                return self.client.fail_request("Refusing alias cleanup without the native configuration digest.")
            parameters = {"digest": digest}
        self.call_mutation("Deleting unused reference fixture", self.api.delete,
                           self._endpoint() + "/" + quote(resource["name"], safe=""), **parameters)
        if self.get_resource() is not None:
            self.client.fail_request("Reference fixture removal could not be verified.")
        return None

    def execute(self):
        if self.module.params["read_only"]:
            self.exit(changed=False, resource=self.get_resource())
        super().execute()


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(
            {
                "resource_id": {"type": "str", "required": True},
                "owned_ids": {"type": "list", "elements": "str", "required": True},
                "kind": {"type": "str", "required": True, "choices": ["alias", "ipset"]},
                "read_only": {"type": "bool", "default": False},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    FixtureModule(module).execute()


if __name__ == "__main__":
    main()
