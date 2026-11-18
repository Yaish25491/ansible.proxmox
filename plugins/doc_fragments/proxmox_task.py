# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment:
    """Asynchronous task options shared by modifying Proxmox modules."""

    DOCUMENTATION = r"""
options:
  task_timeout:
    description:
      - Maximum time in seconds to wait for each asynchronous Proxmox task.
      - When omitted, the task-waiting function uses a timeout of 300 seconds.
      - Set to C(0) to wait indefinitely. Negative values are not allowed.
      - Increase this value for large disks or slow storage when a finite timeout is preferred.
      - Reaching this timeout fails the module but does not cancel the task on the Proxmox server.
      - This is separate from O(api_timeout), which applies to individual HTTP requests.
      - This option does not retry operations rejected because of an incompatible system state.
    type: int
"""
