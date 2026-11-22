#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Inspect a provider fixture's modern TFA marker without returning legacy keys."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_tfa_provider_fixture_info
short_description: Inspect only the modern TFA marker of a designated integration user
description:
- Role-local read-only fixture preflight. Never returns legacy authentication keys or token data.
version_added: 1.0.0
options:
  userid:
    description: Designated user whose name begins with ansible-.
    type: str
    required: true
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
"""

EXAMPLES = r"""
- name: Inspect the designated provider fixture without returning authentication keys
  proxmox_tfa_provider_fixture_info:
    userid: ansible-provider@pve
  no_log: true
"""

RETURN = r"""
fixture:
  description: Selected userid and a modern_tfa boolean indicating whether the native marker is exactly x.
  type: dict
  returned: always
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_argument_spec,
    proxmox_mutually_exclusive,
    proxmox_required_together,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_info import ProxmoxInfoModule


class ProviderFixtureInfoModule(ProxmoxInfoModule):
    """Return only the marker invariant needed for safe hardware-fixture cleanup."""

    result_key = "fixture"

    def get_info(self):
        userid = self.module.params["userid"]
        if re.fullmatch(r"ansible-[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.client.fail_request("Provider fixture userid must be a designated ansible- user@realm identifier.")
        self.client.validate_pve_permissions(self.api, "/access")
        data = self.call_api("Inspecting provider fixture TFA marker", self.api.get, "access/users/{0}".format(quote(userid, safe="")))
        return {"userid": userid, "modern_tfa": data.get("keys") == "x"}


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"userid": {"type": "str", "required": True}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProviderFixtureInfoModule(module).execute()


if __name__ == "__main__":
    main()
