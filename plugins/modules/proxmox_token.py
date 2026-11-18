#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_token
short_description: Manage Proxmox VE token configuration
description:
- Creates, reconciles metadata or deletes API tokens for an existing Proxmox VE user.
- A token secret is returned only on verified creation. It cannot be read again.
- Writes are synchronous; O(task_timeout) has no effect.
version_added: 1.0.0
options:
  userid:
    description: Existing owner in user@realm format.
    type: str
    required: true
  tokenid:
    description: User-specific identifier beginning with a letter followed by letters, digits, dots, underscores or hyphens.
    type: str
    required: true
  privsep:
    description: Whether separate token ACLs restrict permissions. Omit to preserve existing state; creation defaults to true.
    type: bool
  expire:
    description: Expiration epoch. Omit to preserve existing metadata or allow server creation defaults; zero means no token-specific expiration.
    type: int
  comment:
    description:
    - Token comment. Omit to preserve existing metadata. An empty string clears the comment on servers that support it.
    - The literal string C(0) cannot survive Proxmox configuration serialization and is rejected before reads or writes,
      for both creation and updates. The update handler can accept it but persistence drops it, clearing an existing comment.
    - Older servers, including pve-access-control 9.0.3, ignore an empty string on update;
      a real update then fails readback verification rather than reporting success. Check mode predicts the requested value.
    type: str
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
- The native API allows owner self-service or C(User.Modify) on applicable groups; the collection enforces Administrator on C(/access).
- A privilege-separated token needs its own ACLs to be useful. This module does not create ACLs, create owners or rotate secrets.
- The authenticated token cannot delete itself. A missing owner is an error for state=present and an absent token for state=absent.
- Set no_log=true on tasks that create tokens and on every task that persists or consumes token_secret to prevent disclosure in controller output.
- Creation verifies metadata after writing. A failed request or readback may leave a created token whose secret is unavailable; inspect it before
  explicit removal/recreation.
- User identifiers are treated literally and URL-encoded only when used in API paths.
- Check mode never creates or invents a secret. Omitted creation expiration is not predicted because server policy can supply a default.
"""

EXAMPLES = r"""
- name: Manage a token
  ansible.proxmox.proxmox_token:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
    tokenid: automation
    privsep: true
    expire: 0
    comment: Application automation
  no_log: true
  register: created_token
- name: Remove a token
  ansible.proxmox.proxmox_token:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
    tokenid: automation
    state: absent
"""

RETURN = r"""
token:
  description: Current or predicted token configuration, or null when absent.
  returned: always
  type: dict
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
token_secret:
  description: One-time token secret. Store securely; not returned on updates, no-ops or in check mode.
  returned: when a token is created and verified
  type: str
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

FULL_TOKEN_ID_TEMPLATE = "{0}!{1}"
TOKEN_ENTRY_PATH = "access/users/{0}/token/{1}"


