#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_node_time
short_description: Manage the timezone of a Proxmox VE node
description:
  - Configures the timezone on an existing Proxmox VE node.
  - Avoids writing when the configured timezone already matches and reads the settings after an update.
  - The node time API supports changing the timezone only. UTC time and local time are read-only observations.
version_added: "1.0.0"
options:
  node:
    description: Proxmox VE node name.
    type: str
    required: true
  timezone:
    description:
      - Timezone name supported by the node, such as V(Europe/Berlin) or V(Etc/UTC).
      - Names are case-sensitive and compared exactly as returned by Proxmox.
      - Consult C(timedatectl list-timezones) or C(/usr/share/zoneinfo/zone.tab) on the node for supported names.
      - The node validates timezone availability when applying a change. Check mode does not verify that the requested name is installed.
    type: str
    required: true
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Predicts a timezone change without writing settings. Clock observations are omitted when a change is predicted.
notes:
  - Requires all privileges of the C(Administrator) role propagated on C(/nodes/{node}), including in check mode.
  - The timezone applies to the entire node and affects local time presentation and scheduling. Coordinate changes with other users.
  - The module does not set the system clock or configure NTP synchronization.
  - Timezone updates are synchronous; O(task_timeout) does not affect this operation. O(api_timeout) controls each HTTPS request.
  - The API has no concurrency digest for timezone changes. Avoid concurrent timezone updates to the same node.
  - Use M(ansible.proxmox.proxmox_node_info) with C(gather_subset=[time]) for independent reads.
"""

EXAMPLES = r"""
- name: Configure the timezone on a node
  ansible.proxmox.proxmox_node_time:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    timezone: Europe/Berlin

- name: Predict switching a node to UTC
  ansible.proxmox.proxmox_node_time:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    timezone: Etc/UTC
  check_mode: true

- name: Read the timezone and clock observations independently
  ansible.proxmox.proxmox_node_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    gather_subset: [time]
  register: node_time
"""

RETURN = r"""
time:
  description: Time settings returned by Proxmox, or the requested timezone alone when check mode predicts a change.
  returned: always
  type: dict
  contains:
    timezone:
      description: Configured timezone, or the requested timezone in a check-mode prediction.
      type: str
      returned: always
    time:
      description: UTC Unix timestamp observed on the node.
      type: int
      returned: unless check mode predicts a change
    localtime:
      description: Local time expressed as seconds since 1970-01-01 00:00:00 in that timezone, as returned by Proxmox.
      type: int
      returned: unless check mode predicts a change
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_task_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


class ProxmoxNodeTimeModule(ProxmoxResourceModule):
    """Reconcile the singleton timezone through the shared resource lifecycle."""

    result_key = "time"

    def get_resource(self):
        node = self.module.params["node"]
        if not node:
            self.module.fail_json(msg="node must not be empty.")
        timezone = self.module.params["timezone"]
        if not timezone or any(character.isspace() for character in timezone):
            self.module.fail_json(msg="timezone must be a non-empty timezone name without whitespace.")
        self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(node))
        if not self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node", fail_on_missing=False):
            self.client.fail_request("Node {0} does not exist.".format(node))
        return self.call_api("Retrieving time from node {0}".format(node), self.api.nodes(node).time.get)

    def get_update_parameters(self, resource):
        return self.get_parameter_updates(resource, {"timezone": self.module.params["timezone"]}, {})

    def update_resource(self, resource, updates):
        node = self.module.params["node"]
        self.call_mutation("Updating timezone on node {0}".format(node), self.api.nodes(node).time.put, **updates)
        return self.get_resource()

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        # Clock observations cannot be predicted from the controller's timezone database.
        return dict(updates)

    def create_resource(self):
        self.module.fail_json(msg="Time settings can only be updated on an existing node.")

    def delete_resource(self, resource):
        self.module.fail_json(msg="The Proxmox API does not support deleting node time settings.")


def main():
    module = AnsibleModule(
        argument_spec=proxmox_task_argument_spec({
            "node": {"type": "str", "required": True},
            "timezone": {"type": "str", "required": True},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxNodeTimeModule(module).execute()


if __name__ == "__main__":
    main()
