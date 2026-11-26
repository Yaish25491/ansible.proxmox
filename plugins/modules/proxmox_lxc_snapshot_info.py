#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc_snapshot_info
short_description: Retrieve LXC container snapshots
description:
  - Returns all snapshot records for an LXC container when O(snapname) is omitted.
  - The complete snapshot list includes the special V(current) entry representing the current
    container configuration.
  - When O(snapname) is provided, retrieves only that snapshot's full configuration.
  - Returns an empty list when the container or requested snapshot does not exist.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: Proxmox VE node containing the container.
    type: str
    required: true
  vmid:
    description:
      - Identifier of the LXC container.
      - Valid values range from 100 to 999999999.
    type: int
    required: true
  snapname:
    description:
      - Retrieve the configuration of this snapshot.
      - When omitted, retrieves all snapshot summaries, including the V(current) entry.
      - Returns an empty list when the container or requested snapshot does not exist.
    type: str
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
seealso:
  - module: ansible.proxmox.proxmox_lxc_snapshot
notes:
  - The API identity must have the C(Administrator) role privileges propagated on C(/vms).
"""

EXAMPLES = r"""
- name: Retrieve all snapshots of an LXC container
  ansible.proxmox.proxmox_lxc_snapshot_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
  register: lxc_snapshots

- name: Retrieve one LXC snapshot configuration
  ansible.proxmox.proxmox_lxc_snapshot_info:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_proxmox_password }}"
    node: pve01
    vmid: 100
    snapname: before_upgrade
  register: lxc_snapshot
"""

RETURN = r"""
snapshots:
  description:
    - All snapshot summaries, including the current container entry, when O(snapname) is omitted.
    - A single snapshot's full configuration, with its name, when O(snapname) is provided.
    - An empty list when the container or requested snapshot does not exist.
  returned: always
  type: list
  elements: dict
  contains:
    description:
      description: Snapshot description.
      returned: when reported by Proxmox
      type: str
    name:
      description: Snapshot identifier; V(current) identifies the current container.
      returned: always
      type: str
    parent:
      description: Parent snapshot identifier.
      returned: when reported by Proxmox
      type: str
    snaptime:
      description: Snapshot creation time as a Unix timestamp.
      returned: when reported by Proxmox
      type: int
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxLxcSnapshotInfoModule(ProxmoxInfoModule):
    """Retrieve all LXC snapshot summaries or one selected configuration."""

    result_key = "snapshots"

    def get_info(self):
        """Return snapshot summaries or the selected configuration for an LXC container."""
        params = self.module.params
        vmid = params["vmid"]
        if not 100 <= vmid <= 999999999:
            self.module.fail_json(msg="VMID must be between 100 and 999999999.")
        node = params["node"]
        self.client.validate_pve_permissions(self.api, "/vms")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        containers = self.call_api(
            "Retrieving LXC containers from node {0}".format(node),
            self.api.nodes(node).lxc.get,
        )
        if not self.validate_proxmox_resource_exist(
            "LXC container", vmid, lambda: containers, resource_key="vmid", fail_on_missing=False
        ):
            return []
        snapshot_api = self.api.nodes(node).lxc(vmid).snapshot
        snapname = params.get("snapname")
        if snapname is not None:
            if not self.validate_proxmox_resource_exist(
                "snapshot",
                snapname,
                snapshot_api.get,
                resource_key="name",
                fail_on_missing=False,
            ):
                return []
            snapshot = self.call_api(
                "Retrieving snapshot {0} of LXC container {1} on node {2}".format(snapname, vmid, node),
                snapshot_api(snapname).config.get,
            )
            return [dict(snapshot, name=snapname)] if isinstance(snapshot, dict) else []
        return self.call_api(
            "Retrieving snapshots of LXC container {0} on node {1}".format(vmid, node),
            snapshot_api.get,
        ) or []


def main():
    """Parse arguments and return the requested LXC snapshot information."""
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int", "required": True},
                "snapname": {"type": "str"},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcSnapshotInfoModule(module).execute_info_module()


if __name__ == "__main__":
    main()
