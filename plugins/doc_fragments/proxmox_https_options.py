# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment:
    """Documentation for Proxmox HTTPS connection options."""

    DOCUMENTATION = r"""
options:
  api_user:
    description:
      - Proxmox account name for HTTPS authentication.
      - Uses the E(PROXMOX_USER) environment variable when omitted.
      - May be omitted for token authentication when O(api_token_id) contains C(user@realm!token).
    type: str
  api_password:
    description:
      - Account password for HTTPS password authentication.
      - Uses the E(PROXMOX_PASSWORD) environment variable when omitted.
    type: str
  api_token_id:
    description:
      - API token name, or a complete C(user@realm!token) identifier.
      - Uses the E(PROXMOX_TOKEN_ID) environment variable when omitted.
    type: str
  api_token_secret:
    description:
      - API token secret for HTTPS token authentication.
      - Uses the E(PROXMOX_TOKEN_SECRET) environment variable when omitted.
    type: str
  api_port:
    description:
      - HTTPS API port.
      - When omitted, proxmoxer selects the service default (8006 for PVE or 8007 for PBS).
      - Uses the E(PROXMOX_PORT) environment variable when omitted.
    type: int
  api_otp:
    description:
      - One-time password used with HTTPS password authentication.
      - Uses the E(PROXMOX_OTP) environment variable when omitted.
    type: str
  validate_certs:
    description:
      - Validate the remote HTTPS certificate.
      - Uses the E(PROXMOX_VALIDATE_CERTS) environment variable when omitted.
    type: bool
    default: true
  ca_path:
    description:
      - CA bundle used to validate HTTPS certificates.
      - Uses the E(PROXMOX_CA_PATH) environment variable when omitted.
      - Valid only when O(validate_certs=true).
    type: path
  api_timeout:
      description:
        - Connection and request timeout in seconds.
        - Uses the E(PROXMOX_API_TIMEOUT) environment variable when omitted.
      type: int
      default: 5
"""
