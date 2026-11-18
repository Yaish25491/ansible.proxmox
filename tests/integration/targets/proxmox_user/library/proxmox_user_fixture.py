#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_user_fixture
short_description: Manage only owned identity test fixtures
description:
- Role-local fixture helper; not a public collection module.
version_added: 1.0.0
options:
  resource_id:
    type: str
    required: true
    description: Owned fixture identifier.
  owned_ids:
    type: list
    elements: str
    required: true
    description: Identifiers claimed by this test invocation.
  read_only:
    type: bool
    default: false
    description: Read without mutation.
  groups:
    type: list
    elements: str
    description: Initial groups for an owned disabled fixture user.
  state:
    type: str
    choices:
    - present
    - absent
    default: present
    description: Whether the fixture should exist.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
"""

EXAMPLES = r"""
- name: Read only an owned fixture
  proxmox_user_fixture:
    resource_id: "{{ fixture_id }}"
    owned_ids: "{{ fixture_ids }}"
    read_only: true
"""

RETURN = r"""
fixture:
  description: Fixture configuration or null when absent.
  returned: always
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule
import re
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_resource_argument_spec, proxmox_mutually_exclusive, proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxFixtureModule(ProxmoxResourceModule):
    """Create only disabled, passwordless test users or test groups."""

    result_key = "fixture"

    def get_resource(self):
        identity = self.module.params["resource_id"]
        if identity not in self.module.params["owned_ids"] or not identity.startswith("ansible-"):
            self.module.fail_json(msg="Refusing an identifier outside this test's owned fixture list.")
        pattern = r"ansible-[A-Za-z0-9._-]+"
        if re.fullmatch(pattern, identity) is None:
            self.module.fail_json(msg="Invalid owned fixture identifier.")
        self.client.validate_pve_permissions(self.api, "/access")
        entries = self.call_api("Listing fixture identities", self.api.get, "access/groups")
        if not self.validate_proxmox_resource_exist("fixture", identity, lambda: entries, resource_key="groupid", fail_on_missing=False):
            return None
        data = self.call_api("Reading fixture identity", self.api.get, "access/groups/{0}".format(identity))
        return {"groupid": identity, **{key: value for key, value in data.items() if key in ("groups", "members", "enable", "comment")}}

    def create_resource(self):
        parameters = {"groupid": self.module.params["resource_id"]}
        self.call_mutation("Creating owned fixture", self.api.post, "access/groups", **parameters)
        resource = self.get_resource()
        if resource is None:
            self.client.fail_request("Created fixture could not be verified.")
        return resource

    def update_resource(self, resource, updates):
        return resource

    def delete_resource(self, resource):
        self.call_mutation("Deleting owned fixture", self.api.delete, "access/groups/{0}".format(resource["groupid"]))
        if self.get_resource() is not None:
            self.client.fail_request("Fixture removal could not be verified.")
        return None

    def execute(self):
        if self.module.params["read_only"]:
            self.exit(changed=False, resource=self.get_resource())
        super().execute()


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "resource_id": {"type": "str", "required": True}, "owned_ids": {"type": "list", "elements": "str", "required": True},
            "groups": {"type": "list", "elements": "str"}, "read_only": {"type": "bool", "default": False},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(), mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFixtureModule(module).execute()


if __name__ == "__main__":
    main()
