#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_pool_test_api
short_description: Prepare API fixtures for the paired pool integration target
description:
  - Test-only API transport for owned container and template fixtures.
  - Mutation requests wait for any returned Proxmox task to complete.
author:
  - Ansible Eco Content Team (@eco-ansible-content)
options:
  path:
    description: Relative Proxmox API path.
    type: str
    required: true
  method:
    description: API method; only reads are supported in check mode.
    type: str
    choices: [get, post, delete]
    default: get
  data:
    description: API parameters.
    type: dict
    default: {}
  task_timeout:
    description: Maximum seconds to wait for an asynchronous fixture operation.
    type: int
    default: 300
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
"""

EXAMPLES = r"""
- name: Discover allocated guest IDs before claiming fixtures
  proxmox_pool_test_api:
    api_host: "{{ proxmox_api_host }}"
    api_token_id: "{{ proxmox_api_token_id }}"
    api_token_secret: "{{ proxmox_api_token_secret }}"
    path: cluster/resources
    data:
      type: vm
"""

RETURN = r"""
data:
  description: API response.
  returned: always
  type: raw
task:
  description: Final task status when the response contains a UPID.
  returned: always
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_base import ProxmoxBase
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


class PoolTestApi(ProxmoxBase):
    """Use the production transport and task helpers for test fixtures."""

    result_key = "data"

    def execute(self):
        """Read or change a fixture, waiting for asynchronous completion."""
        method = self.module.params["method"]
        if self.module.check_mode and method != "get":
            self.module.fail_json(msg="Fixture writes must not run in check mode.")
        request = self.call_api if method == "get" else self.call_mutation
        response = request(
            "Preparing pool integration fixture",
            getattr(self.api, method),
            self.module.params["path"],
            **self.module.params["data"]
        )
        task = {}
        if isinstance(response, str) and response.startswith("UPID:"):
            task = ProxmoxTask(self.module, client=self.client).wait_for_task(response, timeout=self.module.params["task_timeout"])
        self.exit(changed=method != "get", resource=response, task=task)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "path": {"type": "str", "required": True},
            "method": {"type": "str", "choices": ["get", "post", "delete"], "default": "get"},
            "data": {"type": "dict", "default": {}},
            "task_timeout": {"type": "int", "default": 300},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    PoolTestApi(module).execute()


if __name__ == "__main__":
    main()
