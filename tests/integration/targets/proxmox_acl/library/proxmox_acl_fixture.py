#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_acl_fixture
short_description: Manage only owned integration token fixtures
description:
- Role-local token fixture support; the paired target uses public modules for group and user fixtures.
version_added: 1.0.0
options:
  resource_id:
    type: str
    required: true
    description: Owned token name associated with the same fixture identifier as the parent user.
  owned_ids:
    type: list
    elements: str
    required: true
    description: Identifiers preflighted by this target.
  userid:
    type: str
    required: true
    description: Owned disabled parent user with the target's ownership comment.
  read_only:
    type: bool
    default: false
    description: Inspect without writes.
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
  proxmox_acl_fixture:
    resource_id: ansible-acl-1000020000-token
    owned_ids: [ansible-acl-1000020000-token]
    userid: ansible-acl-1000020000@pve
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
    """Provision only a token below the target's verified owned user."""

    result_key = "fixture"

    def _endpoint(self):
        params = self.module.params
        identifier = params["resource_id"]
        if identifier not in params["owned_ids"] or re.fullmatch(r"ansible-acl-[0-9]{10}-token", identifier) is None:
            self.client.fail_request("Refusing a fixture outside the target's owned identifiers.")
        userid = params.get("userid") or ""
        if userid != identifier.removesuffix("-token") + "@pve":
            self.client.fail_request("Token parent must be the matching owned ACL fixture user.")
        self.client.validate_pve_permissions(self.api, "/access")
        users = self.call_api("Listing fixture users", self.api.get, "access/users")
        owner = next((user for user in users if user["userid"] == userid), None)
        if owner is None:
            if self.module.params.get("state", "present") == "present" and not params["read_only"]:
                self.client.fail_request("Create the owned user before its token.")
            return None
        if owner.get("comment") != "Owned ACL integration fixture" or not self.parameter_values_equal(owner.get("enable"), 0):
            self.client.fail_request("Refusing a token fixture whose parent ownership or disabled state cannot be verified.")
        return "access/users/{0}/token".format(quote(userid, safe=""))

    def get_resource(self):
        endpoint = self._endpoint()
        if endpoint is None:
            return None
        entries = self.call_api("Listing owned fixture metadata", self.api.get, endpoint)
        return next((dict(entry) for entry in entries if entry["tokenid"] == self.module.params["resource_id"]), None)

    def create_resource(self):
        endpoint = self._endpoint()
        if endpoint is None:
            return self.client.fail_request("Create the owned user before its token.")
        path = endpoint + "/" + quote(self.module.params["resource_id"], safe="")
        self.call_mutation("Creating owned fixture", self.api.post, path, privsep=1)
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Owned fixture creation could not be verified.")
        return result

    def update_resource(self, resource, updates):
        return resource

    def delete_resource(self, resource):
        endpoint = self._endpoint()
        if endpoint is None:
            return None
        self.call_mutation("Deleting owned fixture", self.api.delete, endpoint + "/" + quote(self.module.params["resource_id"], safe=""))
        if self.get_resource() is not None:
            self.client.fail_request("Owned fixture removal could not be verified.")
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
                "userid": {"type": "str", "required": True},
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
