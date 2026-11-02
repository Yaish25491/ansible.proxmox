# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from .proxmox_base import ProxmoxBase
from .proxmox_task import ProxmoxTask


class ProxmoxPower(ProxmoxBase):
    """Shared QEMU VM power-state checks and start/stop operations."""

    def _vm(self):
        return self.api.nodes(self.module.params["node"]).qemu(self.module.params["vmid"])

    def get_status(self, return_none_on_404=False):
        """Retrieve the VM's current power status, optionally tolerating a missing VM."""
        node = self.module.params["node"]
        vmid = self.module.params["vmid"]
        return self.call_api(
            "Retrieving VM {0} status on node {1}".format(vmid, node),
            self._vm().status.current.get,
            return_none_on_404=return_none_on_404,
        )

    def validate_policy(self, state, policy):
        """Only off and fail may be used while removing a VM."""
        if state == "absent" and policy not in ("off", "fail"):
            self.module.fail_json(msg="power_cycle must be off or fail when state is absent.")

    @staticmethod
    def configuration_options(policy):
        """Include pending settings when comparing live hotplug changes."""
        return {} if policy == "hotplug" else {"current": 1}

    def require_known(self, status, operation):
        """Fail closed if the power state cannot be verified."""
        if status is None:
            self.module.fail_json(msg="Unable to verify VM power state; refusing to {0}.".format(operation))

    def require_stopped(self, status, operation):
        """Guard operations that cannot be performed on a powered-on VM."""
        if status == "stopped":
            return
        if operation == "delete":
            self.require_known(status, "delete the VM")
            self.module.fail_json(msg="VM is {0}; power it off before deleting it.".format(status))
        else:
            self.require_known(status, "change VM configuration")
            self.module.fail_json(msg="VM is {0}; power it off before changing its configuration.".format(status))

    def validate_update(self, status, policy):
        """Check whether configuration changes are safe under this power policy."""
        if status != "stopped":
            self.require_known(status, "change VM configuration")
            if policy == "fail":
                self.require_stopped(status, "update")

    def validate_delete(self, status, policy):
        """Require a stopped VM unless the policy allows stopping it first."""
        if policy == "fail":
            self.require_stopped(status, "delete")
        else:
            self.require_known(status, "delete the VM")

    def needs_change(self, status, policy):
        """Whether the desired on/off policy requires a power transition."""
        if policy not in ("on", "off"):
            return False
        self.require_known(status, "change VM power state")
        return (policy == "on" and status != "running") or (policy == "off" and status != "stopped")

    @staticmethod
    def should_stop(status, policy, has_updates):
        """Stop a powered-on VM before offline changes or when requesting off."""
        return policy in ("on", "off") and status != "stopped" and (
            has_updates or policy == "off" or status != "running"
        )

    @staticmethod
    def should_start(status, policy):
        """Start a stopped VM when requesting on."""
        return policy == "on" and status != "running"

    def set_state(self, action):
        """Start or stop a VM, wait for the UPID, and verify the final state.

        Returns:
            The task UPID and verified VM status, in that order.
        """
        vmid = self.module.params["vmid"]
        node = self.module.params["node"]
        if action not in ("start", "stop"):
            raise ValueError("Unsupported VM power operation: {0}".format(action))
        result = self.call_api(
            "{0} VM {1} on node {2}".format(action.capitalize(), vmid, node),
            getattr(self._vm().status, action).post,
        )
        if not isinstance(result, str) or not result.startswith("UPID:"):
            self.module.fail_json(msg="VM {0} did not return a task UPID.".format(action))
        task = ProxmoxTask(self.module, client=self.client)
        timeout = self.module.params.get("task_timeout")
        if timeout is None:
            task.wait_for_task(result)
        else:
            task.wait_for_task(result, timeout=timeout)
        status = self.get_status()
        expected = "running" if action == "start" else "stopped"
        if not isinstance(status, dict) or status.get("status") != expected:
            self.module.fail_json(msg="VM {0} did not reach the {1} state.".format(vmid, expected))
        return result, expected
