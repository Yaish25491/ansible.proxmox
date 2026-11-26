#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_realm_info
short_description: Retrieve Proxmox VE authentication realm configuration
description:
- Lists authentication realm summaries or reads one realm's provider configuration independently of mutations.
- A missing selected realm returns an empty list. Omit realm to list login summaries, including built-in realms.
version_added: 1.0.0
options:
  realm:
    description:
    - Optional literal realm identifier, 2 through 32 characters, starting with a letter and using letters, digits, dots, underscores or hyphens.
    - Selecting a realm reads its full configuration and domains digest. Omitting it returns summaries without a digest.
    type: str
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Reads configuration with changed=false and no writes.
notes:
- Requires all privileges of the Administrator role propagated on /access/realm, including reads and check mode.
- Native detail reads require Realm.Allocate or Sys.Audit on /access/realm; the collection retains its stronger Administrator policy.
- Passwords, OpenID client keys and realm-wide TFA API keys are excluded from both summaries and detail records; nonsecret TFA fields are returned.
- Detailed provider fields retain the Proxmox API names, including hyphenated OpenID property names. Unset properties are omitted.
- Does not test connectivity, authenticate users or start identity synchronization.
"""

EXAMPLES = r"""
- name: Read a configured realm
  ansible.proxmox.proxmox_realm_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate
"""

RETURN = r"""
realms:
  description:
  - Sanitized realm summaries, or one detailed provider configuration.
  - LDAP and AD detail records can contain server1, server2, base_dn, domain and transport or synchronization options.
  - OpenID detail records can contain issuer-url, client-id and claim, scope and group options.
  - Secrets are excluded; TFA metadata contains only nonsecret fields. Unset fields are omitted; default is normalized to a boolean.
  - Native keys containing hyphens require bracket notation, for example C(record['issuer-url']).
  - C(case-sensitive) (int, when configured) - Whether LDAP or AD usernames are case sensitive. The server default is true.
  - C(sync-defaults-options) (str, when configured) - Proxmox property string of LDAP or AD synchronization defaults. Does not execute synchronization.
  - C(issuer-url) (str, when configured) - OpenID issuer URL; required when creating OpenID.
  - C(client-id) (str, when configured) - OpenID client identifier; required when creating OpenID.
  - C(username-claim) (str, when configured) - OpenID username claim. Creation-only; an existing differing value is rejected.
  - C(groups-claim) (str, when configured) - OpenID claim containing group memberships.
  - C(groups-autocreate) (int, when configured) - Allow OpenID login to create groups. Server default is false.
  - C(groups-overwrite) (int, when configured) - Allow OpenID login to replace all user groups. Server default is false.
  - C(acr-values) (str, when configured) - Space-separated OpenID authentication context class references.
  - C(query-userinfo) (int, when configured) - Query the OpenID userinfo endpoint. Server default is true.
  type: list
  elements: dict
  returned: always
  contains:
    realm:
      description: Realm identifier.
      type: str
      returned: always
    type:
      description: Provider type, including pam and pve for built-in realms.
      type: str
      returned: always
    digest:
      description: Digest of the cluster domains configuration, suitable as a concurrency guard for updates.
      type: str
      returned: for a selected existing realm
    default:
      description: Whether this realm is the default for login.
      type: bool
      returned: always
    comment:
      description: Login selector description.
      type: str
      returned: when configured
    server1:
      description: Primary LDAP or AD server address; required when creating either provider.
      type: str
      returned: when configured
    server2:
      description: Fallback LDAP or AD server address.
      type: str
      returned: when configured
    port:
      description: LDAP or AD server port, from 1 through 65535. Server defaults follow the selected protocol.
      type: int
      returned: when configured
    base_dn:
      description: LDAP search base. Required when creating LDAP; optional for AD.
      type: str
      returned: when configured
    user_attr:
      description: LDAP username attribute. Required when creating LDAP; optional for AD.
      type: str
      returned: when configured
    domain:
      description: AD domain name; required when creating AD.
      type: str
      returned: when configured
    bind_dn:
      description: LDAP or AD bind distinguished name.
      type: str
      returned: when configured
    mode:
      description: LDAP transport mode. The server defaults to ldap; use ldaps or ldap+starttls for TLS.
      type: str
      returned: when configured
    verify:
      description: Whether LDAP or AD verifies the provider certificate. The server default is false.
      type: int
      returned: when configured
    capath:
      description: CA store path on the Proxmox server for LDAP or AD; server default is /etc/ssl/certs.
      type: str
      returned: when configured
    cert:
      description: Client certificate path on the Proxmox server for LDAP or AD.
      type: str
      returned: when configured
    certkey:
      description: Client private-key file path on the Proxmox server for LDAP or AD, not its contents.
      type: str
      returned: when configured
    sslversion:
      description: TLS version used for LDAP or AD TLS connections.
      type: str
      returned: when configured
    filter:
      description: LDAP user synchronization filter stored in realm configuration.
      type: str
      returned: when configured
    sync_attributes:
      description: Comma-separated PVE-field=LDAP-attribute mappings for LDAP or AD synchronization.
      type: str
      returned: when configured
    user_classes:
      description: Comma-separated LDAP user object classes for LDAP or AD synchronization.
      type: str
      returned: when configured
    group_dn:
      description: LDAP group search base for LDAP or AD synchronization.
      type: str
      returned: when configured
    group_name_attr:
      description: LDAP group-name attribute for LDAP or AD synchronization.
      type: str
      returned: when configured
    group_filter:
      description: LDAP group synchronization filter for LDAP or AD.
      type: str
      returned: when configured
    group_classes:
      description: Comma-separated LDAP group object classes for LDAP or AD synchronization.
      type: str
      returned: when configured
    autocreate:
      description: Allow OpenID login to create users. Server default is false.
      type: int
      returned: when configured
    prompt:
      description: OpenID authorization prompt value.
      type: str
      returned: when configured
    scopes:
      description: Space-separated OpenID scopes. Server default is email profile.
      type: str
      returned: when configured
    audiences:
      description: Additional OpenID audiences; requires libpve-access-control 9.1.1 or later.
      type: str
      returned: when configured
    secure:
      description: Deprecated LDAP/AD LDAPS flag retained in legacy configurations; explicit mode migrates it to the modern transport option.
      type: int
      returned: when configured
    tfa:
      description: Nonsecret TFA property fields type, id, url, digits and step, with string values. Never includes a Yubico API key.
      type: dict
      returned: when configured
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


