#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_group
short_description: Manage Proxmox VE group configuration
description:
- Creates, reconciles and deletes Proxmox VE groups, including their membership.
- Membership changes update each affected user while retaining that user's unrelated groups.
- Writes are synchronous. O(task_timeout) does not affect these operations.
version_added: 1.0.0
options:
  groupid:
    description:
    - Group identifier using letters, digits, dots, underscores or hyphens.
    - The reserved URL path segments C(.) and C(..) are not supported.
    type: str
    required: true
  comment:
    description: Group comment. Omit to preserve; an empty string clears the comment.
    type: str
  members:
    description:
    - User identifiers in user@realm format. Each affected user must already exist.
    - Supply literal identifiers, without URL encoding. Characters such as C(%), C(#) and C(?) are encoded for API paths.
    - Omit to preserve membership. With O(membership_mode=exact), an empty list removes all members without deleting users.
    type: list
    elements: str
  membership_mode:
    description: How supplied members affect the existing membership. Ignored when members is omitted.
    type: str
    choices:
    - exact
    - present
    - absent
    default: exact
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
- Group CRUD requires native C(Group.Allocate) on C(/access/groups); membership writes additionally require C(User.Modify).
- No atomic group-membership endpoint or concurrency digest is available. Avoid simultaneous management of the same users' groups.
- With O(state=present), all affected users are checked before any write. Later failures retain changed=true for completed writes.
- With O(state=absent), O(comment), O(members) and O(membership_mode) are ignored after argument and identifier validation.
- Deleting a group removes its memberships and all ACL entries that reference it; it never deletes member user accounts.
"""

EXAMPLES = r"""
- name: Manage a group
  ansible.proxmox.proxmox_group:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    groupid: application
    members:
      - application@pve
- name: Remove a group
  ansible.proxmox.proxmox_group:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    groupid: application
    state: absent
"""

RETURN = r"""
group:
  description: Current or predicted group configuration, or null when absent.
  returned: always
  type: dict
  contains:
    groupid:
      description: Group identifier.
      type: str
      returned: when the resource exists
    comment:
      description: Group comment, or an empty string.
      type: str
      returned: when the resource exists
    members:
      description: Sorted realm-qualified user identifiers.
      type: list
      returned: when the resource exists
      elements: str
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_resource_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

GROUP_RESOURCE_PATH = "access/groups/{0}"


class ProxmoxGroupModule(ProxmoxResourceModule):
    """Reconcile groups and user-side membership through the shared lifecycle."""

    result_key = "group"

    def get_resource(self):
        params = self.module.params
        groupid = params["groupid"]
        if groupid in (".", ".."):
            self.module.fail_json(msg="groupid cannot be the reserved URL path segments . or ..")
        if re.fullmatch(r"[A-Za-z0-9._-]+", groupid) is None:
            self.module.fail_json(msg="groupid must contain only letters, digits, dots, underscores and hyphens.")
        for userid in params.get("members") or []:
            if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
                self.module.fail_json(msg="members must contain valid user@realm identifiers.")
        self.client.validate_pve_permissions(self.api, "/access")
        groups = self.call_api("Listing groups", self.api.get, "access/groups")
        if not self.validate_proxmox_resource_exist("group", groupid, lambda: groups, resource_key="groupid", fail_on_missing=False):
            return None
        data = self.call_api("Reading group {0}".format(groupid), self.api.get, GROUP_RESOURCE_PATH.format(groupid))
        return {"groupid": groupid, "comment": data.get("comment", ""), "members": sorted(set(data.get("members") or []))}

    def get_update_parameters(self, resource):
        updates = {}
        comment = self.module.params.get("comment")
        if comment is not None:
            updates = self.get_parameter_updates(resource, {"comment": comment}, {})
        requested = self.module.params.get("members")
        if requested is None:
            return updates
        current = set(resource["members"])
        desired = set(requested)
        mode = self.module.params["membership_mode"]
        if mode == "present":
            desired |= current
        elif mode == "absent":
            desired = current - desired
        users = self.call_api("Listing users for group membership", self.api.get, "access/users", full=1)
        by_id = {entry["userid"]: entry for entry in users}
        missing = (current | set(requested)) - set(by_id)
        if missing:
            self.client.fail_request("Users do not exist: {0}.".format(", ".join(sorted(missing))))
        user_updates = {}
        for userid in sorted(current ^ desired):
            groups = by_id[userid].get("groups") or []
            groups = set(groups.split(",") if isinstance(groups, str) else groups)
            if userid in desired:
                groups.add(resource["groupid"])
            else:
                groups.discard(resource["groupid"])
            user_updates[userid] = sorted(groups)
        if current != desired:
            updates.update(members=sorted(desired), users=user_updates)
        return updates

    def _apply_updates(self, updates):
        groupid = self.module.params["groupid"]
        if "comment" in updates:
            self.call_mutation("Updating group {0}".format(groupid), self.api.put, GROUP_RESOURCE_PATH.format(groupid), comment=updates["comment"])
        for userid, groups in updates.get("users", {}).items():
            self.call_mutation("Updating group membership for {0}".format(userid), self.api.put,
                               "access/users/{0}".format(quote(userid, safe="")), groups=",".join(groups))
        resource = self.get_resource()
        if resource is None:
            self.client.fail_request("Written group could not be read back.")
        if "members" in updates and resource["members"] != updates["members"]:
            self.client.fail_request("Group membership readback does not match the requested membership.")
        if "comment" in updates and resource["comment"] != updates["comment"]:
            self.client.fail_request("Group comment readback does not match the requested comment.")
        return resource

    def create_resource(self):
        groupid = self.module.params["groupid"]
        updates = self.get_update_parameters({"groupid": groupid, "comment": "", "members": []})
        self.call_mutation("Creating group {0}".format(groupid), self.api.post, "access/groups", groupid=groupid)
        return self._apply_updates(updates)

    def update_resource(self, resource, updates):
        return self._apply_updates(updates)

    def delete_resource(self, resource):
        groupid = resource["groupid"]
        self.call_mutation("Deleting group {0}".format(groupid), self.api.delete, GROUP_RESOURCE_PATH.format(groupid))
        if self.get_resource() is not None:
            self.client.fail_request("Group deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "delete":
            return None
        if operation == "create":
            resource = {"groupid": self.module.params["groupid"], "comment": "", "members": []}
            updates = self.get_update_parameters(resource)
        return self.apply_parameter_updates(resource, {key: value for key, value in updates.items() if key != "users"})


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "groupid": {"type": "str", "required": True},
            "comment": {"type": "str"}, "members": {"type": "list", "elements": "str"},
            "membership_mode": {"type": "str", "choices": ["exact", "present", "absent"], "default": "exact"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxGroupModule(module).execute()


if __name__ == "__main__":
    main()
