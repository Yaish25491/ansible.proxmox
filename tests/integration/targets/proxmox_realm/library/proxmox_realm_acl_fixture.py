#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_realm_acl_fixture
short_description: Manage an owned realm sync test ACL
description:
- Role-local fixture until the public ACL module is available on the target branch.
- Only a nonpropagating NoAccess user binding at /vms can be read or changed.
version_added: 1.0.0
options:
  userid:
    description: Owned, realm-qualified fixture user.
    type: str
    required: true
  owned_users:
    description: Users preflighted and claimed by this target invocation.
    type: list
    elements: str
    required: true
  read_only:
    description: Inspect the binding without mutation.
    type: bool
    default: false
  state:
    description: Whether this one fixture binding should exist.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
"""

EXAMPLES = r"""
- name: Read an owned ACL fixture
  proxmox_realm_acl_fixture:
    userid: '{{ realm_sync_vanished_user }}'
    owned_users: '{{ realm_sync_owned_users }}'
    read_only: true
"""

RETURN = r"""
binding:
  description: The exact fixture binding, or null when absent.
  type: dict
  returned: always
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxRealmAclFixture(ProxmoxResourceModule):
    """Restrict fixture API operations to a single owned deny binding."""

    result_key = "binding"

    def get_resource(self):
        userid = self.module.params["userid"]
        if userid not in self.module.params["owned_users"] or re.fullmatch(r"ansible-[A-Za-z0-9._-]+@ansible-realm-[A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="Refusing an ACL outside this invocation's owned realm users.")
        self.client.validate_pve_permissions(self.api, "/access")
        entries = self.call_api("Reading realm fixture ACL", self.api.get, "access/acl")
        realms = {user.rsplit("@", 1)[-1] for user in self.module.params["owned_users"]}
        for entry in entries:
            subject = entry.get("ugid", "")
            related = any(subject.endswith("@" + realm) or "@" + realm + "!" in subject
                          or (entry.get("type") == "group" and subject.endswith("-" + realm)) for realm in realms)
            expected = (entry.get("type") == "user" and subject in self.module.params["owned_users"]
                        and entry.get("path") == "/vms" and entry.get("roleid") == "NoAccess" and int(entry.get("propagate", 1)) == 0)
            if related and not expected:
                self.client.fail_request("Unexpected ACL in a proposed fixture realm; refusing ownership or mutation.")
        return next((entry for entry in entries if all(entry.get(key) == value for key, value in {
            "path": "/vms", "type": "user", "ugid": userid, "roleid": "NoAccess",
        }.items())), None)

    def create_resource(self):
        self.call_mutation("Creating realm fixture ACL", self.api.put, "access/acl", path="/vms",
                           users=self.module.params["userid"], roles="NoAccess", propagate=0)
        resource = self.get_resource()
        if resource is None or int(resource.get("propagate", 1)) != 0:
            self.client.fail_request("Owned nonpropagating deny binding could not be verified.")
        return resource

    def update_resource(self, resource, updates):
        return resource

    def delete_resource(self, resource):
        self.call_mutation("Removing realm fixture ACL", self.api.put, "access/acl", path="/vms",
                           users=self.module.params["userid"], roles="NoAccess", delete=1)
        if self.get_resource() is not None:
            self.client.fail_request("Owned ACL removal could not be verified.")
        return None

    def execute(self):
        if self.module.params["read_only"]:
            self.exit(changed=False, resource=self.get_resource())
        super().execute()


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "userid": {"type": "str", "required": True},
            "owned_users": {"type": "list", "elements": "str", "required": True},
            "read_only": {"type": "bool", "default": False},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxRealmAclFixture(module).execute()


if __name__ == "__main__":
    main()
