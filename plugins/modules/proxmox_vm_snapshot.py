#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_snapshot
short_description: Manage QEMU/KVM virtual machine snapshots
description:
  - Creates, updates, or deletes snapshots of a QEMU/KVM virtual machine.
  - With O(state=present), creates a snapshot or updates its description when necessary.
  - With O(state=absent), deletes an existing snapshot and returns unchanged when it is already absent.
  - With O(state=rollback), restores the VM to an existing snapshot.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Proxmox VE node containing the virtual machine.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the QEMU/KVM virtual machine.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  snapname:
    description:
      - Snapshot name of 2-40 ASCII characters, starting with a letter and containing only letters, digits, underscores, or hyphens.
      - The names V(current) and V(pending) are reserved by Proxmox.
    type: str
    required: true
  description:
    description:
      - Description or comment for a new or existing snapshot.
      - When omitted, an existing description is preserved. An empty string clears it.
    type: str
  state:
    description:
      - C(present) creates the snapshot if it does not exist.
      - C(absent) deletes the snapshot.
      - C(rollback) rolls back the VM to the snapshot.
      - C(rollback) is not idempotent and always reports C(changed=true) on success.
    type: str
    choices: [present, absent, rollback]
    default: present
  vmstate:
    description:
      - Include the VM RAM when creating a snapshot.
      - This is a creation-only setting; saved RAM cannot be changed on an existing snapshot.
    type: bool
    default: false
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Predicts snapshot creation, description updates, deletion, and rollback without modifying the VM.
seealso:
  - module: ansible.proxmox.proxmox_vm_snapshot_info
notes:
  - Snapshot support depends on the VM disks, storage, and current configuration.
  - Rollback is not idempotent; each invocation restores the snapshot and reports a change.
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Create a snapshot before an upgrade
  ansible.proxmox.proxmox_vm_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    description: Before the application upgrade
    vmstate: true
    task_timeout: 900

- name: Update a snapshot description
  ansible.proxmox.proxmox_vm_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    description: Verified recovery point
    state: present

- name: Roll the VM back to a snapshot
  ansible.proxmox.proxmox_vm_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    state: rollback

- name: Delete a snapshot
  ansible.proxmox.proxmox_vm_snapshot:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
    state: absent
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


class ProxmoxVmSnapshotModule(ProxmoxResourceModule):
    """Manage snapshot resources and perform explicit VM rollback actions."""

    result_key = "snapshot"

    def execute_resource_module(self):
        """Run rollback actions or delegate present/absent to the shared lifecycle."""
        if self.module.params["state"] != "rollback":
            return super().execute_resource_module()
        resource = self.get_resource()
        snapname = self.module.params["snapname"]
        if resource is None:
            self.module.fail_json(
                msg="Snapshot {0} does not exist on VM {1}.".format(snapname, self.module.params["vmid"])
            )
        if not self.module.check_mode:
            task_upid = self.call_mutation(
                "Rolling back VM {0} to snapshot {1}".format(self.module.params["vmid"], snapname),
                self._snapshot_api(snapname).rollback.post,
            )
            self._wait_for_snapshot_task(task_upid)
        self.exit(changed=True, resource=resource)

    def get_resource(self):
        """Validate parameters and retrieve the requested snapshot metadata.

        Returns:
            dict or None: Snapshot metadata, or None when the name is absent.
        """
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
        if snapname == "current" or snapname.lower() == "pending":
            self.module.fail_json(msg="snapname must not be 'current' or 'pending'.")
        self.client.validate_pve_permissions(self.api, "/vms")
        self._snapshot_api = self.api.nodes(params["node"]).qemu(vmid).snapshot
        snapshots = self.call_api("Retrieving snapshot {0}".format(snapname), self._snapshot_api.get)
        return next(
            (snapshot for snapshot in snapshots or [] if isinstance(snapshot, dict) and snapshot.get("name") == snapname),
            None,
        )

    def create_resource(self):
        """Create the missing snapshot and wait for its asynchronous task.

        Returns:
            dict: Identifier of the created snapshot.
        """
        params = self.module.params
        options = {"snapname": params["snapname"], "vmstate": int(params["vmstate"])}
        if params.get("description") is not None:
            options["description"] = params["description"]
        task_upid = self.call_mutation(
            "Creating snapshot {0} of VM {1} on node {2}".format(params["snapname"], params["vmid"], params["node"]),
            self._snapshot_api.post,
            **options
        )
        self._wait_for_snapshot_task(task_upid)
        return {"name": params["snapname"]}

    def get_update_parameters(self, resource):
        """Compare only the mutable description supplied by the caller.

        Args:
            resource (dict): Existing snapshot metadata.

        Returns:
            dict: Changed description, or an empty mapping for a no-op.
        """
        description = self.module.params.get("description")
        if description is None:
            return {}
        return self.get_parameter_updates(
            {"description": resource.get("description", "")}, {"description": description}, {},
        )

    def update_resource(self, resource, updates):
        """Apply a synchronous snapshot description update.

        Args:
            resource (dict): Existing snapshot metadata.
            updates (dict): Description change to submit.

        Returns:
            dict: Metadata with the updated description.
        """
        snapname = self.module.params["snapname"]
        self.call_mutation("Updating snapshot {0}".format(snapname), self._snapshot_api(snapname).config.put, **updates)
        return self.apply_parameter_updates(resource, updates)

    def delete_resource(self, _resource):
        """Delete the existing snapshot and wait for its asynchronous task.

        Args:
            _resource (dict): Existing metadata, already checked by the lifecycle.
        """
        snapname = self.module.params["snapname"]
        task_upid = self.call_mutation("Deleting snapshot {0}".format(snapname), self._snapshot_api(snapname).delete)
        self._wait_for_snapshot_task(task_upid)

    def _wait_for_snapshot_task(self, task_upid):
        """Validate and wait for a snapshot creation, deletion, or rollback task.

        Args:
            task_upid (str): Task identifier returned by the mutation endpoint.
        """
        if not isinstance(task_upid, str) or not task_upid.startswith("UPID:"):
            self.client.fail_request("Snapshot operation did not return a task UPID.")
        ProxmoxTask(self.module, client=self.client).wait_for_task(task_upid)

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        """Predict creation identifiers and reuse shared update/delete results.

        Args:
            operation (str): Requested create, update, or delete operation.
            resource (dict, optional): Existing snapshot metadata.
            updates (dict, optional): Predicted description changes.

        Returns:
            dict or None: Predicted snapshot result, or None after deletion.
        """
        if operation == "create":
            return {"name": self.module.params["snapname"]}
        return super().get_check_mode_resource(operation, resource=resource, updates=updates)


def main():
    """Parse arguments and reconcile the requested snapshot state."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "node": {"type": "str", "required": True},
            "vmid": {"type": "int", "required": True},
            "snapname": {"type": "str", "required": True},
            "state": {"type": "str", "choices": ["present", "absent", "rollback"], "default": "present"},
            "description": {"type": "str"},
            "vmstate": {"type": "bool", "default": False},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmSnapshotModule(module).execute_resource_module()


if __name__ == "__main__":
    main()
