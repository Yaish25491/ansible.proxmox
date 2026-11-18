#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_user
short_description: Manage Proxmox VE user configuration
description:
- Creates, reconciles or removes Proxmox VE account records in an existing realm.
- Preserves omitted attributes and verifies state after synchronous writes. O(task_timeout) has no effect.
version_added: 1.0.0
options:
  userid:
    description:
    - User identifier in C(user@realm) format. The realm must already be configured.
    - Supply the literal identifier, including any percent signs, question marks or number signs; do not URL-encode it.
    type: str
    required: true
  groups:
    description:
    - Authoritative group membership. Omit to preserve current groups; an empty list removes all memberships.
    - Every requested group must already exist. Ordering and duplicate group names are ignored.
    type: list
    elements: str
  enable:
    description: Whether the user is enabled. Omit to preserve existing state; new users default to enabled.
    type: bool
  expire:
    description: Expiration epoch in seconds. Omit to preserve the current value; zero means no expiration.
    type: int
  password:
    description:
    - Initial password, used only when creating the user. Must contain 8 to 64 characters.
    - Ignored for an existing user so repeated creation tasks are idempotent. Password rotation is not supported.
    type: str
  email:
    description: Email address. Omit to preserve the existing value; use an empty string to clear it.
    type: str
  firstname:
    description: First name. Omit to preserve the existing value; use an empty string to clear it.
    type: str
  lastname:
    description: Last name. Omit to preserve the existing value; use an empty string to clear it.
    type: str
  comment:
    description: User comment. Omit to preserve the existing value; use an empty string to clear it.
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
- Does not create realms or provision operating-system or directory accounts. Realm-specific operations are validated by the server.
- Native creation/deletion requires C(Realm.AllocateUser) on the user realm and C(User.Modify) on applicable groups; updates require C(User.Modify).
- The C(root@pam) account cannot be deleted or disabled. The configured API user cannot be deleted or disabled by this module.
- Check mode validates realm/group existence and predicts attributes, but the server validates email and realm password policy only on write.
"""

EXAMPLES = r"""
- name: Manage a user
  ansible.proxmox.proxmox_user:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
    groups:
      - application
    enable: true
    expire: 0
- name: Remove a user
  ansible.proxmox.proxmox_user:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
    state: absent
"""

RETURN = r"""
user:
  description: Current or predicted user configuration, or null when absent.
  returned: always
  type: dict
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

USER_RESOURCE_PATH = "access/users/{0}"


