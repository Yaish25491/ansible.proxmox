# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from abc import abstractmethod

from .proxmox_base import ProxmoxBase


class ProxmoxActionModule(ProxmoxBase):
    """Abstract template for idempotent imperative actions."""

    def is_action_needed(self):
        """Decide whether to perform the action.

        Returns:
            True by default; subclasses may return False for an idempotent no-op.
        """
        return True

    @abstractmethod
    def perform_action(self):
        """Perform the action outside check mode.

        Returns:
            Action result data, or None to use get_action_result instead.
        """
        pass

    def get_action_result(self):
        """Provide result data without performing the action.

        Returns:
            An empty mapping by default. Used for no-ops, check mode, or actions
            that return None.
        """
        return {}

    def execute_action_module(self):
        """Exit with an unchanged, predicted, or performed action result.

        Check mode reports a needed action as changed without performing it.
        """
        if not self.is_action_needed():
            self.exit(changed=False, resource=self.get_action_result())

        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_action_result())

        result = self.perform_action()
        if result is None:
            result = self.get_action_result()
        self.exit(changed=True, resource=result)

    def execute(self):
        """Run the action lifecycle through the common handler entry point."""
        self.execute_action_module()
