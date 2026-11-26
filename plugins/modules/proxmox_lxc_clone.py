#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_clone
short_description: Create a clone of an LXC container or template
description:
  - Creates a clone of an LXC container or template.
  - The operation is not idempotent. Each successful call creates a new container.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description:
      - Proxmox VE node containing the source container or template.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the source LXC container or template.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  newid:
    description:
      - Identifier to assign to the cloned container.
      - The identifier must not already be in use and must range from 100 to 999999999.
    type: int
    required: true
  hostname:
    description: Hostname to assign to the cloned container.
    type: str
  description:
    description: Description for the cloned container.
    type: str
  full:
    description:
      - Create a full copy of all disks.
      - Set to V(false) to request a linked clone, which requires support from the source disks and storage.
      - When omitted, Proxmox defaults to a full clone from a regular container and a linked clone from a template.
    type: bool
  pool:
    description: Add the cloned container to this resource pool.
    type: str
  snapname:
    description: Snapshot name to use as the clone source.
    type: str
  storage:
    description: Target storage for a full clone.
    type: str
  target:
    description:
      - Target node for the clone.
      - This is allowed only when the source container uses shared storage.
    type: str
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Checks VMID availability, cross-node destination storage, and linked-clone disk support without cloning.
seealso:
  - module: ansible.proxmox.proxmox_lxc
    description: Manage the container after it has been cloned.
  - module: ansible.proxmox.proxmox_lxc_info
    description: Retrieve LXC container information.
notes:
  - This module is not idempotent; a subsequent invocation fails if O(newid) is in use.
  - The LXC clone API does not support selecting a disk format. The destination storage default is used.
  - The API identity must have the permissions required to clone the source container or template on C(/vms),
    including C(VM.Clone), and to allocate the new container with C(VM.Allocate) on C(/vms/{newid}) or its pool.
  - Cloning also requires C(Datastore.AllocateSpace) on each storage used by the clone and C(SDN.Use) on any bridge used by the container configuration.
"""

EXAMPLES = r"""
- name: Clone an LXC container
  ansible.proxmox.proxmox_lxc_clone:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    newid: 101
    hostname: my-lxc-copy
    full: true
    task_timeout: 900

- name: Clone a container from a snapshot
  ansible.proxmox.proxmox_lxc_clone:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    vmid: 200
    newid: 201
    snapname: before-upgrade
    storage: local-lvm
"""

RETURN = r"""
clone:
  description: Identifier of the newly created LXC container clone.
  returned: success
  type: dict
  sample: {"vmid": 101}
