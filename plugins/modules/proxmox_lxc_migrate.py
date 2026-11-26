#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_migrate
short_description: Migrate an LXC container to another node
description:
  - Reads migration preconditions before submitting an LXC migration.
  - Returns preconditions in both normal and check mode.
  - A running container requires O(restart=true).
  - Proxmox VE does not implement live migration for LXC containers.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Source Proxmox VE node containing the container.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the LXC container.
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
    type: float
  restart:
    description: Use restart migration.
    type: bool
  target_storage:
    description:
      - Mapping from source to target storage.
      - A single storage ID maps all source storage; V(1) keeps storage IDs unchanged.
    type: str
  timeout:
    description: Shutdown timeout in seconds for restart migration.
    type: int
    default: 180
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Checks migration preconditions and predicts the migration without submitting it.
seealso:
  - module: ansible.proxmox.proxmox_lxc
notes:
  - The result includes the preconditions returned by Proxmox.
  - When Proxmox returns C(allowed_nodes), the target must be listed there.
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Migrate an LXC container to another node
  ansible.proxmox.proxmox_lxc_migrate:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    target: pve02
    restart: true
    target_storage: local-lvm
    task_timeout: 1800
"""

RETURN = r"""
migration:
  description: Source and target LXC migration identifiers.
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
      description: Migrated container identifier.
      type: int
  sample: {"node": "pve01", "target": "pve02", "vmid": 100}
preconditions:
  description: Migration preconditions returned by the Proxmox API.
  returned: success
  type: dict
  contains:
    running:
      description: Whether the container is running on the source node.
      returned: always
      type: bool
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


class ProxmoxLxcMigrateModule(ProxmoxActionModule):
    """Validate migration preconditions and migrate an LXC container."""

    result_key = "migration"
    API_OPTIONS = {
        "bwlimit": "bwlimit",
        "restart": "restart",
        "target_storage": "target-storage",
        "timeout": "timeout",
    }

    def is_action_needed(self):
        """Validate identifiers, permissions, and migration preconditions."""
        params = self.module.params
        vmid = params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        if params["node"] == params["target"]:
            self.module.fail_json(msg="target must be different from the source node.")
        if params.get("bwlimit") is not None and params["bwlimit"] < 0:
            self.module.fail_json(msg="bwlimit must be zero or greater.")

        self.client.validate_pve_permissions(self.api, "/vms")
        self._migration_api = self.api.nodes(params["node"]).lxc(vmid).migrate
        self._preconditions = self.call_api(
            "Checking migration preconditions for LXC container {0} from node {1} to node {2}".format(
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
            self.module.fail_json(msg="Unable to determine LXC migration preconditions.")

        running = preconditions.get("running")
        if running not in (True, False, 0, 1, "0", "1"):
            self.module.fail_json(msg="Unable to determine whether the LXC container is running.")
        if running in (True, 1, "1") and not params.get("restart"):
            self.module.fail_json(msg="LXC container is running; set restart=true to migrate it.")

        allowed_nodes = preconditions.get("allowed_nodes", preconditions.get("allowed-nodes"))
        if allowed_nodes is not None and not isinstance(allowed_nodes, list):
            self.module.fail_json(msg="Unable to determine nodes allowed for LXC migration.")

        not_allowed_nodes = preconditions.get("not_allowed_nodes", preconditions.get("not-allowed-nodes"))
        if not_allowed_nodes is None:
            not_allowed_nodes = {}
        if not isinstance(not_allowed_nodes, dict):
            self.module.fail_json(msg="Unable to determine nodes disallowed for LXC migration.")

        target = params["target"]
        target_blockers = not_allowed_nodes.get(target)
        if target in not_allowed_nodes or (allowed_nodes is not None and target not in allowed_nodes):
            self.module.fail_json(
                msg="Target node {0} is not allowed for LXC migration.".format(target),
                not_allowed_nodes=target_blockers,
            )

    def perform_action(self):
        """Submit migration and wait for Proxmox's task to complete."""
        params = self.module.params
        options = {"target": params["target"]}
        for parameter, api_parameter in self.API_OPTIONS.items():
            value = params.get(parameter)
            if value is not None:
                options[api_parameter] = int(value) if isinstance(value, bool) else value

        task_upid = self.call_mutation(
            "Migrating LXC container {0} from node {1} to node {2}".format(
                params["vmid"], params["node"], params["target"]
            ),
            self._migration_api.post,
            **options
        )
        if not isinstance(task_upid, str) or not task_upid.startswith("UPID:"):
            self.module.fail_json(msg="LXC migration request did not return a task UPID.")
        self._task_upid = task_upid
        ProxmoxTask(self.module, client=self.client).wait_for_task(task_upid, include_log_on_failure=True)
        return self.get_action_result()

    def get_action_result(self):
        """Return source, target, and container identifiers."""
        params = self.module.params
        return {"node": params["node"], "target": params["target"], "vmid": params["vmid"]}

    def prepare_result(self, changed, resource=None, **values):
        """Include migration preconditions and task UPID when available."""
        result = super().prepare_result(changed, resource, **values)
        if hasattr(self, "_preconditions"):
            result["preconditions"] = self._preconditions
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result


def main():
    """Parse arguments and execute the LXC migration action."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "node": {"type": "str", "required": True},
            "vmid": {"type": "int", "required": True},
            "target": {"type": "str", "required": True},
            "bwlimit": {"type": "float"},
            "restart": {"type": "bool"},
            "target_storage": {"type": "str"},
            "timeout": {"type": "int", "default": 180},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcMigrateModule(module).execute_action_module()


if __name__ == "__main__":
    main()
