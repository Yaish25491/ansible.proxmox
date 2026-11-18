#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_token_info
short_description: Retrieve Proxmox VE token information
description:
- Lists API token metadata for an owner or retrieves one token.
- Returns an empty list when the owner or selected token is absent. Token secrets are never returned.
version_added: 1.0.0
options:
  userid:
    description: Owner identifier in user@realm format.
    type: str
    required: true
  tokenid:
    description: Optional user-specific token identifier. Omit to list metadata for this owner.
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
- An expiration of zero means no token-specific expiration; owner expiration or other server policy still applies.
- This module cannot recover a token secret or infer effective ACL permissions from metadata.
"""

EXAMPLES = r"""
- name: Read a token
  ansible.proxmox.proxmox_token_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
    tokenid: automation
"""

RETURN = r"""
tokens:
  description: Token records. Empty when the selected resource does not exist.
  returned: always
  type: list
  contains:
    userid:
      description: Owning user identifier.
      type: str
      returned: when the resource exists
    tokenid:
      description: User-specific token identifier.
      type: str
      returned: when the resource exists
    full_tokenid:
      description: Full user@realm!token identifier.
      type: str
      returned: when the resource exists
    privsep:
      description: Whether separate token ACLs apply.
      type: bool
      returned: when the resource exists
    expire:
      description: Configured expiration epoch, or zero when no token-specific expiration is set.
      type: int
      returned: when the resource exists
    comment:
      description: Token comment, or an empty string.
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


class ProxmoxTokenInfoModule(ProxmoxInfoModule):
    """Read token metadata without exposing or regenerating secrets."""

    result_key = "tokens"

    def get_info(self):
        userid, tokenid = self.module.params["userid"], self.module.params.get("tokenid")
        if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use a valid user@realm identifier.")
        if tokenid is not None and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]+", tokenid) is None:
            self.module.fail_json(msg="tokenid must start with a letter and contain at least two valid characters.")
        self.client.validate_pve_permissions(self.api, "/access")
        users = self.call_api("Listing token owners", self.api.get, "access/users")
        if not self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid", fail_on_missing=False):
            return []
        path = "access/users/{0}/token".format(quote(userid, safe=""))
        tokens = self.call_api("Listing tokens", self.api.get, path)
        if tokenid is not None:
            if not self.validate_proxmox_resource_exist("token", tokenid, lambda: tokens, resource_key="tokenid", fail_on_missing=False):
                return []
            data = self.call_api("Reading token {0}".format(tokenid), self.api.get, "{0}/{1}".format(path, tokenid))
            tokens = [dict(data, tokenid=tokenid)]
        return [{"userid": userid, "tokenid": entry["tokenid"], "full_tokenid": "{0}!{1}".format(userid, entry["tokenid"]),
                 "privsep": bool(int(entry.get("privsep", 1))), "expire": int(entry.get("expire") or 0), "comment": entry.get("comment", "")}
                for entry in tokens]


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"userid": {"type": "str", "required": True}, "tokenid": {"type": "str", "no_log": False}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxTokenInfoModule(module).execute()


if __name__ == "__main__":
    main()
