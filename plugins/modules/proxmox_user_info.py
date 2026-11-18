#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_user_info
short_description: Retrieve Proxmox VE user information
description:
- Lists or retrieves normalized user attributes and group membership.
- Returns an empty list for a missing selected user. Passwords and TFA secrets are never returned.
version_added: 1.0.0
options:
  userid:
    description:
    - Optional user@realm identifier. Omit to list user summaries.
    - Supply the literal identifier; the module URL-encodes it when constructing request paths.
    type: str
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Reads state and validates inputs without changing configuration.
notes:
- Requires all privileges of the C(Administrator) role propagated on C(/access), including for reads and check mode.
- Normalizes groups to a sorted list, enable to a boolean and an omitted expiration to zero.
- Unset optional text attributes are returned as empty strings. Password, TFA and token data are excluded.
"""

EXAMPLES = r"""
- name: Read a user
  ansible.proxmox.proxmox_user_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
"""

RETURN = r"""
users:
  description: User records. Empty when the selected resource does not exist.
  returned: always
  type: list
  contains:
    userid:
      description: Realm-qualified user identifier.
      type: str
      returned: when the resource exists
    groups:
      description: Sorted group identifiers.
      type: list
      returned: when the resource exists
      elements: str
    enable:
      description: Whether the account is enabled.
      type: bool
      returned: when the resource exists
    expire:
      description: Expiration epoch, or zero for no expiration.
      type: int
      returned: when the resource exists
    email:
      description: Email address.
      type: str
      returned: when the resource exists
    firstname:
      description: First name.
      type: str
      returned: when the resource exists
    lastname:
      description: Last name.
      type: str
      returned: when the resource exists
    comment:
      description: User comment.
      type: str
      returned: when the resource exists
  elements: dict
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxUserInfoModule(ProxmoxInfoModule):
    """Retrieve user configuration independently of user mutations."""

    result_key = "users"

    def get_info(self):
        userid = self.module.params.get("userid")
        if userid is not None and re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use user@realm with a valid realm name and no whitespace, slash or colon.")
        self.client.validate_pve_permissions(self.api, "/access")
        users = self.call_api("Listing users", self.api.get, "access/users", full=1)
        if userid is not None:
            if not self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid", fail_on_missing=False):
                return []
            data = self.call_api("Reading user {0}".format(userid), self.api.get, "access/users/{0}".format(quote(userid, safe="")))
            users = [dict(data, userid=userid)]
        result = []
        for data in users:
            groups = data.get("groups") or []
            result.append({"userid": data["userid"], "groups": sorted(set(groups.split(",") if isinstance(groups, str) else groups)),
                           "enable": bool(int(data.get("enable", 1))), "expire": int(data.get("expire") or 0),
                           **{name: data.get(name, "") for name in ("email", "firstname", "lastname", "comment")}})
        return result


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"userid": {"type": "str"}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxUserInfoModule(module).execute()


if __name__ == "__main__":
    main()
