# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from abc import abstractmethod

from .proxmox_base import ProxmoxBase


class ProxmoxInfoModule(ProxmoxBase):
    """Abstract template for read-only modules."""

    @abstractmethod
    def get_info(self):
        """Retrieve information without modifying Proxmox resources.

        Returns:
            API information to expose under this handler's result_key.
        """
        pass

    def execute_info_module(self):
        """Retrieve information and exit with changed set to False."""
        self.exit(changed=False, resource=self.get_info())

    def execute(self):
        """Run the read-only lifecycle through the common handler entry point."""
        self.execute_info_module()
