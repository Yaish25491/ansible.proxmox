#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate short-lived, role-local TOTP enrollment inputs without API access."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule


def verification_code(secret, timestamp):
    """Return an RFC 6238 SHA-1 six-digit TOTP value for a 30-second period."""
    digest = hmac.new(secret, struct.pack(">Q", int(timestamp) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return "{0:06d}".format(binary % 1000000)


def main():
    module = AnsibleModule(argument_spec={"userid": {"type": "str", "required": True}}, supports_check_mode=True)
    secret = secrets.token_bytes(20)
    encoded = base64.b32encode(secret).decode("ascii").rstrip("=")
    uri = "otpauth://totp/{0}?secret={1}&issuer=AnsibleIntegration".format(quote(module.params["userid"], safe=""), encoded)
    module.exit_json(changed=False, uri=uri, value=verification_code(secret, time.time()))


if __name__ == "__main__":
    main()
