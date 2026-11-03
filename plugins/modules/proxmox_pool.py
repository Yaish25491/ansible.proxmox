#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_pool
short_description: Manage Proxmox VE resource pools and membership
description:
  - Creates, updates and deletes Proxmox VE resource pools, including nested pools.
  - Manages comments and membership of QEMU VMs, LXC containers and configured storage.
  - Uses the current pool API with query parameters to support nested pool identifiers.
version_added: "1.0.0"
options:
  poolid:
    description:
      - Unique pool identifier, optionally including up to three slash-separated nesting levels.
      - Each newly created pool name must start with a letter and its parent must already exist.
    type: str
    required: true
    aliases: [name]
  state:
    description:
      - Whether the pool should exist.
      - A pool must contain no guests, storage or child pools before it can be removed.
      - Removing a pool does not delete its former guests or storage.
    type: str
    choices: [present, absent]
    default: present
  comment:
    description:
      - Pool description. Omit to preserve the current comment; use an empty string to clear it.
      - The literal string C("0") is rejected with O(state=present) because Proxmox does not preserve it.
    type: str
  vms:
    description:
      - VMIDs of QEMU VMs or LXC containers whose pool membership should be managed.
      - Omit to preserve guest membership. An empty list with O(membership_mode=exact) removes all guest memberships.
      - VMIDs must be between 100 and 999999999.
    type: list
    elements: int
  storage:
    description:
      - Configured storage IDs whose pool membership should be managed.
      - Omit to preserve storage membership. An empty list with O(membership_mode=exact) removes all storage memberships.
      - Membership changes do not create or delete the underlying storage.
    type: list
    elements: str
  membership_mode:
    description:
      - With V(exact), supplied membership lists are authoritative for their respective resource types.
      - With V(present), add supplied members while preserving other members.
      - With V(absent), remove supplied members while preserving other members.
      - Applies only with O(state=present). Omitted lists always preserve their current membership.
    type: str
    choices: [exact, present, absent]
    default: exact
  allow_move:
    description:
      - Allow a requested guest to move from another pool to this pool.
      - With V(false), adding a guest assigned to another pool fails before changing the pool.
      - Applies only to guest additions; storage may belong to multiple pools.
    type: bool
    default: false
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Predicts pool and membership changes without modifying the server.
notes:
  - Requires propagated C(Administrator) privileges on C(/pool).
  - Supplying O(vms) also requires propagated C(Administrator) privileges on C(/vms).
  - Supplying O(storage) also requires propagated C(Administrator) privileges on C(/storage).
  - Pool operations are synchronous. Updates involving both additions and removals use multiple API requests;
    preceding successful requests are not rolled back if a subsequent request fails.
  - Failures report C(changed=true) when an earlier API mutation completed successfully.
  - Check mode returns a predicted configuration; newly added members may contain fewer fields than live API results.
"""

EXAMPLES = r"""
- name: Create a pool with a comment
  ansible.proxmox.proxmox_pool:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications
    comment: Application workloads

- name: Reconcile all guest and storage memberships
  ansible.proxmox.proxmox_pool:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications/production
    vms: [100, 101]
    storage: [local-lvm]
    allow_move: true

- name: Remove one guest membership without changing other members
  ansible.proxmox.proxmox_pool:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications/production
    vms: [101]
    membership_mode: absent

- name: Remove an empty pool
  ansible.proxmox.proxmox_pool:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications/production
    state: absent
"""

RETURN = r"""
pool:
  description: Current or predicted pool configuration, or null when the pool is absent.
  returned: always
  type: dict
  contains:
    poolid:
      description: Unique pool identifier.
      type: str
      returned: when the pool exists
    comment:
      description: Pool description, when configured.
      type: str
      returned: when configured
    members:
      description: Guest and storage member records returned by Proxmox.
      type: list
      elements: dict
      returned: when the pool exists
  sample:
    poolid: applications
    comment: Application workloads
    members: []
