# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import time

from .proxmox_base import ProxmoxBase


class ProxmoxTask(ProxmoxBase):
    """Wait for PVE or PBS asynchronous tasks identified by a UPID."""

    @staticmethod
    def task_node(upid):
        """Extract the node component from a Proxmox task identifier.

        Args:
            upid: Task identifier beginning with 'UPID:<node>:'.

        Returns:
            The node name embedded in the task identifier.

        Raises:
            ValueError: If the prefix or node component is missing or invalid.
        """
        parts = upid.split(":", 2)
        if len(parts) < 3 or parts[0] != "UPID" or not parts[1]:
            raise ValueError("Invalid Proxmox task UPID")
        return parts[1]

    def wait_for_task(self, upid, node=None, timeout=300, poll_interval=1):
        """Poll a task until it stops, failing for task errors or a timeout.

        Args:
            upid: Proxmox task identifier to poll.
            node: Optional node override; otherwise extracted from upid.
            timeout: Maximum wait in seconds; zero waits indefinitely and negative
                values fail the module. Defaults to 300 seconds.
            poll_interval: Positive number of seconds between status requests.

        Returns:
            The final stopped-task status mapping.

        Raises:
            ValueError: If poll_interval is nonpositive or node extraction fails.
        """
        if timeout < 0:
            self.module.fail_json(msg="timeout must be zero or greater")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")

        node = node or self.task_node(upid)
        deadline = time.monotonic() + timeout if timeout else None
        endpoint = self.api.nodes(node).tasks(upid).status

        while True:
            status = self.call_api("Retrieving Proxmox task status", endpoint.get)
            if status.get("status") == "stopped":
                exit_status = status.get("exitstatus")
                if exit_status and exit_status != "OK":
                    self.module.fail_json(msg="Proxmox task failed", task=upid, task_exit_status=exit_status)
                return status

            if deadline is not None and time.monotonic() >= deadline:
                self.module.fail_json(msg="Timed out waiting for Proxmox task", task=upid, timeout=timeout)
            time.sleep(poll_interval)
