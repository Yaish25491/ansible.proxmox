#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_vm_migrate
short_description: Migrate a QEMU/KVM virtual machine to another node
description:
  - Reads and validates preconditions before submitting VM migration.
  - A running VM requires O(online=true).
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Source Proxmox VE node containing the VM.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the QEMU/KVM virtual machine.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  target:
    description: Destination Proxmox VE node.
    type: str
    required: true
  bwlimit:
    description:
      - Override the configured migration I/O bandwidth limit in KiB/s.
      - Must be zero or greater.
    type: int
  force:
    description:
      - Allow migration with local resources.
      - C(force=true) requires password authentication as C(root@pam); API tokens are not accepted.
    type: bool
  migration_network:
    description:
      - CIDR of the network used for migration.
      - Requires password authentication as C(root@pam); API tokens are not accepted.
    type: str
  migration_type:
    description:
      - Migration transport security mode.
      - Requires password authentication as C(root@pam); API tokens are not accepted.
    type: str
    choices: [secure, insecure]
  online:
    description:
      - Use live migration.
      - Required when the source VM is running.
    type: bool
  targetstorage:
    description:
      - Storage mapping from source to target.
      - A single storage ID maps all source storage; V(1) keeps storage IDs unchanged.
    type: str
  with_conntrack_state:
    description: Migrate conntrack state for a running VM when the host supports it.
    type: bool
  with_local_disks:
    description: Enable live storage migration for local disks.
    type: bool
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Checks migration preconditions and predicts the migration without submitting it.
seealso:
  - module: ansible.proxmox.proxmox_vm_info
notes:
  - The result includes the preconditions returned by Proxmox.
  - The target must appear in the precondition response's C(allowed_nodes), even when storage overrides are supplied.
  - Local CD/DVD devices prevent migration and are rejected before submission.
  - O(force=true), O(migration_network), and O(migration_type) require password authentication as C(root@pam); API tokens are not accepted.
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Check and migrate a VM to another node
  ansible.proxmox.proxmox_vm_migrate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    target: pve02
    online: true
    bwlimit: 102400
    force: false
    targetstorage: local-lvm
    with_conntrack_state: true
    with_local_disks: true
    task_timeout: 1800
"""

RETURN = r"""
migration:
  description: Source and target VM migration identifiers.
  returned: success
  type: dict
  contains:
    node:
      description: Source node.
      type: str
    target:
      description: Destination node.
      type: str
    vmid:
      description: Migrated VM identifier.
      type: int
  sample: {"node": "pve01", "target": "pve02", "vmid": 100}
preconditions:
  description: Migration preconditions returned by the Proxmox API.
  returned: success
  type: dict
