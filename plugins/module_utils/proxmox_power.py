# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from .proxmox_base import ProxmoxBase
from .proxmox_task import ProxmoxTask


class ProxmoxPower(ProxmoxBase):
    """Shared guest power-state checks and operations."""

    POWER_STATES = {
        "start": "running",
        "stop": "stopped",
        "shutdown": "stopped",
        "reset": "running",
        "reboot": "running",
        "pause": "paused",
        "hibernate": "suspended",
        "resume": "running",
    }
    LXC_POWER_ACTIONS = ("start", "stop", "shutdown", "reboot")

    def __init__(self, module, client=None, guest_type="qemu"):
        """Select the guest API while preserving QEMU as the default.

        Args:
            module: Ansible module supplying connection and guest parameters.
            client: Optional shared Proxmox client.
            guest_type: Proxmox guest API family, either qemu or lxc.
        """
        super().__init__(module, client=client)
        if guest_type not in ("qemu", "lxc"):
            self.module.fail_json(msg="guest_type must be qemu or lxc.")
        self.guest_type = guest_type
        self.guest_label = "LXC container" if guest_type == "lxc" else "VM"

    def resolve_node(self):
        """Locate a guest by its cluster-wide identifier and API family.

        Returns:
            The node name containing the guest, cached for subsequent calls.
        """
        vmid = self.module.params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        if not getattr(self, "_node", None):
            resources = self.call_api("Locating {0} {1}".format(self.guest_label, vmid), self.api.cluster.resources.get, type="vm")
            for resource in resources:
                if resource.get("type") == self.guest_type and str(resource.get("vmid")) == str(vmid) and resource.get("node"):
                    self._node = resource["node"]
                    break
            else:
                label = "QEMU VM" if self.guest_type == "qemu" else self.guest_label
                self.module.fail_json(msg="{0} {1} does not exist or is not visible to this identity.".format(label, vmid))
        return self._node

    def node(self):
        """Use an explicit node for existing callers, or discover it by VMID.

        Returns:
            The configured or discovered Proxmox node name.
        """
        return self.module.params.get("node") or self.resolve_node()

    def vm_resource(self):
        """Build the API resource for the selected guest.

        Returns:
            The proxmoxer guest resource on its node.
        """
        return getattr(self.api.nodes(self.node()), self.guest_type)(self.module.params["vmid"])

    def get_status(self, return_none_on_404=False):
        """Retrieve the selected guest's current power status with GET.

        Args:
            return_none_on_404: Whether HTTP 404 should return None instead of
                failing the module.

        Returns:
            The current status mapping, or None for a tolerated HTTP 404.
        """
        node = self.node()
        vmid = self.module.params["vmid"]
        return self.call_api(
            "Retrieving {0} {1} status on node {2}".format(self.guest_label, vmid, node),
            self.vm_resource().status.current.get,
            return_none_on_404=return_none_on_404,
        )

    def get_powerstate(self):
        """Normalize guest status while keeping QEMU-specific states separate.

        Returns:
            LXC guests report stopped or running. QEMU additionally supports
            paused and suspended. Unknown responses fail instead of guessing.
        """
        status = self.get_status()
        if isinstance(status, dict):
            if status.get("status") == "stopped":
                return "suspended" if self.guest_type == "qemu" and status.get("lock") == "suspended" else "stopped"
            if status.get("status") == "running":
                if self.guest_type == "qemu" and status.get("qmpstatus") == "paused":
                    return "paused"
                if self.guest_type == "lxc" or status.get("qmpstatus", "running") == "running":
                    return "running"
        self.module.fail_json(msg="Unable to determine {0} power state from current status.".format(self.guest_label))

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
        operation = "resume" if self.guest_type == "qemu" and action == "start" and current == "paused" else action
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
            self.module.fail_json(msg="Unable to verify {0} power state; refusing to {1}.".format(self.guest_label, operation))

    def require_stopped(self, status, operation):
        """Guard operations that cannot be performed on a powered-on VM.

        Args:
            status: Observed VM power status.
            operation: Operation label, such as delete or update.
        """
        if status == "stopped":
            return
        if operation == "delete":
            self.require_known(status, "delete the {0}".format(self.guest_label))
            self.module.fail_json(msg="{0} is {1}; power it off before deleting it.".format(self.guest_label, status))
        else:
            self.require_known(status, "change {0} configuration".format(self.guest_label))
            self.module.fail_json(msg="{0} is {1}; power it off before changing its configuration.".format(self.guest_label, status))

    def validate_update(self, status, policy):
        """Check whether configuration changes are safe under a power policy.

        Args:
            status: Observed VM power status.
            policy: Requested power-cycle policy.
        """
        if status != "stopped":
            self.require_known(status, "change {0} configuration".format(self.guest_label))
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
            self.require_known(status, "delete the {0}".format(self.guest_label))

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
        self.require_known(status, "change {0} power state".format(self.guest_label))
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
        return policy in ("on", "off") and status != "stopped" and (has_updates or policy == "off" or status != "running")

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
        if self.guest_type == "lxc" and action not in self.LXC_POWER_ACTIONS:
            self.module.fail_json(msg="Unsupported LXC container power operation: {0}.".format(action))
        if action not in self.POWER_STATES:
            raise ValueError("Unsupported VM power operation: {0}".format(action))
        vmid = self.module.params["vmid"]
        node = self.node()
        endpoint = "suspend" if action in ("pause", "hibernate") else action
        options = {"todisk": 1} if action == "hibernate" else {}
        result = self.call_mutation(
            "{0} {1} {2} on node {3}".format(action.capitalize(), self.guest_label, vmid, node), getattr(self.vm_resource().status, endpoint).post, **options
        )
        if not isinstance(result, str) or not result.startswith("UPID:"):
            self.client.fail_request("{0} {1} did not return a task UPID.".format(self.guest_label, action))
        task = ProxmoxTask(self.module, client=self.client)
        task.wait_for_task(result, include_log_on_failure=True)
        status = self.get_powerstate()
        expected = self.POWER_STATES[action]
        if status != expected:
            self.client.fail_request("{0} {1} did not reach the {2} state.".format(self.guest_label, vmid, expected))
        return result, expected