class ProxmoxUserModule(ProxmoxResourceModule):
    """Reconcile account attributes and membership using the shared client."""

    result_key = "user"
    attributes = ("email", "firstname", "lastname", "comment")

    def _validate_parameters(self):
        params = self.module.params
        userid = params["userid"]
        if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use user@realm with a valid realm name and no whitespace, slash or colon.")
        if params.get("expire") is not None and params["expire"] < 0:
            self.module.fail_json(msg="expire must be zero or greater.")
        authuser = params.get("api_user") or (params.get("api_token_id") or "").partition("!")[0]
        if userid in ("root@pam", authuser) and (params["state"] == "absent" or params.get("enable") is False):
            self.module.fail_json(msg="The root or authenticated account cannot be deleted or disabled.")
        groups = params.get("groups")
        if groups is not None and any(re.fullmatch(r"[A-Za-z0-9._-]+", group) is None for group in groups):
            self.module.fail_json(msg="groups must contain valid group identifiers.")
        self._validate_attributes()

    def _validate_attributes(self):
        for name in self.attributes:
            value = self.module.params.get(name)
            maximum = {"email": 254, "comment": 2048}.get(name, 1024)
            if value is not None and len(value) > maximum:
                self.module.fail_json(msg="{0} exceeds {1} characters.".format(name, maximum))

    def _validate_creation(self):
        userid = self.module.params["userid"]
        realms = self.call_api("Listing authentication realms", self.api.get, "access/domains")
        if not any(entry["realm"] == userid.rsplit("@", 1)[1] for entry in realms):
            self.client.fail_request("The requested authentication realm does not exist.")
        password = self.module.params.get("password")
        if password is not None and not 8 <= len(password) <= 64:
            self.module.fail_json(msg="Initial password must contain between 8 and 64 characters.")

    def _validate_groups(self):
        groups = self.module.params.get("groups")
        if not groups:
            return
        inventory = self.call_api("Listing groups", self.api.get, "access/groups")
        missing = set(groups) - {entry["groupid"] for entry in inventory}
        if missing:
            self.client.fail_request("Groups do not exist: {0}.".format(", ".join(sorted(missing))))

    def get_resource(self):
        self._validate_parameters()
        params = self.module.params
        userid = params["userid"]
        self.client.validate_pve_permissions(self.api, "/access")
        users = self.call_api("Listing users", self.api.get, "access/users")
        exists = self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid", fail_on_missing=False)
        if params["state"] == "present":
            if not exists:
                self._validate_creation()
            self._validate_groups()
        if not exists:
            return None
        data = self.call_api("Reading user {0}".format(userid), self.api.get, USER_RESOURCE_PATH.format(quote(userid, safe="")))
        groups = data.get("groups") or []
        return {"userid": userid, "groups": sorted(set(groups.split(",") if isinstance(groups, str) else groups)),
                "enable": bool(int(data.get("enable", 1))), "expire": int(data.get("expire") or 0),
                **{name: data.get(name, "") for name in self.attributes}}

    def get_update_parameters(self, resource):
        desired = {key: self.module.params[key] for key in self.attributes + ("enable", "expire") if self.module.params.get(key) is not None}
        groups = self.module.params.get("groups")
        if groups is not None:
            desired["groups"] = sorted(set(groups))
        return self.get_parameter_updates(resource, desired, {})

    @staticmethod
    def _api_parameters(values):
        parameters = dict(values)
        if "groups" in parameters:
            parameters["groups"] = ",".join(parameters["groups"])
        if "enable" in parameters:
            parameters["enable"] = int(parameters["enable"])
        return parameters

    def create_resource(self):
        userid = self.module.params["userid"]
        values = self.get_update_parameters({})
        parameters = self._api_parameters(values)
        if self.module.params.get("password") is not None:
            parameters["password"] = self.module.params["password"]
        self.call_mutation("Creating user {0}".format(userid), self.api.post, "access/users", userid=userid, **parameters)
        resource = self.get_resource()
        if resource is None:
            self.client.fail_request("Created user could not be read back.")
        updates = self.get_update_parameters(resource)
        return self.update_resource(resource, updates) if updates else resource

    def update_resource(self, resource, updates):
        userid = resource["userid"]
        self.call_mutation("Updating user {0}".format(userid), self.api.put,
                           USER_RESOURCE_PATH.format(quote(userid, safe="")), **self._api_parameters(updates))
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Updated user could not be read back.")
        if self.get_update_parameters(result):
            self.client.fail_request("User readback does not match the requested values.")
        return result

    def delete_resource(self, resource):
        self.call_mutation("Deleting user {0}".format(resource["userid"]), self.api.delete,
                           USER_RESOURCE_PATH.format(quote(resource["userid"], safe="")))
        if self.get_resource() is not None:
            self.client.fail_request("User deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            predicted = {"userid": self.module.params["userid"], "groups": [], "enable": True, "expire": 0,
                         **dict.fromkeys(self.attributes, "")}
            return self.apply_parameter_updates(predicted, self.get_update_parameters(predicted))
        return super().get_check_mode_resource(operation, resource, updates)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "userid": {"type": "str", "required": True},
            "groups": {"type": "list", "elements": "str"},
            "enable": {"type": "bool"},
            "expire": {"type": "int"},
            "password": {"type": "str", "no_log": True},
            "email": {"type": "str"},
            "firstname": {"type": "str"},
            "lastname": {"type": "str"},
            "comment": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxUserModule(module).execute()


if __name__ == "__main__":
    main()
