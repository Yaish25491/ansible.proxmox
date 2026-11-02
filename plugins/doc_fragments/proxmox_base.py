# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0-or-later.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment:
    """Documentation shared by Proxmox modules."""

    DOCUMENTATION = r"""
options:
  api_host:
    description:
      - Proxmox server hostname or IP address.
      - Uses the E(PROXMOX_HOST) environment variable when omitted.
    type: str
    required: true
notes:
  - All modules connect to the Proxmox API over HTTPS.
  - Modules require the configured identity to have the C(Administrator) role propagated.
  - When using an API token with privilege separation enabled, both the token and its owning user
    must have the required permissions.
  - HTTPS authentication requires either O(api_password), or both O(api_token_id) and O(api_token_secret).
author:
  - Ansible Eco Content Team (@eco-ansible-content)
requirements:
  - proxmoxer >= 2.3.0
  - requests
"""