class ProxmoxTokenModule(ProxmoxResourceModule):
    """Reconcile token metadata while preserving the one-time secret contract."""

    result_key = "token"

    def __init__(self, module, client=None):
        super().__init__(module, client)
        self.created_secret = None

    def get_resource(self):
        params = self.module.params
        userid, tokenid = params["userid"], params["tokenid"]
        if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use a valid user@realm identifier.")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]+", tokenid) is None:
            self.module.fail_json(msg="tokenid must start with a letter and contain at least two valid characters.")
        if params.get("expire") is not None and params["expire"] < 0:
            self.module.fail_json(msg="expire must be zero or greater.")
        if params["state"] == "present" and params.get("comment") == "0":
            self.module.fail_json(msg="Proxmox cannot persist comment=0; use another value to avoid clearing the comment.")
        auth_token = params.get("api_token_id")
        if auth_token and "!" not in auth_token:
            auth_token = FULL_TOKEN_ID_TEMPLATE.format(params.get("api_user"), auth_token)
        if params["state"] == "absent" and auth_token == FULL_TOKEN_ID_TEMPLATE.format(userid, tokenid):
            self.module.fail_json(msg="The authenticated token cannot delete itself.")
        self.client.validate_pve_permissions(self.api, "/access")
        users = self.call_api("Listing token owners", self.api.get, "access/users")
        if not self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid", fail_on_missing=False):
            if params["state"] == "present":
                self.client.fail_request("Token owner {0} does not exist.".format(userid))
            return None
        path = "access/users/{0}/token".format(quote(userid, safe=""))
        tokens = self.call_api("Listing tokens for {0}".format(userid), self.api.get, path)
        if not self.validate_proxmox_resource_exist("token", tokenid, lambda: tokens, resource_key="tokenid", fail_on_missing=False):
            return None
        data = self.call_api("Reading token {0}".format(tokenid), self.api.get, "{0}/{1}".format(path, tokenid))
        return {"userid": userid, "tokenid": tokenid, "full_tokenid": FULL_TOKEN_ID_TEMPLATE.format(userid, tokenid),
                "privsep": bool(int(data.get("privsep", 1))), "expire": int(data.get("expire") or 0), "comment": data.get("comment", "")}

    def get_update_parameters(self, resource):
        desired = {key: self.module.params[key] for key in ("privsep", "expire", "comment") if self.module.params.get(key) is not None}
        return self.get_parameter_updates(resource, desired, {})

    @staticmethod
    def _api_parameters(values):
        parameters = dict(values)
        if "privsep" in parameters:
            parameters["privsep"] = int(parameters["privsep"])
        return parameters

    def create_resource(self):
        path = TOKEN_ENTRY_PATH.format(quote(self.module.params["userid"], safe=""), self.module.params["tokenid"])
        response = self.call_mutation("Creating API token", self.api.post, path, **self._api_parameters(self.get_update_parameters({})))
        if not isinstance(response, dict) or not response.get("value"):
            self.client.fail_request("Token creation completed without a usable one-time secret; inspect the token before retrying.")
        self.created_secret = response["value"]
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Created token could not be read back; inspect the token before retrying.")
        if self.get_update_parameters(result):
            self.client.fail_request("Created token metadata does not match the requested values; inspect the token before retrying.")
        return result

    def update_resource(self, resource, updates):
        path = TOKEN_ENTRY_PATH.format(quote(resource["userid"], safe=""), resource["tokenid"])
        self.call_mutation("Updating API token metadata", self.api.put, path, **self._api_parameters(updates))
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Updated token could not be read back.")
        if self.get_update_parameters(result):
            self.client.fail_request("Token metadata readback does not match the requested values; the server may not support the requested metadata values.")
        return result

    def delete_resource(self, resource):
        path = TOKEN_ENTRY_PATH.format(quote(resource["userid"], safe=""), resource["tokenid"])
        self.call_mutation("Deleting API token", self.api.delete, path)
        if self.get_resource() is not None:
            self.client.fail_request("Token deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            userid, tokenid = self.module.params["userid"], self.module.params["tokenid"]
            predicted = {"userid": userid, "tokenid": tokenid, "full_tokenid": FULL_TOKEN_ID_TEMPLATE.format(userid, tokenid), "privsep": True, "comment": ""}
            return self.apply_parameter_updates(predicted, self.get_update_parameters(predicted))
        return super().get_check_mode_resource(operation, resource, updates)

    def prepare_result(self, changed, resource=None, **values):
        result = super().prepare_result(changed, resource, **values)
        if self.created_secret is not None:
            result["token_secret"] = self.created_secret
        return result


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "userid": {"type": "str", "required": True}, "tokenid": {"type": "str", "required": True, "no_log": False},
            "privsep": {"type": "bool"}, "expire": {"type": "int"}, "comment": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxTokenModule(module).execute()


if __name__ == "__main__":
    main()