task_upid:
  description: UPID of the completed Proxmox clone task.
  returned: when the clone request returns a task UPID
  type: str
  sample: 'UPID:pve01:00000001:00000001:00000001:vzclone:100:root@pam:'
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ProxmoxLxcCloneModule(ProxmoxPower, ProxmoxActionModule):
    """Create a non-idempotent clone through Proxmox's LXC clone endpoint."""

    result_key = "clone"
    guest_type = "lxc"
    CLONE_OPTIONS = ("description", "full", "hostname", "pool", "snapname", "storage", "target")

    def __init__(self, module, client=None):
        super().__init__(module, client=client, guest_type=self.guest_type)

    def is_action_needed(self):
        """Validate clone identifiers and authorize the non-idempotent action."""
        self._validate_vmids()
        self.client.validate_pve_permissions(self.api, "/vms")
        self._validate_check_mode()
        return True

    def _validate_check_mode(self):
        """Validate clone options and destination availability before cloning."""
        self._validate_clone_options()
        self._validate_destination_vmid()
        if not self.module.check_mode:
            return

        source = self._validate_source_lxc()
        self._validate_target_storage()
        self._validate_linked_clone_support(source)

    def _validate_clone_options(self):
        """Reject storage for an explicitly requested linked clone."""
        if self.module.params.get("full") is False and self.module.params.get("storage") is not None:
            self.module.fail_json(msg="storage cannot be used with full=false for an LXC clone.")

    def _validate_destination_vmid(self):
        """Verify that the destination VMID is unused across the cluster."""
        newid = self.module.params["newid"]
        resources = self.call_api(
            "Checking whether VMID {0} is available for cloning".format(newid),
            self.api.cluster.resources.get,
            type="vm",
        ) or []
        if any(
            isinstance(resource, dict) and str(resource.get("vmid")) == str(newid)
            for resource in resources
        ):
            self.module.fail_json(msg="VMID {0} is already in use.".format(newid))

    def _validate_source_lxc(self):
        """Verify that the source LXC container or template exists on the node."""
        vmid = self.module.params["vmid"]
        node = self.module.params["node"]
        source_containers = self.call_api(
            "Retrieving LXC containers from node {0}".format(node),
            self.api.nodes(node).lxc.get,
        )
        source = next(
            (
                resource
                for resource in source_containers or []
                if isinstance(resource, dict) and str(resource.get("vmid")) == str(vmid)
            ),
            None,
        )
        if source is None:
            self.module.fail_json(msg="LXC container {0} is not available on node {1}.".format(vmid, node))
        return source

    def _validate_target_storage(self):
        """Require shared destination storage when cloning to another node."""
        params = self.module.params
        storage = params.get("storage")
        target = params.get("target")
        if not storage or not target or target in (params["node"], "localhost"):
            return

        storage_config = self.call_api(
            "Retrieving configuration for storage {0}".format(storage),
            self.api.storage(storage).get,
        )
        if not isinstance(storage_config, dict):
            self.module.fail_json(msg="Unable to determine whether storage '{0}' is shared.".format(storage))
        if storage_config.get("shared", 0) not in (1, "1", True):
            self.module.fail_json(msg="can't clone to non-shared storage '{0}'".format(storage))

    def _validate_linked_clone_support(self, source):
        """Check storage feature support when the request uses a linked clone."""
        params = self.module.params
        full = params.get("full")
        uses_linked_clone = full is False or (full is None and source.get("template") in (1, "1", True))
        if not uses_linked_clone:
            return
        if params.get("storage") is not None:
            self.module.fail_json(msg="storage cannot be used with a linked LXC clone.")

        vmid = params["vmid"]
        node = params["node"]
        options = {"feature": "clone"}
        if params.get("snapname") is not None:
            options["snapname"] = params["snapname"]
        features = self.call_api(
            "Checking linked clone support for LXC container {0} on node {1}".format(vmid, node),
            self.vm_resource().feature.get,
            **options
        )
        if not isinstance(features, dict) or features.get("hasFeature") not in (0, "0", 1, "1"):
            self.module.fail_json(
                msg="Unable to determine linked clone support for LXC container {0} on node {1}.".format(
                    vmid, node
                )
            )
        if features["hasFeature"] not in (1, "1", True):
            self.module.fail_json(
                msg=(
                    "Linked clone feature is not supported for one or more disks of LXC container {0} on node {1}. "
                    "Use full=true to create a full clone."
                ).format(vmid, node)
            )

    def _validate_vmids(self):
        """Validate the source and destination VMIDs before calling Proxmox."""
        vmid = self.module.params["vmid"]
        newid = self.module.params["newid"]
        for parameter, value in (("vmid", vmid), ("newid", newid)):
            if not 100 <= value <= 999999999:
                self.module.fail_json(msg="{0} must be between 100 and 999999999.".format(parameter))
        if vmid == newid:
            self.module.fail_json(msg="newid must be different from vmid.")

    def perform_action(self):
        """Submit the LXC clone request and wait for its asynchronous task."""
        params = self.module.params
        vmid = params["vmid"]
        node = params["node"]
        newid = params["newid"]
        options = {"newid": newid}
        options.update(
            {option: params[option] for option in self.CLONE_OPTIONS if params.get(option) is not None}
        )
        if "full" in options:
            options["full"] = int(options["full"])
        self._task_upid = self.call_mutation(
            "Cloning LXC container {0} on node {1} as LXC container {2}".format(vmid, node, newid),
            self.vm_resource().clone.post,
            **options
        )
        if not isinstance(self._task_upid, str) or not self._task_upid.startswith("UPID:"):
            self.module.fail_json(msg="LXC container {0} clone request did not return a task UPID.".format(vmid))
        ProxmoxTask(self.module, client=self.client).wait_for_task(self._task_upid)
        return self.get_action_result()

    def get_action_result(self):
        """Return the destination VMID for a real or predicted clone."""
        return {"vmid": self.module.params["newid"]}

    def prepare_result(self, changed, resource=None, **values):
        """Include the completed clone task UPID in the action result."""
        result = super().prepare_result(changed, resource, **values)
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result


def main():
    """Parse LXC clone arguments and create a container clone."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int", "required": True},
                "newid": {"type": "int", "required": True},
                "hostname": {"type": "str"},
                "description": {"type": "str"},
                "full": {"type": "bool"},
                "pool": {"type": "str"},
                "snapname": {"type": "str"},
                "storage": {"type": "str"},
                "target": {"type": "str"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcCloneModule(module).execute_action_module()


if __name__ == "__main__":
    main()
