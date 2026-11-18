# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared QEMU and LXC resource lifecycle with guest-specific configuration hooks."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from abc import abstractmethod

from .proxmox_power import ProxmoxPower
from .proxmox_resource import ProxmoxResourceModule
from .proxmox_task import ProxmoxTask


class ProxmoxGuestModule(ProxmoxResourceModule):
    """Reconcile guest resources, power policy, tasks, and check-mode results."""

    guest_type = "qemu"
    config_update_method = "post"

    @property
    def power(self):
        """Return a power helper sharing this guest's API client."""
        return ProxmoxPower(self.module, client=self.client, guest_type=self.guest_type)

    def _guest(self):
        """Return the selected guest's proxmoxer resource."""
        return self.power.vm_resource()

    @abstractmethod
    def _creation_parameters(self):
        """Build guest-specific creation parameters.

        Returns:
            The requested API parameter mapping, including the VMID.
        """
        pass

    def _validated_request(self):
        """Validate the guest identifier and power policy.

        Returns:
            The requested lifecycle state and power-cycle policy.
        """
        vmid = self.module.params.get("vmid")
        if vmid is None or not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        state = self.module.params.get("state", "present")
        policy = self.module.params.get("power_cycle", "fail")
        self.power.validate_policy(state, policy)
        return state, policy

    def execute_resource_module(self):
        """Reconcile configuration and power without mutating in check mode."""
        state, policy = self._validated_request()
        resource = self.get_resource()
        if state == "absent":
            self._execute_guest_deletion(resource, policy)
        elif resource is None:
            self._execute_guest_creation(policy)
        else:
            self._execute_guest_update(resource, policy)

    def _execute_guest_deletion(self, resource, policy):
        """Delete a guest after enforcing its stop policy.

        Args:
            resource: Existing guest configuration, or None when absent.
            policy: Requested power-cycle policy.
        """
        if resource is None:
            self.exit(changed=False, resource=None)
        self.power.validate_delete(getattr(self, "_vm_status", None), policy)
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("delete", resource=resource))
        if self.power.should_stop(getattr(self, "_vm_status", None), policy, False):
            self._apply_power_state("stop")
        self.exit(changed=True, resource=self.delete_resource(resource))

    def _execute_guest_creation(self, policy):
        """Create a guest and apply its initial power policy.

        Args:
            policy: Requested power-cycle policy.
        """
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("create"))
        resource = self.create_resource()
        if self.power.should_start("stopped", policy):
            self._apply_power_state("start")
        self.exit(changed=True, resource=resource)

    def _execute_guest_update(self, resource, policy):
        """Reconcile a guest's configuration and requested power state.

        Args:
            resource: Existing guest configuration.
            policy: Requested power-cycle policy.
        """
        updates = self.get_update_parameters(resource)
        status = getattr(self, "_vm_status", None)
        if updates:
            self.power.validate_update(status, policy)
        power_change = self.power.needs_change(status, policy)
        if not updates and not power_change:
            self.exit(changed=False, resource=resource)
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("update", resource=resource, updates=updates))
        if self.power.should_stop(status, policy, bool(updates)):
            self._apply_power_state("stop")
        if updates:
            resource = self.update_resource(resource, updates)
        if self.power.should_start(self._vm_status, policy):
            self._apply_power_state("start")
        self.exit(changed=True, resource=resource)

    def _apply_power_state(self, action):
        """Wait for a power operation and remember its verified state.

        Args:
            action: Guest power operation, such as start or stop.
        """
        self._task_upid, self._vm_status = self.power.set_state(action)

    def _wait_for_task(self, result):
        """Wait for asynchronous writes while retaining the last task identifier.

        Args:
            result: A task UPID or the result of a synchronous API operation.
        """
        if isinstance(result, str) and result.startswith("UPID:"):
            self._task_upid = result
            ProxmoxTask(self.module, client=self.client).wait_for_task(result, include_log_on_failure=True)

    def create_resource(self):
        """Create a guest using its configuration hook.

        Returns:
            The newly created guest configuration.
        """
        return self._create_guest(self._creation_parameters())

    def _create_guest(self, parameters):
        """Submit creation, wait for completion, and read the allocated devices.

        Args:
            parameters: Validated guest-specific API creation parameters.

        Returns:
            The newly created guest configuration.
        """
        node = self.module.params["node"]
        vmid = self.module.params["vmid"]
        label = self.power.guest_label
        result = self.call_mutation(
            "Creating {0} {1} on node {2}".format(label, vmid, node),
            getattr(self.api.nodes(node), self.guest_type).create,
            **parameters
        )
        self._wait_for_task(result)
        return self.call_api(
            "Retrieving created {0} {1} configuration on node {2}".format(label, vmid, node),
            self._guest().config.get,
            current=1,
        )

    def update_resource(self, resource, updates):
        """Write configuration through the API method required by the guest.

        Args:
            resource: Existing guest configuration.
            updates: Changed fields and optional deletions.

        Returns:
            The predicted configuration after applying the updates.
        """
        result = self.call_mutation(
            "Updating {0} {1} on node {2}".format(self.power.guest_label, self.module.params["vmid"], self.module.params["node"]),
            getattr(self._guest().config, self.config_update_method),
            **updates
        )
        self._wait_for_task(result)
        return self.apply_parameter_updates(resource, updates)

    def delete_resource(self, _resource):
        """Remove a guest only after verifying it is stopped.

        Args:
            _resource: Existing guest configuration.

        Returns:
            None after successful deletion.
        """
        self.power.require_stopped(getattr(self, "_vm_status", None), "delete")
        result = self.call_mutation(
            "Deleting {0} {1} from node {2}".format(self.power.guest_label, self.module.params["vmid"], self.module.params["node"]),
            self._guest().delete,
        )
        self._wait_for_task(result)
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        """Predict guest changes without performing writes or power operations.

        Args:
            operation: Lifecycle operation to predict.
            resource: Existing guest configuration, if available.
            updates: Configuration updates to predict.

        Returns:
            The predicted guest configuration, or None for deletion.
        """
        if operation == "create":
            return self._creation_parameters()
        if operation == "delete" and resource is not None:
            self.power.validate_delete(getattr(self, "_vm_status", None), self.module.params.get("power_cycle", "fail"))
        return super().get_check_mode_resource(operation, resource, updates)

    def prepare_result(self, changed, resource=None, **values):
        """Return the guest VMID and last asynchronous task under its result key.

        Args:
            changed: Whether the guest changed or would change.
            resource: Current or predicted guest configuration.
            **values: Additional result fields.

        Returns:
            The completed Ansible result mapping.
        """
        guest = dict(resource or {})
        guest["vmid"] = str(self.module.params["vmid"])
        result = super().prepare_result(changed, resource=guest, **values)
        if getattr(self, "_task_upid", None) is not None:
            result["task_upid"] = self._task_upid
        return result
