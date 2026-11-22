#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_tfa_realm_fixture
short_description: Manage only an owned disconnected TFA test realm
description:
- Role-local fixture helper. Never connects to an identity provider or changes existing authentication realms.
version_added: 1.0.0
options:
  resource_id:
    description: Unique owned realm identifier.
    type: str
    required: true
  owned_ids:
    description: Identifiers preflighted absent by this invocation.
    type: list
    elements: str
    required: true
  read_only:
    description: Inspect without changing configuration.
    type: bool
    default: false
  state:
    description: Whether the owned fixture should exist.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
"""

EXAMPLES = r"""
- name: Inspect a candidate owned realm before claiming it
  proxmox_tfa_realm_fixture:
    resource_id: ansible-tfa-1000020000
    owned_ids: [ansible-tfa-1000020000]
    read_only: true
"""

RETURN = r"""
fixture:
  description: Owned realm ID and comment, or null when absent.
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


class ProxmoxTfaRealmFixtureModule(ProxmoxResourceModule):
    """Own only a uniquely named LDAP definition with connection checks disabled."""

    result_key = "fixture"
    comment = "Owned Ansible TFA realm fixture"

    def _path(self):
        return "access/domains/{0}".format(quote(self.module.params["resource_id"], safe=""))

    def get_resource(self):
        params = self.module.params
        identifier = params["resource_id"]
        if identifier not in params["owned_ids"] or re.fullmatch(r"ansible-tfa-[0-9]{10}", identifier) is None:
            self.client.fail_request("Refusing a realm outside this invocation's owned fixture identifiers.")
        self.client.validate_pve_permissions(self.api, "/access")
        realms = self.call_api("Listing test realms", self.api.get, "access/domains")
        if not self.validate_proxmox_resource_exist("realm", identifier, lambda: realms, resource_key="realm", fail_on_missing=False):
            return None
        data = self.call_api("Inspecting owned test realm", self.api.get, self._path())
        if not params["read_only"] and data.get("comment") != self.comment:
            self.client.fail_request("Refusing to modify a realm with an unrecognized ownership comment.")
        return {"realm": identifier, "comment": data.get("comment", "")}

    def create_resource(self):
        self.call_mutation("Creating disconnected test realm", self.api.post, "access/domains", **{
            "realm": self.module.params["resource_id"], "type": "ldap", "server1": "192.0.2.1", "base_dn": "dc=ansible,dc=invalid",
            "user_attr": "uid", "comment": self.comment, "check-connection": 0,
        })
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Created test realm could not be verified.")
        return result

    def update_resource(self, resource, updates):
        return resource

    def delete_resource(self, resource):
        self.call_mutation("Removing owned test realm", self.api.delete, self._path())
        if self.get_resource() is not None:
            self.client.fail_request("Owned test realm removal could not be verified.")
        return None

    def execute(self):
        if self.module.params["read_only"]:
            self.exit(changed=False, resource=self.get_resource())
        super().execute()


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "resource_id": {"type": "str", "required": True},
            "owned_ids": {"type": "list", "elements": "str", "required": True},
            "read_only": {"type": "bool", "default": False},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxTfaRealmFixtureModule(module).execute()


if __name__ == "__main__":
    main()