"""

import copy
import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxPoolModule(ProxmoxResourceModule):
    """Reconcile pools through the shared resource lifecycle."""

    result_key = "pool"
    member_keys = {"vms": "vmid", "storage": "storage"}

    def __init__(self, module, client=None):
        super().__init__(module, client=client)
        self.guest_info = {}

    def get_resource(self):
        """Validate inputs and permissions, then discover the pool and its members."""
        self._validate_parameters()
        poolid = self.module.params["poolid"]
        state = self.module.params["state"]
        pools = self._list_pools()
        resource = self._get_pool(poolid, pool_summaries=pools)
        if resource is None and state == "present":
            self._validate_new_pool(poolid, pools)
        if resource is not None and state == "absent":
            self._validate_empty(resource, pools)
        return resource

    def _validate_parameters(self):
        """Check identifiers, comments and membership permissions before discovery."""
        params = self.module.params
        if not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,2}", params["poolid"]):
            self.module.fail_json(msg="poolid must contain one to three slash-separated names using letters, digits, '.', '_' or '-'.")
        if params["state"] == "present" and params.get("comment") == "0":
            self.module.fail_json(msg='Proxmox cannot preserve comment="0"; use an empty string to clear the comment or choose another value.')
        for vmid in params.get("vms") or []:
            if not 100 <= vmid <= 999999999:
                self.module.fail_json(msg="VMIDs must be between 100 and 999999999.")
        for option, acl in (("vms", "/vms"), ("storage", "/storage")):
            if params.get(option) is None:
                continue
            if params["state"] == "absent":
                self.module.fail_json(msg="{0} cannot be supplied with state=absent; remove memberships first.".format(option))
            self.client.validate_pve_permissions(self.api, acl)

    def _list_pools(self):
        """Require visible pool inventory before deciding whether a pool exists."""
        self.client.validate_pve_permissions(self.api, "/pool")
        return self.call_api("Listing Proxmox pools", self.api.pools.get)

    def _get_pool(self, poolid, pool_summaries=None):
        """Retrieve pool details, reusing checked summaries only for this call."""
        pools = self._list_pools() if pool_summaries is None else pool_summaries
        if not self.validate_proxmox_resource_exist(
            "pool", poolid, lambda: pools, resource_key="poolid", fail_on_missing=False,
        ):
            return None
        resources = self.call_api("Retrieving pool {0}".format(poolid), self.api.pools.get, poolid=poolid)
        return resources[0] if resources else None

    def _validate_new_pool(self, poolid, pools):
        """Check the new leaf name and require its parent to exist."""
        parent, separator, leaf = poolid.rpartition("/")
        if not re.match(r"[A-Za-z]", leaf):
            self.module.fail_json(msg="Each newly created pool name must start with a letter.")
        if separator and not any(pool["poolid"] == parent for pool in pools):
            self.module.fail_json(msg="Parent pool {0} does not exist.".format(parent))

    def _validate_empty(self, resource, pools):
        """Reject nonempty pools in real and check mode, including nested children."""
        prefix = self.module.params["poolid"] + "/"
        if resource.get("members") or any(pool["poolid"].startswith(prefix) for pool in pools):
            self.module.fail_json(msg="Pool {0} is not empty; remove memberships and child pools first.".format(resource["poolid"]))

    def get_update_parameters(self, resource):
        """Compute membership deltas and changed comments without mutating input."""
        updates = self._get_membership_updates(resource)
        comment = self.module.params.get("comment")
        if comment is not None and comment != resource.get("comment", ""):
            updates["comment"] = comment
        additions = updates.get("add", {})
        self._validate_guest_additions(additions.get("vms", []))
        self._validate_storage_additions(additions.get("storage", []))
        return updates

    def _get_membership_updates(self, resource):
        """Compare supplied memberships with their requested final sets."""
        updates = {}
        mode = self.module.params["membership_mode"]
        for option, member_key in self.member_keys.items():
            requested = self.module.params.get(option)
            if requested is None:
                continue
            current = {member[member_key] for member in resource.get("members", []) if member_key in member}
            desired = set(requested)
            if mode == "present":
                desired |= current
            elif mode == "absent":
                desired = current - desired
            for operation, values in (("add", desired - current), ("remove", current - desired)):
                if values:
                    updates.setdefault(operation, {})[option] = sorted(values)
        return updates

    def _validate_guest_additions(self, vmids):
        """Require existing guests and explicit permission to move their membership."""
        if not vmids:
            return
        guests = self.call_api("Listing guests for pool membership", self.api.cluster.resources.get, type="vm")
        self.guest_info = {guest["vmid"]: guest for guest in guests}
        for vmid in vmids:
            guest = self.guest_info.get(vmid)
            if guest is None:
                self.module.fail_json(msg="Guest {0} does not exist.".format(vmid))
            owner = guest.get("pool")
            if owner and owner != self.module.params["poolid"] and not self.module.params["allow_move"]:
                self.module.fail_json(msg="Guest {0} belongs to pool {1}; set allow_move=true to move it.".format(vmid, owner))

    def _validate_storage_additions(self, storage_ids):
        """Require configured storage without querying it for unchanged membership."""
        if not storage_ids:
            return
        storages = self.call_api("Listing configured storage for pool membership", self.api.storage.get)
        missing = set(storage_ids) - {storage["storage"] for storage in storages}
        if missing:
            self.module.fail_json(msg="Storage does not exist: {0}.".format(", ".join(sorted(missing))))

    def _get_update_requests(self, updates):
        """Build removal, addition and comment requests in their execution order."""
        operations = (("remove", {"delete": 1}), ("add", {"allow-move": int(self.module.params["allow_move"])}))
        requests = []
        for operation, flags in operations:
            members = updates.get(operation)
            if not members:
                continue
            parameters = {key: ",".join(str(value) for value in values) for key, values in members.items()}
            requests.append({**parameters, **flags})
        if "comment" in updates:
            if not requests:
                requests.append({})
            requests[0]["comment"] = updates["comment"]
        return requests

    def _apply_updates(self, updates):
        """Execute prepared requests, retaining shared partial-failure reporting."""
        poolid = self.module.params["poolid"]
        for parameters in self._get_update_requests(updates):
            self.call_mutation("Updating pool {0}".format(poolid), self.api.pools.put, poolid=poolid, **parameters)

    def create_resource(self):
        """Create an empty pool, reconcile requested members, and return its config."""
        poolid = self.module.params["poolid"]
        updates = self.get_update_parameters({"poolid": poolid, "members": []})
        parameters = {"poolid": poolid}
        if self.module.params.get("comment") is not None:
            parameters["comment"] = self.module.params["comment"]
        self.call_mutation("Creating pool {0}".format(poolid), self.api.pools.post, **parameters)
        updates.pop("comment", None)
        self._apply_updates(updates)
        return self._get_pool(poolid)

    def update_resource(self, _resource, updates):
        """Apply only changed values and return the refreshed configuration."""
        self._apply_updates(updates)
        return self._get_pool(self.module.params["poolid"])

    def delete_resource(self, _resource):
        """Delete an already validated empty pool synchronously."""
        self.call_mutation("Deleting pool {0}".format(self.module.params["poolid"]), self.api.pools.delete, poolid=self.module.params["poolid"])
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        """Predict membership and comment changes using the same validated deltas."""
        if operation == "delete":
            return None
        if operation == "create":
            resource = {"poolid": self.module.params["poolid"], "members": []}
            updates = self.get_update_parameters(resource)
        return self._predict_pool(resource, updates)

    def _predict_pool(self, resource, updates):
        """Apply the same membership deltas to a copy for check-mode results."""
        predicted = copy.deepcopy(resource)
        if "comment" in updates:
            predicted["comment"] = updates["comment"]
        members = predicted["members"]
        for option, member_key in self.member_keys.items():
            removed = updates.get("remove", {}).get(option, [])
            members = [member for member in members if member.get(member_key) not in removed]
        additions = updates.get("add", {})
        guest_fields = ("id", "vmid", "type", "node", "name")
        members.extend(
            {key: self.guest_info[vmid][key] for key in guest_fields if key in self.guest_info[vmid]}
            for vmid in additions.get("vms", [])
        )
        members.extend({"type": "storage", "storage": storage} for storage in additions.get("storage", []))
        predicted["members"] = members
        return predicted


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "poolid": {"type": "str", "required": True, "aliases": ["name"]},
            "comment": {"type": "str"},
            "vms": {"type": "list", "elements": "int"},
            "storage": {"type": "list", "elements": "str"},
            "membership_mode": {"type": "str", "choices": ["exact", "present", "absent"], "default": "exact"},
            "allow_move": {"type": "bool", "default": False},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxPoolModule(module).execute()


if __name__ == "__main__":
    main()
