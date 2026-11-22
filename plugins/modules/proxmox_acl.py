#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_acl
short_description: Manage individual Proxmox VE ACL role bindings
description:
- Creates, reconciles or removes one path, subject type, subject and role binding.
- Preserves every other ACL entry and role assignment, including inherited assignments.
- Changes configured ACL entries, not an authoritative replacement of the ACL tree or effective privileges.
version_added: 1.0.0
options:
  path:
    required: true
    description:
    - ACL path, such as C(/vms), C(/pool/application) or C(/).
    - Repeated slashes and a trailing slash are normalized; a missing leading slash is added.
    - With O(state=present), validates PVE 9 ACL path families before any API call in both modes.
      Path validity does not require the referenced guest, storage, node or other object to exist.
    - With O(state=absent), accepts any syntactically valid path so obsolete bindings can be removed.
    type: str
  subject_type:
    required: true
    description: Type of identity receiving the role.
    type: str
    choices: [user, group, token]
  subject:
    required: true
    description:
    - Literal user@realm, group name or full user@realm!token identifier, according to O(subject_type).
    - A single identity is accepted. Lists and comma-separated identifiers are rejected.
    type: str
  role:
    required: true
    description: Single role identifier, including built-in roles such as C(PVEAuditor) or C(NoAccess).
    type: str
  propagate:
    description:
    - Whether this binding applies to child paths.
    - Omit to preserve an existing binding; new bindings default to true.
    - Ignored with O(state=absent).
    type: bool
  state:
    description: Whether the selected binding should exist.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Validates existing subjects and roles and predicts changes without writing.
notes:
- O(path), O(subject_type), O(subject) and O(role) are required.
- Requires all privileges of C(Administrator) propagated on C(/access) and O(path), including in check mode.
- The native API requires permission-modification rights on the selected path; C(NoAccess) requires C(Permissions.Modify).
- Supply literal identifiers, including percent signs, number signs and question marks; do not URL-encode them.
- Subjects and roles must already exist. Group, role and token provisioning is outside this module.
- A missing binding is already absent, even when its subject or role no longer exists.
- Remove ACL bindings before deleting their subjects or roles. Native PUT with C(delete=1) still validates each subject and role;
  skipping client validation would not allow a stale binding with missing dependencies to be removed by the API.
- Known PVE 9 ACL path families are validated locally for creation and updates; the server remains the final authority
  for version-specific changes. Reads and deletion retain access to obsolete path families.
- ACL operations use synchronous PUT requests, including removal. O(task_timeout) has no effect.
- Configured bindings are returned by M(ansible.proxmox.proxmox_acl_info) using C(GET /access/acl).
  Effective privileges use C(GET /access/permissions), which evaluates roles, inheritance, groups and token restrictions;
  its privilege mapping is a different result from individual configured role bindings.
"""

EXAMPLES = r"""
- name: Grant read-only access to one user
  ansible.proxmox.proxmox_acl:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    path: /vms
    subject_type: user
    subject: application@pve
    role: PVEAuditor
    propagate: true

- name: Remove only the selected role binding
  ansible.proxmox.proxmox_acl:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    path: /vms
    subject_type: user
    subject: application@pve
    role: PVEAuditor
    state: absent

- name: Grant access to an existing group
  ansible.proxmox.proxmox_acl:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    path: /pool/application
    subject_type: group
    subject: operators
    role: PVEAuditor
    propagate: false

- name: Grant access to an existing API token
  ansible.proxmox.proxmox_acl:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    path: /vms
    subject_type: token
    subject: application@pve!automation
    role: PVEAuditor
"""

RETURN = r"""
acl:
  description: Current or predicted binding, or null when absent.
  returned: always
  type: dict
  contains:
    path:
      description: Normalized ACL path.
      type: str
      returned: when the binding exists
    type:
      description: Subject type.
      type: str
      returned: when the binding exists
    ugid:
      description: User, group or full API-token identifier.
      type: str
      returned: when the binding exists
    roleid:
      description: Assigned role identifier.
      type: str
      returned: when the binding exists
    propagate:
      description: Whether the assignment propagates to child paths.
      type: bool
      returned: when the binding exists
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    is_pve_acl_path,
    proxmox_resource_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

ACL_RESOURCE_PATH = "access/acl"


