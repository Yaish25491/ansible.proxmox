#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_snapshot
short_description: Manage LXC container snapshots
description:
  - Creates, updates, or deletes snapshots of an LXC container.
  - With O(state=rollback), restores the container to a snapshot.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Proxmox VE node containing the container.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the LXC container.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  snapname:
    description:
      - Snapshot name of 2-40 ASCII characters, starting with a letter and containing only letters, digits, underscores, or hyphens.
      - The names V(current) and V(vzdump) are reserved for LXC snapshots by Proxmox.
      - The name V(pending) is also rejected because guest configurations use it for uncommitted changes.
    type: str
    required: true
  description:
    description:
      - Description or comment for a new or existing snapshot.
      - When omitted, an existing description is preserved. An empty string clears it.
    type: str
  state:
    description:
      - C(present) creates the snapshot if it does not exist or updates its description.
      - C(absent) deletes the snapshot.
      - C(rollback) rolls the container back to the snapshot and is not idempotent.
    type: str
    choices: [present, absent, rollback]
    default: present
  start:
    description:
      - Whether to start the container after a successful rollback.
      - This option is used only with C(state=rollback).
    type: bool
    default: false
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Predicts snapshot creation, description updates, deletion, and rollback without modifying the container.
seealso:
  - module: ansible.proxmox.proxmox_lxc_snapshot_info
notes:
  - Rollback is not idempotent; each invocation restores the snapshot and reports a change.
  - Rolling back a running container stops it and leaves it stopped unless C(start=true) is requested.
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Create an LXC snapshot
  ansible.proxmox.proxmox_lxc_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    description: Before the container upgrade
    task_timeout: 900

- name: Roll back an LXC container
  ansible.proxmox.proxmox_lxc_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    state: rollback
    start: true
"""

RETURN = r"""
snapshot:
  description: Snapshot metadata or its identifier; null when the snapshot is absent.
  returned: success
  type: dict
  contains:
    name:
      description: Snapshot name.
      returned: when the snapshot is present
      type: str
  sample: {"name": "before_upgrade"}
task_upid:
    description: UPID returned for an asynchronous snapshot operation.
    returned: when creation, deletion, or rollback starts an asynchronous task
    type: str
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ProxmoxLxcSnapshotModule(ProxmoxResourceModule):
    """Manage LXC snapshots through the shared resource lifecycle."""

    result_key = "snapshot"

    def execute_resource_module(self):
        """Run rollback directly or delegate other states to the resource lifecycle."""
        if self.module.params["state"] != "rollback":
            return super().execute_resource_module()
        resource = self.get_resource()
        snapname = self.module.params["snapname"]
        if resource is None:
            self.module.fail_json(
                msg="Snapshot {0} does not exist on LXC container {1}.".format(snapname, self.module.params["vmid"])
            )
        if not self.module.check_mode:
            task_upid = self.call_mutation(
                "Rolling back LXC container {0} to snapshot {1}".format(self.module.params["vmid"], snapname),
                self._snapshot_api(snapname).rollback.post,
                start=int(self.module.params["start"]),
            )
            self._wait_for_snapshot_task(task_upid)
        self.exit(changed=True, resource=resource)

    def get_resource(self):
        """Validate snapshot parameters and retrieve matching snapshot metadata."""
        params = self.module.params
        vmid = params["vmid"]
        snapname = params["snapname"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]+", snapname, flags=re.ASCII) or len(snapname) > 40:
            self.module.fail_json(
                msg=(
                    "snapname must be 2-40 ASCII characters, start with a letter, and "
                    "contain only letters, digits, underscores, or hyphens."
                )
            )
        if snapname in ("current", "vzdump") or snapname.lower() == "pending":
            self.module.fail_json(
                msg="snapname must not be 'current', 'vzdump', or 'pending'."
            )
        self.client.validate_pve_permissions(self.api, "/vms")
        self._snapshot_api = self.api.nodes(params["node"]).lxc(vmid).snapshot
        snapshots = self.call_api("Retrieving snapshot {0}".format(snapname), self._snapshot_api.get)
        resource = next(
            (snapshot for snapshot in snapshots or [] if isinstance(snapshot, dict) and snapshot.get("name") == snapname),
            None,
        )
        if resource is not None and params.get("state", "present") == "present" and params.get("description") is not None:
            snapshot_config = self.call_api(
                "Retrieving snapshot {0} configuration".format(snapname),
                self._snapshot_api(snapname).config.get,
            )
            if isinstance(snapshot_config, dict) and snapshot_config.get("description") is not None:
                current_description = snapshot_config["description"]
                if isinstance(current_description, str):
                    current_description = current_description.rstrip("\r\n")
                resource = dict(resource, description=current_description)
        return resource

    def create_resource(self):
        """Create the missing LXC snapshot and wait for its asynchronous task."""
        params = self.module.params
        options = {"snapname": params["snapname"]}
        if params.get("description") is not None:
            options["description"] = params["description"]
        task_upid = self.call_mutation(
            "Creating snapshot {0} of LXC container {1} on node {2}".format(
                params["snapname"], params["vmid"], params["node"]
            ),
            self._snapshot_api.post,
            **options
        )
        self._wait_for_snapshot_task(task_upid)
        return {"name": params["snapname"]}

    def get_update_parameters(self, resource):
        """Compare normalized descriptions and preserve the requested value for writes."""
        description = self.module.params.get("description")
        if description is None:
            return {}
        current_description = resource.get("description", "")
        if isinstance(current_description, str):
            current_description = current_description.rstrip("\r\n")
        compared_description = description.rstrip("\r\n") if isinstance(description, str) else description
        updates = self.get_parameter_updates(
            {"description": current_description}, {"description": compared_description}, {},
        )
        if updates:
            updates["description"] = description
        return updates

    def update_resource(self, resource, updates):
        """Apply an existing snapshot's description update."""
        snapname = self.module.params["snapname"]
        self.call_mutation("Updating snapshot {0}".format(snapname), self._snapshot_api(snapname).config.put, **updates)
        return self.apply_parameter_updates(resource, updates)

    def delete_resource(self, _resource):
        """Delete the existing snapshot and wait for its asynchronous task."""
        snapname = self.module.params["snapname"]
        task_upid = self.call_mutation("Deleting snapshot {0}".format(snapname), self._snapshot_api(snapname).delete)
        self._wait_for_snapshot_task(task_upid)

    def _wait_for_snapshot_task(self, task_upid):
        """Validate and wait for a snapshot operation task."""
        if not isinstance(task_upid, str) or not task_upid.startswith("UPID:"):
            self.client.fail_request("Snapshot operation did not return a task UPID.")
        self._task_upid = task_upid
        ProxmoxTask(self.module, client=self.client).wait_for_task(task_upid)

    def prepare_result(self, changed, resource=None, **values):
        """Include an UPID when an asynchronous snapshot operation ran."""
        result = super().prepare_result(changed, resource, **values)
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        """Return the predicted identifier for snapshot creation."""
        if operation == "create":
            return {"name": self.module.params["snapname"]}
        return super().get_check_mode_resource(operation, resource=resource, updates=updates)


def main():
    """Parse arguments and reconcile the requested LXC snapshot state."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int", "required": True},
                "snapname": {"type": "str", "required": True},
                "state": {"type": "str", "choices": ["present", "absent", "rollback"], "default": "present"},
                "start": {"type": "bool", "default": False},
                "description": {"type": "str"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcSnapshotModule(module).execute_resource_module()


if __name__ == "__main__":
    main()
