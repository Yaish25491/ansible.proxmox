#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_role
short_description: Manage Proxmox VE role configuration
description:
- Creates, reconciles and deletes custom roles using privilege sets.
- Refreshes permissions and state after writes. No task is started, so O(task_timeout) has no effect.
version_added: 1.0.0
options:
  roleid:
    description:
    - Role identifier. Letters, digits, dots, underscores and hyphens are accepted.
    - The reserved URL path segments C(.) and C(..) are not supported.
    type: str
    required: true
  privileges:
    description:
    - Authoritative privilege set. Ordering and duplicates are ignored.
    - Omit to preserve existing privileges; an empty list removes all privileges from a custom role.
    - Ignored when O(state=absent).
    type: list
    elements: str
  state:
    description: Whether the resource should exist.
    type: str
    choices:
    - present
    - absent
    default: present
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads state and validates inputs without changing configuration.
notes:
- Requires all privileges of the C(Administrator) role propagated on C(/access), including for reads and check mode.
- Uses synchronous GET/POST/PUT/DELETE operations under C(/access/roles); O(api_timeout) bounds each request.
- Built-in roles can be read and matched but cannot be changed or deleted. New names must not start with PVE, case-insensitively.
- The native write API requires C(Sys.Modify) on C(/access); the collection enforces the stronger Administrator policy.
- Defining a role does not grant it to an identity. ACL assignments are outside this module.
- Remove ACL assignments that reference a role before deleting it. Proxmox does not remove these references automatically.
"""

EXAMPLES = r"""
- name: Manage a role
  ansible.proxmox.proxmox_role:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    roleid: ApplicationReader
    privileges:
      - VM.Audit
- name: Remove a role
  ansible.proxmox.proxmox_role:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    roleid: ApplicationReader
    state: absent
"""

RETURN = r"""
role:
  description: Current or predicted role configuration, or null when absent.
  returned: always
  type: dict
  contains:
    roleid:
      description: Role identifier.
      type: str
      returned: when the resource exists
    privileges:
      description: Sorted privilege set.
      type: list
      returned: when the resource exists
      elements: str
    special:
      description: Whether the role is a built-in role.
      type: bool
      returned: when the resource exists
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_resource_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

ROLE_RESOURCE_PATH = "access/roles/{0}"


class ProxmoxRoleModule(ProxmoxResourceModule):
    """Reconcile custom role definitions through the shared lifecycle."""

    result_key = "role"

    def get_resource(self):
        roleid = self.module.params["roleid"]
        if roleid in (".", ".."):
            self.module.fail_json(msg="roleid cannot be the reserved URL path segments . or ..")
        if re.fullmatch(r"[A-Za-z0-9._-]+", roleid) is None:
            self.module.fail_json(msg="roleid must contain only letters, digits, dots, underscores and hyphens.")
        self.client.validate_pve_permissions(self.api, "/access")
        roles = self.call_api("Listing roles", self.api.get, "access/roles")
        requested = self.module.params.get("privileges")
        if self.module.params["state"] == "present" and requested is not None:
            administrator = next((entry for entry in roles if entry["roleid"] == "Administrator"), None)
            if administrator is None:
                self.client.fail_request("Cannot validate privileges without the Administrator role definition.")
            known = set(administrator.get("privs", "").split(","))
            unknown = set(requested) - known
            if unknown:
                self.module.fail_json(msg="Unknown privileges: {0}.".format(", ".join(sorted(unknown))))
        summary = next((entry for entry in roles if entry["roleid"] == roleid), None)
        if summary is None:
            if self.module.params["state"] == "present" and roleid.lower().startswith("pve"):
                self.module.fail_json(msg="New role names must not start with PVE.")
            return None
        data = self.call_api("Reading role {0}".format(roleid), self.api.get, ROLE_RESOURCE_PATH.format(roleid))
        resource = {"roleid": roleid, "privileges": sorted(data), "special": bool(summary.get("special", 0))}
        if resource["special"] and self.module.params["state"] == "absent":
            self.module.fail_json(msg="Built-in roles cannot be deleted.")
        return resource

    def get_update_parameters(self, resource):
        requested = self.module.params.get("privileges")
        if requested is None:
            return {}
        updates = self.get_parameter_updates(resource, {"privileges": sorted(set(requested))}, {})
        if updates and resource["special"]:
            self.module.fail_json(msg="Built-in role privileges cannot be changed.")
        return updates

    def create_resource(self):
        roleid = self.module.params["roleid"]
        self.call_mutation("Creating role {0}".format(roleid), self.api.post, "access/roles", roleid=roleid,
                           privs=",".join(sorted(set(self.module.params.get("privileges") or []))))
        resource = self.get_resource()
        if resource is None:
            self.client.fail_request("Created role could not be read back.")
        if self.get_update_parameters(resource):
            self.client.fail_request("Role privilege readback does not match the requested values.")
        return resource

    def update_resource(self, resource, updates):
        self.call_mutation("Updating role {0}".format(resource["roleid"]), self.api.put,
                           ROLE_RESOURCE_PATH.format(resource["roleid"]), privs=",".join(updates["privileges"]))
        resource = self.get_resource()
        if resource is None:
            self.client.fail_request("Updated role could not be read back.")
        if self.get_update_parameters(resource):
            self.client.fail_request("Role privilege readback does not match the requested values.")
        return resource

    def delete_resource(self, resource):
        self.call_mutation("Deleting role {0}".format(resource["roleid"]), self.api.delete, ROLE_RESOURCE_PATH.format(resource["roleid"]))
        if self.get_resource() is not None:
            self.client.fail_request("Role deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            return {"roleid": self.module.params["roleid"], "privileges": sorted(set(self.module.params.get("privileges") or [])), "special": False}
        return super().get_check_mode_resource(operation, resource, updates)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "roleid": {"type": "str", "required": True},
            "privileges": {"type": "list", "elements": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxRoleModule(module).execute()


if __name__ == "__main__":
    main()