class ProxmoxAclModule(ProxmoxResourceModule):
    """Reconcile one configured binding without replacing neighboring entries."""

    result_key = "acl"

    def _validate_parameters(self):
        params = self.module.params
        path = re.sub(r"/+", "/", params["path"])
        if not path or re.fullmatch(r"[A-Za-z0-9._/-]+", path) is None:
            self.module.fail_json(msg="path must be a nonempty ACL path containing letters, digits, dot, dash, underscore or slash.")
        self.path = "/" + path.strip("/")
        if params["state"] == "present" and not is_pve_acl_path(self.path):
            self.module.fail_json(msg="{0} is not a supported Proxmox VE ACL path.".format(self.path))
        subject = params["subject"]
        patterns = {
            "user": r"[^\s:/,;]+@[A-Za-z][A-Za-z0-9._-]+",
            "group": r"[A-Za-z0-9._-]+",
            "token": r"[^\s:/,;]+@[A-Za-z][A-Za-z0-9._-]+![A-Za-z][A-Za-z0-9._-]+",
        }
        if re.fullmatch(patterns[params["subject_type"]], subject) is None:
            self.module.fail_json(msg="subject must be a single valid identifier matching subject_type.")
        if re.fullmatch(r"[A-Za-z0-9._-]+", params["role"]) is None:
            self.module.fail_json(msg="role must be a single role identifier containing letters, digits, dot, dash or underscore.")

    def _validate_dependencies(self):
        params = self.module.params
        roles = self.call_api("Listing ACL roles", self.api.get, "access/roles")
        self.validate_proxmox_resource_exist("role", params["role"], lambda: roles, resource_key="roleid")
        subject_type = params["subject_type"]
        if subject_type == "group":
            subjects = self.call_api("Listing ACL groups", self.api.get, "access/groups")
            self.validate_proxmox_resource_exist("group", params["subject"], lambda: subjects, resource_key="groupid")
            return
        userid = params["subject"].rpartition("!")[0] if subject_type == "token" else params["subject"]
        users = self.call_api("Listing ACL users", self.api.get, "access/users")
        self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid")
        if subject_type == "token":
            tokens = self.call_api("Listing ACL user tokens", self.api.get, "access/users/{0}/token".format(quote(userid, safe="")))
            token_name = params["subject"].rpartition("!")[2]
            self.validate_proxmox_resource_exist("token", token_name, lambda: tokens, resource_key="tokenid")

    def get_resource(self):
        self._validate_parameters()
        self.client.validate_pve_permissions(self.api, "/access")
        if self.path != "/access":
            self.client.validate_pve_permissions(self.api, self.path)
        entries = self.call_api("Listing ACL bindings", self.api.get, ACL_RESOURCE_PATH)
        params = self.module.params
        current = next((entry for entry in entries if entry["path"] == self.path and entry["type"] == params["subject_type"]
                        and entry["ugid"] == params["subject"] and entry["roleid"] == params["role"]), None)
        if params["state"] == "present" or current is not None:
            self._validate_dependencies()
        if current is None:
            return None
        return dict(current, propagate=bool(int(current.get("propagate", 1))))

    def get_update_parameters(self, resource):
        propagate = self.module.params.get("propagate")
        return self.get_parameter_updates(resource, {"propagate": propagate} if propagate is not None else {}, {})

    def _write_binding(self, deleting=False, propagate=None):
        params = self.module.params
        values = {"path": self.path, "roles": params["role"], params["subject_type"] + "s": params["subject"]}
        if deleting:
            values["delete"] = 1
        elif propagate is not None:
            values["propagate"] = int(propagate)
        self.call_mutation("Removing ACL binding" if deleting else "Setting ACL binding", self.api.put, ACL_RESOURCE_PATH, **values)

    def _verified_binding(self, expected_propagate):
        result = self.get_resource()
        if result is None or result["propagate"] != expected_propagate:
            self.client.fail_request("ACL readback does not match the requested binding.")
        return result

    def create_resource(self):
        propagate = self.module.params.get("propagate")
        self._write_binding(propagate=True if propagate is None else propagate)
        return self._verified_binding(True if propagate is None else propagate)

    def update_resource(self, resource, updates):
        self._write_binding(propagate=updates["propagate"])
        return self._verified_binding(updates["propagate"])

    def delete_resource(self, resource):
        self._write_binding(deleting=True)
        if self.get_resource() is not None:
            self.client.fail_request("ACL binding deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            params = self.module.params
            propagate = params.get("propagate")
            return {"path": self.path, "type": params["subject_type"], "ugid": params["subject"], "roleid": params["role"],
                    "propagate": True if propagate is None else propagate}
        return super().get_check_mode_resource(operation, resource, updates)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "path": {"type": "str", "required": True},
            "subject_type": {"type": "str", "choices": ["user", "group", "token"], "required": True},
            "subject": {"type": "str", "required": True},
            "role": {"type": "str", "required": True},
            "propagate": {"type": "bool"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxAclModule(module).execute()


if __name__ == "__main__":
    main()