task_upid:
  description: UPID of the completed migration task.
  returned: when a migration task is submitted
  type: str
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ProxmoxVmMigrateModule(ProxmoxActionModule):
    """Validate migration preconditions and migrate a VM through the shared action lifecycle."""

    result_key = "migration"
    API_OPTIONS = {
        "bwlimit": "bwlimit",
        "force": "force",
        "migration_network": "migration_network",
        "migration_type": "migration_type",
        "online": "online",
        "targetstorage": "targetstorage",
        "with_conntrack_state": "with-conntrack-state",
        "with_local_disks": "with-local-disks",
    }

    def is_action_needed(self):
        """Validate identifiers, permissions, and migration preconditions.

        Returns:
            bool: True because each valid request attempts a migration.
        """
        params = self.module.params
        vmid = params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        if params["node"] == params["target"]:
            self.module.fail_json(msg="target must be different from the source node.")
        if params.get("bwlimit") is not None and params["bwlimit"] < 0:
            self.module.fail_json(msg="bwlimit must be zero or greater.")

        root_only_options = []
        if params.get("force") is True:
            root_only_options.append("force=true")
        if params.get("migration_network") is not None:
            root_only_options.append("migration_network")
        if params.get("migration_type") is not None:
            root_only_options.append("migration_type")

        api_user = params.get("api_user")
        if root_only_options and (api_user != "root@pam" or params.get("api_token_id")):
            self.module.fail_json(
                msg="Migration options {0} require password authentication as api_user=root@pam; API tokens are not accepted.".format(
                    ", ".join(root_only_options)
                ),
                required_api_user="root@pam",
                required_authentication="api_password",
            )

        self.client.validate_pve_permissions(self.api, "/vms")
        self._migration_api = self.api.nodes(params["node"]).qemu(vmid).migrate
        self._preconditions = self.call_api(
            "Checking migration preconditions for VM {0} from node {1} to node {2}".format(
                vmid, params["node"], params["target"]
            ),
            self._migration_api.get,
            target=params["target"],
        )
        self._validate_preconditions()
        return True

    def _validate_preconditions(self):
        """Fail closed when Proxmox cannot verify the requested migration."""
        params = self.module.params
        preconditions = self._preconditions
        if not isinstance(preconditions, dict):
            self.module.fail_json(msg="Unable to determine VM migration preconditions.")

        local_cdroms = [
            disk
            for disk in preconditions.get("local_disks") or []
            if isinstance(disk, dict) and disk.get("cdrom") in (1, "1", True)
        ]
        if local_cdroms:
            self.module.fail_json(
                msg="Cannot migrate VM with local CD/DVD devices.",
                local_disks=local_cdroms,
            )

        if preconditions.get("running") in (1, "1", True) and not params.get("online"):
            self.module.fail_json(msg="VM is running; set online=true to migrate it.")

        local_resources = preconditions.get("local_resources") or []
        if local_resources and not params.get("force"):
            self.module.fail_json(
                msg="VM has local resources that block migration; set force=true to override.",
                local_resources=local_resources,
            )

        if params.get("with_conntrack_state") and preconditions.get("has-dbus-vmstate") not in (1, "1", True):
            self.module.fail_json(msg="The source host does not support migrating conntrack state.")

        allowed_nodes = preconditions.get("allowed_nodes")
        if not isinstance(allowed_nodes, list):
            self.module.fail_json(msg="Unable to determine nodes allowed for VM migration.")
        target = params["target"]
        if target in allowed_nodes:
            return

        blockers = preconditions.get("not_allowed_nodes") or {}
        target_blockers = blockers.get(target) if isinstance(blockers, dict) else None
        self.module.fail_json(
            msg="Target node {0} is not allowed for VM migration.".format(target),
            not_allowed_nodes=target_blockers,
        )

    def perform_action(self):
        """Submit migration and wait for Proxmox's task to complete.

        Returns:
            dict: Source node, target node, and migrated VM identifier.
        """
        params = self.module.params
        options = {"target": params["target"]}
        for parameter, api_parameter in self.API_OPTIONS.items():
            value = params.get(parameter)
            if value is not None:
                options[api_parameter] = int(value) if isinstance(value, bool) else value

        task_upid = self.call_mutation(
            "Migrating VM {0} from node {1} to node {2}".format(
                params["vmid"], params["node"], params["target"]
            ),
            self._migration_api.post,
            **options
        )
        if not isinstance(task_upid, str) or not task_upid.startswith("UPID:"):
            self.module.fail_json(msg="VM migration request did not return a task UPID.")
        self._task_upid = task_upid
        ProxmoxTask(self.module, client=self.client).wait_for_task(task_upid, include_log_on_failure=True)
        return self.get_action_result()

    def get_action_result(self):
        """Return the migration identifiers for check-mode and real results.

        Returns:
            dict: Source node, target node, and migrated VM identifier.
        """
        params = self.module.params
        return {"node": params["node"], "target": params["target"], "vmid": params["vmid"]}

    def prepare_result(self, changed, resource=None, **values):
        """Include preconditions and the completed migration task in the result.

        Args:
            changed (bool): Whether migration was performed or is predicted.
            resource (dict, optional): Source and target migration identifiers.
            **values: Additional top-level result fields.

        Returns:
            dict: Result containing migration data, preconditions, and optional task UPID.
        """
        result = super().prepare_result(changed, resource, **values)
        if hasattr(self, "_preconditions"):
            result["preconditions"] = self._preconditions
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result


def main():
    """Parse migration arguments and execute the preflight and migration action."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "node": {"type": "str", "required": True},
            "vmid": {"type": "int", "required": True},
            "target": {"type": "str", "required": True},
            "bwlimit": {"type": "int"},
            "force": {"type": "bool"},
            "migration_network": {"type": "str"},
            "migration_type": {"type": "str", "choices": ["secure", "insecure"]},
            "online": {"type": "bool"},
            "targetstorage": {"type": "str"},
            "with_conntrack_state": {"type": "bool"},
            "with_local_disks": {"type": "bool"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxVmMigrateModule(module).execute_action_module()


if __name__ == "__main__":
    main()