class ProxmoxRealmInfoModule(ProxmoxInfoModule):
    """Read provider configuration without changing the identity system."""

    result_key = "realms"

    def get_info(self):
        realm = self.module.params.get("realm")
        if realm is not None and (len(realm) > 32 or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]+", realm) is None):
            self.module.fail_json(
                msg="realm must be 2 through 32 characters, starting with a letter and using letters, digits, dots, underscores or hyphens."
            )
        self.client.validate_pve_permissions(self.api, "/access/realm")
        records = self.call_api("Listing authentication realms", self.api.get, "access/domains")
        if realm is not None:
            if not self.validate_proxmox_resource_exist("realm", realm, lambda: records, resource_key="realm", fail_on_missing=False):
                return []
            detail = self.call_api("Reading authentication realm", self.api.get, "access/domains/{0}".format(quote(realm, safe="")))
            records = [dict(detail, realm=realm)]
        result = []
        for record in records:
            sanitized = {name: value for name, value in record.items() if name not in ("password", "client-key", "tfa")}
            if "tfa" in record:
                options = self._parse_formatted_options(record["tfa"])
                if record["tfa"] in ("oath", "yubico"):
                    options = {"type": record["tfa"]}
                sanitized["tfa"] = {name: value for name, value in options.items() if name in ("type", "id", "url", "digits", "step")}
            sanitized["default"] = bool(int(sanitized.get("default") or 0))
            result.append(sanitized)
        return sorted(result, key=lambda item: item["realm"])


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({"realm": {"type": "str"}}),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxRealmInfoModule(module).execute()


if __name__ == "__main__":
    main()
