# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from .proxmox_base import ProxmoxBase
from .proxmox_task import ProxmoxTask


class ProxmoxPower(ProxmoxBase):
    """Shared QEMU VM power-state checks and operations."""

    POWER_STATES = {
        "start": "running", "stop": "stopped", "shutdown": "stopped",
        "reset": "running", "reboot": "running", "pause": "paused",
        "hibernate": "suspended", "resume": "running",
    }

    def resolve_node(self):
        """Locate a QEMU VM by its cluster-wide identifier.

        Returns:
            The node name containing the VM, cached for subsequent calls.
        """
        vmid = self.module.params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        if not getattr(self, "_node", None):
            resources = self.call_api("Locating VM {0}".format(vmid), self.api.cluster.resources.get, type="vm")
            for resource in resources:
                if resource.get("type") == "qemu" and str(resource.get("vmid")) == str(vmid) and resource.get("node"):
                    self._node = resource["node"]
                    break
            else:
                self.module.fail_json(msg="QEMU VM {0} does not exist or is not visible to this identity.".format(vmid))
        return self._node

    def node(self):
        """Use an explicit node for existing callers, or discover it by VMID.

        Returns:
            The configured or discovered Proxmox node name.
        """
        return self.module.params.get("node") or self.resolve_node()

    def vm_resource(self):
        """Build the API resource for the selected QEMU VM.

        Returns:
            The proxmoxer QEMU resource on the VM's node.
        """
        return self.api.nodes(self.node()).qemu(self.module.params["vmid"])

    def get_status(self, return_none_on_404=False):
        """Retrieve the VM's current power status.

        Args:
            return_none_on_404: Whether HTTP 404 should return None instead of
                failing the module.

        Returns:
            The current status mapping, or None for a tolerated HTTP 404.
        """
        node = self.node()
        vmid = self.module.params["vmid"]
        return self.call_api(
            "Retrieving VM {0} status on node {1}".format(vmid, node),
            self.vm_resource().status.current.get,
            return_none_on_404=return_none_on_404,
        )

    def get_powerstate(self):
        """Normalize current QEMU status, preserving pause and disk suspension.

        Returns:
            One of stopped, running, paused, or suspended. Unrecognized status
            responses fail the module rather than returning a guessed state.
        """
        status = self.get_status()
        if isinstance(status, dict):
            if status.get("status") == "stopped":
                return "suspended" if status.get("lock") == "suspended" else "stopped"
            if status.get("status") == "running":
                if status.get("qmpstatus") == "paused":
                    return "paused"
                if status.get("qmpstatus", "running") == "running":
                    return "running"
        self.module.fail_json(msg="Unable to determine VM power state from current status.")

    @staticmethod
    def is_powerstate_action_needed(action, current):
        """Determine whether a power action must execute.

        Args:
            action: Requested power action.
            current: Normalized current VM power state.

        Returns:
            Whether the action changes the state or is a reset or reboot.
        """
        return action in ("reset", "reboot") or current != ProxmoxPower.POWER_STATES[action]

    def perform_powerstate_action(self, action, current):
        """Perform a power action using the endpoint appropriate to the state.

        Args:
            action: Requested power action.
            current: Normalized current VM power state.

        Returns:
            A pair containing the task UPID, or None for a synchronous action,
            and the verified final power state.
        """
        operation = "resume" if action == "start" and current == "paused" else action
        return self.set_state(operation)

    def validate_policy(self, state, policy):
        """Require an off or fail policy while removing a VM.

        Args:
            state: Desired resource lifecycle state.
            policy: Requested power-cycle policy.
        """
        if state == "absent" and policy not in ("off", "fail"):
            self.module.fail_json(msg="power_cycle must be off or fail when state is absent.")

    @staticmethod
    def configuration_options(policy):
        """Select configuration retrieval options for the power policy.

        Args:
            policy: Requested power-cycle policy.

        Returns:
            API options that include pending settings for hotplug comparisons,
            or select the current configuration for other policies.
        """
        return {} if policy == "hotplug" else {"current": 1}

    def require_known(self, status, operation):
        """Fail closed if the power state cannot be verified.

        Args:
            status: Observed power status, or None when unknown.
            operation: Description of the operation requiring a known status.
        """
        if status is None:
            self.module.fail_json(msg="Unable to verify VM power state; refusing to {0}.".format(operation))

    def require_stopped(self, status, operation):
        """Guard operations that cannot be performed on a powered-on VM.

        Args:
            status: Observed VM power status.
            operation: Operation label, such as delete or update.
        """
        if status == "stopped":
            return
        if operation == "delete":
            self.require_known(status, "delete the VM")
            self.module.fail_json(msg="VM is {0}; power it off before deleting it.".format(status))
        else:
            self.require_known(status, "change VM configuration")
            self.module.fail_json(msg="VM is {0}; power it off before changing its configuration.".format(status))

    def validate_update(self, status, policy):
        """Check whether configuration changes are safe under a power policy.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.
        """
        if status != "stopped":
            self.require_known(status, "change VM configuration")
            if policy == "fail":
                self.require_stopped(status, "update")

    def validate_delete(self, status, policy):
        """Require a stopped VM unless the policy allows stopping it first.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.
        """
        if policy == "fail":
            self.require_stopped(status, "delete")
        else:
            self.require_known(status, "delete the VM")

    def needs_change(self, status, policy):
        """Determine whether an on/off policy requires a power transition.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.

        Returns:
            Whether the current status differs from the requested on/off state.
        """
        if policy not in ("on", "off"):
            return False
        self.require_known(status, "change VM power state")
        return (policy == "on" and status != "running") or (policy == "off" and status != "stopped")

    @staticmethod
    def should_stop(status, policy, has_updates):
        """Determine whether a VM must stop before applying configuration.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.
            has_updates: Whether configuration changes are pending.

        Returns:
            Whether the policy requires stopping the VM before proceeding.
        """
        return policy in ("on", "off") and status != "stopped" and (
            has_updates or policy == "off" or status != "running"
        )

    @staticmethod
    def should_start(status, policy):
        """Determine whether the on policy requires starting the VM.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.

        Returns:
            Whether the VM is not running and the policy requests on.
        """
        return policy == "on" and status != "running"

    def set_state(self, action):
        """Perform a power action, wait for asynchronous tasks, and verify the state.

        Args:
            action: Supported power action to perform on the selected VM.

        Returns:
            A pair containing the task UPID and verified normalized VM power state.

        Raises:
            ValueError: If action is not a supported power operation.
        """
        vmid = self.module.params["vmid"]
        node = self.node()
        if action not in self.POWER_STATES:
            raise ValueError("Unsupported VM power operation: {0}".format(action))
        endpoint = "suspend" if action in ("pause", "hibernate") else action
        options = {"todisk": 1} if action == "hibernate" else {}
        result = self.call_api(
            "{0} VM {1} on node {2}".format(action.capitalize(), vmid, node),
            getattr(self.vm_resource().status, endpoint).post,
            **options
        )
        if not isinstance(result, str) or not result.startswith("UPID:"):
            self.module.fail_json(msg="VM {0} did not return a task UPID.".format(action))
        task = ProxmoxTask(self.module, client=self.client)
        task.wait_for_task(result)
        status = self.get_powerstate()
        expected = self.POWER_STATES[action]
        if status != expected:
            self.module.fail_json(msg="VM {0} did not reach the {1} state.".format(vmid, expected))
        return result, expected
