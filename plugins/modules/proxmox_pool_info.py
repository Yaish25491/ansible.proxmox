#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_pool_info
short_description: Retrieve Proxmox VE resource pool information
description:
  - Lists resource pool summaries, or retrieves one pool's configuration and members.
  - Supports nested pool identifiers and filtering a selected pool's members by resource type.
  - Returns an empty list when the selected pool does not exist.
version_added: "1.0.0"
options:
  poolid:
    description:
      - Unique pool identifier, optionally including up to three slash-separated nesting levels.
      - Omit to return all pool summaries without member details.
    type: str
    aliases: [name]
  type:
    description:
      - Filter the selected pool's members by resource type. Requires O(poolid).
      - Omit to return all QEMU VM, LXC container and storage members.
    type: str
    choices: [qemu, lxc, storage]
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Retrieves pool information without modifying the server.
notes:
  - Requires all privileges of the C(Administrator) role propagated on C(/pool), including for selected-pool reads.
  - Permission failures are reported rather than treated as missing pools or empty inventory.
"""

EXAMPLES = r"""
- name: List pool summaries
  ansible.proxmox.proxmox_pool_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"

- name: Retrieve all members of a nested pool
  ansible.proxmox.proxmox_pool_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications/production

- name: Retrieve only storage members
  ansible.proxmox.proxmox_pool_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    poolid: applications/production
    type: storage
"""

RETURN = r"""
pools:
  description: Pool summaries or the selected pool configuration returned by Proxmox.
  returned: always
  type: list
  elements: dict
  contains:
    poolid:
      description: Unique pool identifier.
      type: str
      returned: always
    comment:
      description: Pool description, when configured.
      type: str
      returned: when configured
    members:
      description: Proxmox guest and storage records, optionally filtered by resource type.
      type: list
      elements: dict
      returned: when poolid is supplied and exists
  sample:
    - poolid: applications
      comment: Application workloads
      members: []
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxPoolInfoModule(ProxmoxInfoModule):
    """Retrieve pool summaries and details through the shared info lifecycle."""

    result_key = "pools"

    def get_info(self):
        """Return summaries, selected details, or an empty list for a missing pool."""
        poolid = self.module.params.get("poolid")
        if poolid is not None and not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,2}", poolid):
            self.module.fail_json(msg="poolid must contain one to three slash-separated names using letters, digits, '.', '_' or '-'.")
        self.client.validate_pve_permissions(self.api, "/pool")
        pools = self.call_api("Listing Proxmox pools", self.api.pools.get)
        if poolid is None:
            return pools
        if not self.validate_proxmox_resource_exist(
            "pool", poolid, lambda: pools, resource_key="poolid", fail_on_missing=False,
        ):
            return []
        parameters = {"poolid": poolid}
        if self.module.params.get("type") is not None:
            parameters["type"] = self.module.params["type"]
        return self.call_api("Retrieving pool {0}".format(poolid), self.api.pools.get, **parameters)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "poolid": {"type": "str", "aliases": ["name"]},
            "type": {"type": "str", "choices": ["qemu", "lxc", "storage"]},
        }),
        supports_check_mode=True,
        required_if=[("type", member_type, ["poolid"]) for member_type in ("qemu", "lxc", "storage")],
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxPoolInfoModule(module).execute()


if __name__ == "__main__":
    main()
