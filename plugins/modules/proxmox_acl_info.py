#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_acl_info
short_description: Retrieve configured Proxmox VE ACL bindings
description:
- Lists configured ACL role assignments, optionally filtering by exact path, subject type, subject or role.
- Filters combine with logical AND. Omit all filters to return all visible bindings.
- Returns configured entries, not calculated effective permissions or expanded inherited assignments.
version_added: 1.0.0
options:
  path:
    description:
    - ACL path, such as C(/vms), C(/pool/application) or C(/).
    - Repeated slashes and a trailing slash are normalized; a missing leading slash is added.
    type: str
  subject_type:
    description: Type of identity receiving the role.
    type: str
    choices: [user, group, token]
  subject:
    description:
    - Literal user@realm, group name or full user@realm!token identifier, according to O(subject_type).
    - A single identity is accepted. Lists and comma-separated identifiers are rejected.
    type: str
  role:
    description: Single role identifier, including built-in roles such as C(PVEAuditor) or C(NoAccess).
    type: str
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves ACL entries without changing state.
notes:
- Requires all privileges of C(Administrator) propagated on C(/access), including in check mode.
- Native ACL listing is restricted to entries visible through C(Sys.Audit) on C(/access) or permission-modification rights.
- Missing filter matches return an empty list. O(subject) and O(role) are exact scalar filters, not lists.
- O(subject) may be used without O(subject_type); matching entries of any subject type are then returned.
- Uses C(GET /access/acl) to return individual configured path, subject and role assignments.
  C(GET /access/permissions) instead returns effective privilege flags after evaluating roles, inheritance, groups and token restrictions.
  These APIs answer different questions and have different result models; this reader does not calculate effective access.
- Path filters accept syntactically valid obsolete path families so existing bindings can still be inspected.
"""

EXAMPLES = r"""
- name: Read configured bindings for one user
  ansible.proxmox.proxmox_acl_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    subject_type: user
    subject: application@pve

- name: Read one configured group binding by all filters
  ansible.proxmox.proxmox_acl_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    path: /pool/application
    subject_type: group
    subject: operators
    role: PVEAuditor

- name: Read bindings for a token without a subject type filter
  ansible.proxmox.proxmox_acl_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    subject: application@pve!automation
"""

RETURN = r"""
acls:
  description: Matching bindings, sorted by path, type, subject and role. Empty when no entries match.
  returned: always
  type: list
  elements: dict
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

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxAclInfoModule(ProxmoxInfoModule):
    """Read configured assignments independently of lifecycle reconciliation."""

    result_key = "acls"

    def get_info(self):
        params = self.module.params
        path = params.get("path")
        if path is not None:
            if not path or re.fullmatch(r"[A-Za-z0-9._/-]+", path) is None:
                self.module.fail_json(msg="path must be a nonempty ACL path containing letters, digits, dot, dash, underscore or slash.")
            path = "/" + re.sub(r"/+", "/", path).strip("/")
        for name in ("subject", "role"):
            value = params.get(name)
            if value is not None and (not value or re.search(r"[\s,;]", value)):
                self.module.fail_json(msg="{0} must be a nonempty scalar identifier without whitespace or list delimiters.".format(name))
        self.client.validate_pve_permissions(self.api, "/access")
        entries = self.call_api("Listing ACL bindings", self.api.get, "access/acl")
        filters = {"path": path, "type": params.get("subject_type"), "ugid": params.get("subject"), "roleid": params.get("role")}
        result = [dict(entry, propagate=bool(int(entry.get("propagate", 1)))) for entry in entries
                  if all(value is None or entry.get(name) == value for name, value in filters.items())]
        return sorted(result, key=lambda entry: (entry["path"], entry["type"], entry["ugid"], entry["roleid"]))


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "path": {"type": "str"},
            "subject_type": {"type": "str", "choices": ["user", "group", "token"]},
            "subject": {"type": "str"},
            "role": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxAclInfoModule(module).execute()


if __name__ == "__main__":
    main()
