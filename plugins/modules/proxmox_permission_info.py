#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_permission_info
short_description: Retrieve effective Proxmox VE permissions
description:
- Queries effective privileges, including inherited permissions, for a user or API token.
- Returns the API mapping of ACL paths to privileges and their propagation flags. This is not a configured ACL entry listing.
version_added: 1.0.0
options:
  userid:
    type: str
    description:
    - Subject user@realm or full user@realm!token identifier. Supply the literal identifier without URL encoding.
    - Omit to inspect the authenticated identity; this option does not change the connection identity.
  path:
    type: str
    description:
    - Optional absolute ACL path. Omit to query the complete effective permission tree.
    - Sent as a query parameter; supply the literal path without URL encoding. The returned path key is preserved as provided by the API.
    - Must belong to a supported PVE 9 ACL path family. Unsupported paths fail before API access in normal and check mode.
    - ACL path validity does not prove that the referenced object exists; inherited privileges can apply before an object is created.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves current information without mutations.
notes:
- Requires all privileges of the C(Administrator) role propagated on C(/access), even though the native API allows self-queries and
  a user's own token queries without that role.
- The native API requires C(Sys.Audit) on C(/access) for other subject queries. Token privilege separation affects effective permissions.
- Server permission and invalid-subject errors are reported. Supports Proxmox VE 9.
- A syntactically valid subject with no effective privileges can return an empty mapping for the full tree,
  or the selected path mapped to an empty mapping. Both native shapes are preserved.
- Full-tree queries enumerate native standard roots and configured ACL/pool paths, not every possible descendant path.
  Supply O(path) to inspect an otherwise unlisted path.
- Invalid response paths, privilege mappings or propagation flags fail explicitly instead of being reported as effective access.
- Configured role assignments are managed by C(proxmox_acl) and listed by C(proxmox_acl_info).
  This read-only module reports their calculated effect; effective privilege maps cannot be assigned directly.
- A privilege key indicates the privilege is granted. Its value is the propagation flag; C(0) means granted without propagation,
  not denied. Privilege-separated tokens receive the intersection of their own and their owner's privileges.
"""

EXAMPLES = r"""
- name: Retrieve effective Proxmox VE permissions
  ansible.proxmox.proxmox_permission_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    userid: application@pve
    path: /vms/100

- name: Retrieve the authenticated identity's full effective permission tree
  ansible.proxmox.proxmox_permission_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"

- name: Retrieve a full API token subject independently of the connection identity
  ansible.proxmox.proxmox_permission_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    userid: application@pve!deployment
    path: /access
"""

RETURN = r"""
permissions:
  description:
  - Mapping of ACL paths to privilege names and propagation flags, as returned by the API.
  - A present privilege with value C(0) is granted without propagation; C(1) indicates propagation.
  - Empty permission trees and selected paths with empty privilege mappings are preserved.
  returned: always
  type: dict
  sample:
    /vms/100:
      VM.Audit: 1
      VM.Console: 0
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    is_pve_acl_path,
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxPermissionInfoModule(ProxmoxInfoModule):
    """Read effective privileges for an explicitly selected or authenticated subject."""

    result_key = "permissions"

    @staticmethod
    def _valid_permissions(permissions, requested_path):
        if not isinstance(permissions, dict):
            return False
        if requested_path is not None and set(permissions) - {requested_path}:
            return False
        for path, privileges in permissions.items():
            if not (is_pve_acl_path(path) and path.startswith("/")) or not isinstance(privileges, dict):
                return False
            if any(not isinstance(name, str) or not name or re.search(r"\s", name)
                   or not isinstance(flag, int) or flag not in (0, 1) for name, flag in privileges.items()):
                return False
        return True

    def get_info(self):
        userid = self.module.params.get("userid")
        if userid is not None and re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+(?:![A-Za-z][A-Za-z0-9._-]+)?", userid) is None:
            self.module.fail_json(msg="userid must be a realm-qualified user or full user@realm!token identifier.")
        path = self.module.params.get("path")
        if path is not None and not (is_pve_acl_path(path) and path.startswith("/")):
            self.module.fail_json(msg="path must be a supported absolute Proxmox VE ACL path.")
        self.client.validate_pve_permissions(self.api, "/access")
        parameters = {name: self.module.params[name] for name in ("userid", "path") if self.module.params.get(name) is not None}
        permissions = self.call_api("Retrieving effective permissions", self.api.get, "access/permissions", **parameters)
        if not self._valid_permissions(permissions, path):
            self.client.fail_request("The effective permissions API returned an invalid path, privilege mapping or propagation flag.")
        return permissions


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "userid": {"type": "str"},
                "path": {"type": "str"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxPermissionInfoModule(module).execute()


if __name__ == "__main__":
    main()
