#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_api_connection_info
short_description: Verify API access to Proxmox VE
version_added: "1.0.0"
description:
  - Opens a connection to Proxmox VE using the shared Proxmox collection connection options.
  - Retrieves the API version to verify that authentication and transport work.
  - Use O(api_port) to override the HTTPS port.
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
notes:
  - This module does not perform an C(Administrator) role or ACL propagation check.
  - A successful connection test confirms access to the API version endpoint, not the permissions required to run a specific module.
"""

EXAMPLES = r"""
- name: Check PVE over HTTPS with a username and password
  ansible.proxmox.proxmox_api_connection_info:
    api_host: "{{ proxmox_api_host }}"
    api_user: "{{ proxmox_api_user }}"
    api_password: "{{ vault_proxmox_api_password }}"
    validate_certs: true
    ca_path: "{{ proxmox_ca_path }}"

- name: Check PVE over HTTPS with an API token
  ansible.proxmox.proxmox_api_connection_info:
    api_host: "{{ proxmox_api_host }}"
    api_user: "{{ proxmox_api_user }}"
    api_token_id: "{{ proxmox_api_token_id }}"
    api_token_secret: "{{ vault_proxmox_api_token_secret }}"
    validate_certs: true
    ca_path: "{{ proxmox_ca_path }}"
"""

RETURN = r"""
version:
  description: Version data returned by the Proxmox API.
  returned: success
  type: dict
  sample: {"version": "9.2", "release": "9.2.2", "repoid": "..."}
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProxmoxConnectionInfoModule(ProxmoxInfoModule):
    """Retrieve version information over HTTPS."""

    result_key = "version"

    def get_info(self):
        return self.call_api("Retrieving the Proxmox API version", self.api.version.get)


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec(),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxConnectionInfoModule(module).execute()


if __name__ == "__main__":
    main()
