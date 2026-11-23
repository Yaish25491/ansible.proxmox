#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_template
short_description: Convert an LXC container into a template
description:
  - Converts an LXC container into a Proxmox VE template.
  - A running container can be stopped automatically before conversion.
  - This is a one-way operation and cannot be reversed.
  - This module only performs initial conversion. Use M(ansible.proxmox.proxmox_lxc) to update or delete the template.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  vmid:
    description:
      - Identifier of the LXC container to convert.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  power_cycle:
    description:
      - C(fail) fails if the container is running.
      - C(off) stops a running container before conversion.
    type: str
    choices: [fail, 'off']
    default: fail
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Predicts conversion without changing the container.
seealso:
  - module: ansible.proxmox.proxmox_lxc_template_info
    description: Retrieve LXC templates from a node.
  - module: ansible.proxmox.proxmox_lxc
    description: Update or delete an LXC template after conversion.
notes:
  - Use C(proxmox_lxc) to update or delete the template, just as for a regular container.
  - The container cannot be converted while it has snapshots. Remove all snapshots before conversion.
  - The API identity must have all privileges of the C(Administrator) role, propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Convert a stopped container to a template
  ansible.proxmox.proxmox_lxc_template:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    vmid: 100
    power_cycle: fail
    task_timeout: 600

- name: Update a template after conversion
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    memory: 1024

- name: Delete a template
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    state: absent
"""

RETURN = r"""
template:
  description:
    - Whether the container is a template after the operation.
    - In check mode, represents the predicted template state without modifying Proxmox VE.
  returned: success
  type: bool
  sample: true
task_upid:
  description: UPID of the completed LXC template-conversion task.
  returned: when conversion occurs successfully
  type: str
  sample: 'UPID:pve01:00000001:00000001:00000001:vztemplate:100:root@pam:'
"""

import time

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_power import ProxmoxPower
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class ProxmoxLxcTemplateTask(ProxmoxTask):
    """Discover LXC conversion workers whose UPID is omitted by the POST API."""

    PAGE_SIZE = 50

    def __init__(self, module, client=None):
        """Resolve the exact authenticated task owner using the shared client."""
        super().__init__(module, client=client)
        options = self.client.connection_options()
        self._task_user = options["user"]
        if options.get("token_name"):
            self._task_user += "!" + options["token_name"]

    def matches_conversion(self, upid, node, vmid):
        """Match a conversion UPID by node, worker type, VMID, and identity."""
        if not isinstance(upid, str):
            return False
        parts = upid.split(":")
        if len(parts) != 9 or parts[-1] != "":
            return False
        try:
            task_node = self.task_node(upid)
        except ValueError:
            return False
        return (
            task_node == node and parts[5] == "vztemplate"
            and parts[6] == str(vmid) and parts[7] == self._task_user
        )

    def conversion_upids(self, node, vmid):
        """Read all visible conversion UPIDs, including active and archived tasks."""
        upids = set()
        start = 0
        while True:
            records = self.call_api(
                "Listing LXC container {0} template tasks on node {1}".format(vmid, node),
                self.api.nodes(node).tasks.get,
                source="all", vmid=vmid, typefilter="vztemplate", start=start, limit=self.PAGE_SIZE,
            )
            if not isinstance(records, list):
                self.module.fail_json(msg="Unexpected response while listing LXC template tasks.")
            upids.update(
                record["upid"] for record in records
                if isinstance(record, dict) and self.matches_conversion(record.get("upid"), node, vmid)
            )
            if len(records) < self.PAGE_SIZE:
                return upids
            start += len(records)

    def wait_for_conversion(self, response, node, vmid, previous_upids):
        """Identify the new worker and delegate completion polling to ProxmoxTask."""
        timeout = self.module.params.get("task_timeout")
        timeout = 300 if timeout is None else timeout
        deadline = time.monotonic() + timeout if timeout else None
        upid = response if self.matches_conversion(response, node, vmid) else None
        while upid is None:
            candidates = self.conversion_upids(node, vmid) - previous_upids
            if len(candidates) > 1:
                self.module.fail_json(
                    msg="Multiple new LXC template tasks match this conversion; refusing to select an unrelated task.",
                    changed=True, vmid=vmid, tasks=sorted(candidates),
                )
            if candidates:
                upid = candidates.pop()
                break
            if deadline is not None and time.monotonic() >= deadline:
                self.module.fail_json(
                    msg="Timed out discovering the LXC template-conversion UPID; check task visibility on the node.",
                    changed=True, vmid=vmid, timeout=timeout,
                )
            time.sleep(1)

        remaining = deadline - time.monotonic() if deadline is not None else 0
        if deadline is not None and remaining <= 0:
            self.module.fail_json(
                msg="Timed out waiting for LXC template conversion.", changed=True, task=upid, timeout=timeout,
            )
        self.wait_for_task(upid, node=node, timeout=remaining, include_log_on_failure=True)
        return upid


class ProxmoxLxcTemplateModule(ProxmoxPower, ProxmoxActionModule):
    """Convert one stopped LXC container through the shared action lifecycle."""

    result_key = "template"
    guest_type = "lxc"

    def __init__(self, module, client=None):
        super().__init__(module, client=client, guest_type=self.guest_type)

    @staticmethod
    def _is_template_status(status):
        """Return whether current LXC status marks the container as a template."""
        return isinstance(status, dict) and status.get("template") in (1, "1", True)

    def is_action_needed(self):
        """Read current container status and decide whether conversion is needed."""
        self.client.validate_pve_permissions(self.api, "/vms")
        self._current_status = self.get_status()
        if self._is_template_status(self._current_status):
            return False
        if not isinstance(self._current_status, dict):
            self.module.fail_json(msg="Unable to determine LXC container status before template conversion.")

        status = self._current_status.get("status")
        if status not in ("running", "stopped"):
            self.module.fail_json(msg="Unable to determine LXC container power state before template conversion.")
        self._needs_stop = status != "stopped"
        if self._needs_stop and self.module.params.get("power_cycle", "fail") == "fail":
            self.module.fail_json(
                msg="LXC container {0} must be stopped before converting it to a template; set power_cycle=off to stop it.".format(
                    self.module.params["vmid"]
                )
            )
        return True

    def perform_action(self):
        """Stop the container if requested, convert it, and verify template status."""
        vmid = self.module.params["vmid"]
        node = self.node()
        task = ProxmoxLxcTemplateTask(self.module, client=self.client)
        if self._needs_stop:
            self.set_state("stop")
        previous_upids = task.conversion_upids(node, vmid)
        conversion_result = self.call_mutation(
            "Converting LXC container {0} on node {1} to a template".format(vmid, node),
            self.vm_resource().template.post,
        )
        self._task_upid = task.wait_for_conversion(conversion_result, node, vmid, previous_upids)
        status = self.get_status()
        if not self._is_template_status(status):
            self.module.fail_json(
                msg="LXC container {0} did not reach the template state after conversion.".format(vmid),
                changed=True, task_upid=self._task_upid, status=status,
            )
        return True

    def get_action_result(self):
        """Return the desired template state for no-op and check-mode results."""
        return True

    def prepare_result(self, changed, resource=None, **values):
        """Include the completed conversion task UPID when conversion occurs."""
        result = super().prepare_result(changed, resource, **values)
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result


def main():
    """Parse arguments and convert the requested LXC container."""
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec(
            {
                "vmid": {"type": "int", "required": True},
                "power_cycle": {"type": "str", "choices": ["fail", "off"], "default": "fail"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcTemplateModule(module).execute_action_module()


if __name__ == "__main__":
    main()
