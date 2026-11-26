# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment:
    DOCUMENTATION = r"""
options:
  scope:
    type: str
    choices: [cluster, qemu, lxc]
    default: cluster
    description: Datacenter configuration or configuration local to one QEMU/LXC guest.
  node:
    type: str
    description: Required with guest scopes; omit with cluster scope.
  vmid:
    type: int
    description: Guest ID between 100 and 999999999. Required with guest scopes; omit with cluster scope.
notes:
- Supports Proxmox VE 9. Requires propagated C(Administrator) on C(/) for datacenter scope or C(/vms/{vmid}) for guest scope.
- Reads and validates fresh node/guest discovery. Discovery or API errors fail explicitly instead of implying that configuration is absent.
"""
