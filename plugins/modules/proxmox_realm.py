#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_realm
short_description: Manage Proxmox VE authentication realms
description:
- Create, reconcile or delete LDAP, Active Directory and OpenID realm configuration.
- Manage provider configuration only; does not synchronize identities, start a login flow or install an identity provider.
version_added: 1.0.0
options:
  state:
    description: Desired presence of the provider configuration. Other reconciliation options are ignored when absent.
    type: str
    choices: [present, absent]
    default: present
  realm:
    description:
    - Realm identifier, 2 through 32 characters; starts with a letter and then letters, digits, dots, underscores or hyphens.
    - Built-in pam and pve realms and the authentication identity's realm cannot be managed.
    type: str
    required: true
  type:
    description: Provider type. Required for creation and immutable afterward.
    type: str
    choices:
    - ldap
    - ad
    - openid
  comment:
    description:
    - Login selector description; at most 4096 characters. Empty string and literal 0 clear the comment, matching Proxmox storage.
    - Omitted leaves existing configuration unchanged.
    type: str
  default:
    description:
    - Select this as the default login realm. Setting true clears the default flag on every other realm.
    - Omitted leaves existing configuration unchanged.
    type: bool
  server1:
    description:
    - Primary LDAP or AD server address; required when creating either provider.
    - Omitted leaves existing configuration unchanged.
    type: str
  server2:
    description:
    - Fallback LDAP or AD server address.
    - Omitted leaves existing configuration unchanged.
    type: str
  port:
    description:
    - LDAP or AD server port, from 1 through 65535. Server defaults follow the selected protocol.
    - Omitted leaves existing configuration unchanged.
    type: int
  base_dn:
    description:
    - LDAP search base. Required when creating LDAP; optional for AD.
    - Omitted leaves existing configuration unchanged.
    type: str
  user_attr:
    description:
    - LDAP username attribute. Required when creating LDAP; optional for AD.
    - Omitted leaves existing configuration unchanged.
    type: str
  domain:
    description:
    - AD domain name; required when creating AD.
    - Omitted leaves existing configuration unchanged.
    type: str
  bind_dn:
    description:
    - LDAP or AD bind distinguished name.
    - Omitted leaves existing configuration unchanged.
    type: str
  mode:
    description:
    - LDAP transport mode. The server defaults to ldap; use ldaps or ldap+starttls for TLS.
    - Omitted leaves existing configuration unchanged.
    - An explicit value removes the deprecated secure flag so it cannot override the selected transport.
    type: str
    choices:
    - ldap
    - ldaps
    - ldap+starttls
  verify:
    description:
    - Whether LDAP or AD verifies the provider certificate. The server default is false.
    - Omitted leaves existing configuration unchanged.
    type: bool
  capath:
    description:
    - CA store path on the Proxmox server for LDAP or AD; server default is /etc/ssl/certs.
    - Omitted leaves existing configuration unchanged.
    type: str
  cert:
    description:
    - Client certificate path on the Proxmox server for LDAP or AD.
    - Omitted leaves existing configuration unchanged.
    type: str
  certkey:
    description:
    - Client private-key file path on the Proxmox server for LDAP or AD, not its contents.
    - Omitted leaves existing configuration unchanged.
    type: str
  sslversion:
    description:
    - TLS version used for LDAP or AD TLS connections.
    - Omitted leaves existing configuration unchanged.
    type: str
    choices:
    - tlsv1
    - tlsv1_1
    - tlsv1_2
    - tlsv1_3
  case_sensitive:
    description:
    - Whether LDAP or AD usernames are case sensitive. The server default is true.
    - Omitted leaves existing configuration unchanged.
    type: bool
  filter:
    description:
    - LDAP user synchronization filter. Stores configuration only; synchronization is never started.
    - Omitted leaves existing configuration unchanged.
    type: str
  sync_attributes:
    description:
    - Comma-separated PVE-field=LDAP-attribute mappings for LDAP or AD synchronization.
    - Omitted leaves existing configuration unchanged.
    type: str
  user_classes:
    description:
    - Comma-separated LDAP user object classes for LDAP or AD synchronization.
    - Omitted leaves existing configuration unchanged.
    type: str
  group_dn:
    description:
    - LDAP group search base for LDAP or AD synchronization.
    - Omitted leaves existing configuration unchanged.
    type: str
  group_name_attr:
    description:
    - LDAP group-name attribute for LDAP or AD synchronization.
    - Omitted leaves existing configuration unchanged.
    type: str
  group_filter:
    description:
    - LDAP group synchronization filter for LDAP or AD.
    - Omitted leaves existing configuration unchanged.
    type: str
  group_classes:
    description:
    - Comma-separated LDAP group object classes for LDAP or AD synchronization.
    - Omitted leaves existing configuration unchanged.
    type: str
  sync_defaults_options:
    description:
    - Proxmox property string of LDAP or AD synchronization defaults. Does not execute synchronization.
    - Supports scope=users|groups|both, enable-new=0|1 and remove-vanished=none or a semicolon-separated set of acl, properties and entry.
    - Replaces the whole defaults property string; field order and implicit server defaults do not cause changes.
    - Deprecated full and purge keys are rejected. Use remove-vanished instead; use remove_properties to clear the property.
    - Omitted leaves existing configuration unchanged.
    type: str
  issuer_url:
    description:
    - OpenID issuer URL; required when creating OpenID.
    - Omitted leaves existing configuration unchanged.
    type: str
  client_id:
    description:
    - OpenID client identifier; required when creating OpenID.
    - Omitted leaves existing configuration unchanged.
    type: str
  client_key:
    description:
    - OpenID client secret. Omitted preserves it; never returned by either module.
    - Omitted leaves existing configuration unchanged.
    type: str
  autocreate:
    description:
    - Allow OpenID login to create users. Server default is false.
    - Omitted leaves existing configuration unchanged.
    type: bool
  username_claim:
    description:
    - OpenID username claim. Creation-only; an existing differing value is rejected.
    - Omitted leaves existing configuration unchanged.
    type: str
  groups_claim:
    description:
    - OpenID claim containing group memberships.
    - Omitted leaves existing configuration unchanged.
    type: str
  groups_autocreate:
    description:
    - Allow OpenID login to create groups. Server default is false.
    - Omitted leaves existing configuration unchanged.
    type: bool
  groups_overwrite:
    description:
    - Allow OpenID login to replace all user groups. Server default is false.
    - Omitted leaves existing configuration unchanged.
    type: bool
  prompt:
    description:
    - OpenID authorization prompt value.
    - Omitted leaves existing configuration unchanged.
    type: str
  scopes:
    description:
    - Space-separated OpenID scopes. Server default is email profile.
    - Omitted leaves existing configuration unchanged.
    type: str
  acr_values:
    description:
    - Space-separated OpenID authentication context class references.
    - Omitted leaves existing configuration unchanged.
    type: str
  query_userinfo:
    description:
    - Query the OpenID userinfo endpoint. Server default is true.
    - Omitted leaves existing configuration unchanged.
    type: bool
  audiences:
    description:
    - Space-separated additional OpenID audiences accepted alongside O(client_id).
    - Requires libpve-access-control 9.1.1 or later on Proxmox VE. Omitted preserves existing configuration.
    - Use O(remove_properties) to remove the property.
    type: str
  password:
    description:
    - LDAP or AD bind password, distinct from O(api_password). Never returned.
    - Controlled by O(update_password). An empty string explicitly writes an empty password.
    type: str
  update_password:
    description:
    - With on_create, O(password) is used only for creation.
    - With always, a supplied password is written on every run and reports changed because it cannot be read back.
    type: str
    choices:
    - on_create
    - always
    default: on_create
  remove_properties:
    description:
    - Optional module option names to remove from an existing realm; omitted and an empty list remove nothing.
    - Required provider fields and the immutable OpenID username claim cannot be removed.
    - Do not supply a value and request its removal together.
    type: list
    elements: str
    default: []
  digest:
    description:
    - Optional expected domains configuration digest. A differing digest fails before a mutation, including in check mode.
    - Updates automatically send the freshly read digest when this option is omitted. The DELETE API has no atomic digest guard.
    type: str
  check_connection:
    description:
    - Check provider bind connectivity when an LDAP or AD configuration write occurs.
    - Omit for OpenID; even false is rejected for that provider. A no-op does not perform a connectivity check.
    type: bool
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads configuration and predicts changes without writes or provider connectivity checks.
notes:
- Requires the Administrator role propagated on /access/realm, including reads and check mode. Native writes require Realm.Allocate.
- LDAP/AD bind passwords are write-only. OpenID client secrets and legacy realm TFA configuration are excluded from all returned records.
- Setting default=true changes the login default for the whole cluster; deleting that realm does not restore the previous default.
- Omission preserves values. Use remove_properties for explicit deletion of optional fields. Other fields are reconciled individually.
- Built-in and controller authentication realms cannot be managed. Realm deletion does not remove realm users, groups or ACLs.
- LDAP/AD synchronization configuration is supported, but synchronization, deprecated secure transport and legacy realm-wide TFA settings are outside this
  module.
- Proxmox can write an LDAP/AD bind credential file before checking connectivity or saving realm configuration.
  A rejected request can therefore leave a partial credential change; inspect and retry deliberately after a failure.
  The module cannot determine whether a failed HTTP request wrote that file and does not infer changed=true for every API error.
- Configuration writes are synchronous. task_timeout is inherited but no asynchronous task is started.
"""

EXAMPLES = r"""
- name: Configure an LDAP realm without contacting its provider
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate
    type: ldap
    server1: ldap.example.com
    base_dn: dc=example,dc=com
    user_attr: uid
    mode: ldaps
    verify: true
    check_connection: false

- name: Remove an optional fallback server
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate
    remove_properties:
      - server2

- name: Configure Active Directory without checking provider connectivity
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: windows
    type: ad
    server1: ad.example.com
    domain: example.com
    mode: ldaps
    verify: true
    check_connection: false

- name: Configure OpenID on a server with libpve-access-control 9.1.1 or later
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate-sso
    type: openid
    issuer_url: https://sso.example.com
    client_id: proxmox
    client_key: '{{ vault_openid_client_key }}'
    audiences: 'proxmox-api https://app.example.com'
  no_log: true

- name: Remove a realm configuration while preserving its identities
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: retired-corporate
    state: absent
"""

RETURN = r"""
realm:
  description:
  - Sanitized realm configuration, or null when absent. Check mode returns predicted values without a new digest.
  - Provider fields retain Proxmox API names, including hyphenated OpenID options; unset fields are omitted.
  - LDAP and AD records can include server1, base_dn, domain, transport and synchronization settings.
  - OpenID records can include issuer-url, client-id, claims, scopes and group settings.
  - Passwords, client-key and legacy TFA configuration are excluded.
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
  returned: always
  type: dict
  contains:
    realm:
      description: Realm identifier.
      returned: when present
      type: str
    type:
      description: Provider type.
      returned: when present
      type: str
    digest:
      description: Domains configuration digest; omitted from changed check-mode predictions.
      returned: when read from the API
      type: str
    comment:
      description: Login selector description; at most 4096 characters. Empty string and literal 0 clear the comment, matching Proxmox storage.
      type: str
      returned: when configured
    default:
      description: Select this as the default login realm. Setting true clears the default flag on every other realm.
      type: int
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
      description: LDAP user synchronization filter. Stores configuration only; synchronization is never started.
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
"""

import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


REALM_RESOURCE_PATH = "access/domains/{0}"
FIELDS = (
    "comment",
    "default",
    "server1",
    "server2",
    "port",
    "base_dn",
    "user_attr",
    "domain",
    "bind_dn",
    "mode",
    "verify",
    "capath",
    "cert",
    "certkey",
    "sslversion",
    "case_sensitive",
    "filter",
    "sync_attributes",
    "user_classes",
    "group_dn",
    "group_name_attr",
    "group_filter",
    "group_classes",
    "sync_defaults_options",
    "issuer_url",
    "client_id",
    "client_key",
    "autocreate",
    "username_claim",
    "groups_claim",
    "groups_autocreate",
    "groups_overwrite",
    "prompt",
    "scopes",
    "acr_values",
    "query_userinfo",
    "audiences",
)
FIELD_SPEC = {
    "comment": {"type": "str"},
    "default": {"type": "bool"},
    "server1": {"type": "str"},
    "server2": {"type": "str"},
    "port": {"type": "int"},
    "base_dn": {"type": "str"},
    "user_attr": {"type": "str"},
    "domain": {"type": "str"},
    "bind_dn": {"type": "str"},
    "mode": {"type": "str", "choices": ["ldap", "ldaps", "ldap+starttls"]},
    "verify": {"type": "bool"},
    "capath": {"type": "str"},
    "cert": {"type": "str"},
    "certkey": {"type": "str", "no_log": False},
    "sslversion": {"type": "str", "choices": ["tlsv1", "tlsv1_1", "tlsv1_2", "tlsv1_3"]},
    "case_sensitive": {"type": "bool"},
    "filter": {"type": "str"},
    "sync_attributes": {"type": "str"},
    "user_classes": {"type": "str"},
    "group_dn": {"type": "str"},
    "group_name_attr": {"type": "str"},
    "group_filter": {"type": "str"},
    "group_classes": {"type": "str"},
    "sync_defaults_options": {"type": "str"},
    "issuer_url": {"type": "str"},
    "client_id": {"type": "str"},
    "client_key": {"type": "str", "no_log": True},
    "autocreate": {"type": "bool"},
    "username_claim": {"type": "str"},
    "groups_claim": {"type": "str"},
    "groups_autocreate": {"type": "bool"},
    "groups_overwrite": {"type": "bool"},
    "prompt": {"type": "str"},
    "scopes": {"type": "str"},
    "acr_values": {"type": "str"},
    "query_userinfo": {"type": "bool"},
    "audiences": {"type": "str"},
}


class ProxmoxRealmModule(ProxmoxResourceModule):
    """Reconcile a single provider configuration without enrolling identities."""

    result_key = "realm"
    ldap_fields = FIELDS[2:24]
    openid_fields = FIELDS[24:]
    api_names = {
        name: name.replace("_", "-")
        for name in (
            "case_sensitive",
            "sync_defaults_options",
            "issuer_url",
            "client_id",
            "client_key",
            "username_claim",
            "groups_claim",
            "groups_autocreate",
            "groups_overwrite",
            "acr_values",
            "query_userinfo",
        )
    }
    defaults = {
        "default": False,
        "verify": False,
        "case-sensitive": True,
        "autocreate": False,
        "groups-autocreate": False,
        "groups-overwrite": False,
        "query-userinfo": True,
        "mode": "ldap",
        "capath": "/etc/ssl/certs",
        "scopes": "email profile",
    }
    required_fields = {"ldap": ("server1", "base_dn", "user_attr"), "ad": ("server1", "domain"), "openid": ("issuer_url", "client_id")}

    def _validate_identifier(self):
        realm = self.module.params["realm"]
        if len(realm) > 32 or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]+", realm) is None:
            self.module.fail_json(
                msg="realm must be 2 through 32 characters, starting with a letter and using letters, digits, dots, underscores or hyphens."
            )
        identity = self.module.params.get("api_user")
        if not identity:
            identity = (self.module.params.get("api_token_id") or "").rpartition("!")[0]
        controller_realm = identity.rsplit("@", 1)[-1]
        if realm in ("pam", "pve", controller_realm):
            self.module.fail_json(msg="Built-in and controller authentication realms cannot be managed.")

    def _validate_fields(self, provider, creating):
        params = self.module.params
        applicable = {"comment", "default"} | set(self.openid_fields if provider == "openid" else self.ldap_fields)
        if provider == "ldap":
            applicable.discard("domain")
        invalid = [name for name in FIELDS if params.get(name) is not None and name not in applicable]
        if invalid:
            self.module.fail_json(msg="Options do not apply to this provider: {0}.".format(", ".join(invalid)))
        if provider == "openid" and (params.get("password") is not None or params.get("check_connection") is not None):
            self.module.fail_json(msg="password and check_connection apply only to LDAP and AD providers.")
        self._validate_required_fields(provider, creating)
        self._validate_removals(provider, applicable, creating)
        self._validate_values()

    def _validate_required_fields(self, provider, creating):
        params = self.module.params
        for name in self.required_fields[provider]:
            if (creating and not params.get(name)) or params.get(name) == "":
                self.module.fail_json(msg="{0} requires a nonempty {1}.".format(provider, name))

    def _validate_removals(self, provider, applicable, creating):
        params = self.module.params
        removals = params.get("remove_properties") or []
        forbidden = set(removals) - (applicable - set(self.required_fields[provider]) - {"username_claim"})
        if forbidden:
            self.module.fail_json(msg="Cannot remove properties: {0}.".format(", ".join(sorted(forbidden))))
        if any(params.get(name) is not None for name in removals):
            self.module.fail_json(msg="A property cannot be supplied and listed in remove_properties together.")
        if creating and removals:
            self.module.fail_json(msg="remove_properties is only supported for an existing realm.")

    def _validate_values(self):
        params = self.module.params
        port = params.get("port")
        if port is not None and not 1 <= port <= 65535:
            self.module.fail_json(msg="port must be from 1 through 65535.")
        limits = dict.fromkeys(
            (
                "server1",
                "server2",
                "base_dn",
                "user_attr",
                "domain",
                "bind_dn",
                "group_dn",
                "group_name_attr",
                "issuer_url",
                "client_id",
                "client_key",
                "groups_claim",
            ),
            256,
        )
        limits.update(comment=4096, filter=2048, group_filter=2048)
        for name, maximum in limits.items():
            value = params.get(name)
            if value is not None and len(value) > maximum:
                self.module.fail_json(msg="{0} exceeds its supported length.".format(name))
        user_attr = params.get("user_attr")
        if user_attr is not None and re.fullmatch(r"\S{2,}", user_attr) is None:
            self.module.fail_json(msg="user_attr must contain at least two non-whitespace characters.")
        self._validate_sync_options()

    def _validate_sync_options(self):
        value = self.module.params.get("sync_defaults_options")
        if value is None:
            return
        fields = value.split(",")
        options = self._parse_formatted_options(value)
        if len(fields) != len(options) or any("=" not in field for field in fields):
            self.module.fail_json(msg="sync_defaults_options requires unique key=value fields; use remove_properties to clear it.")
        if {"full", "purge"} & options.keys():
            self.module.fail_json(msg="Deprecated full and purge sync options are unsupported; use remove-vanished instead.")
        if options.keys() - {"scope", "enable-new", "remove-vanished"}:
            self.module.fail_json(msg="sync_defaults_options only supports scope, enable-new and remove-vanished.")
        if "scope" in options and options["scope"] not in ("users", "groups", "both"):
            self.module.fail_json(msg="Sync scope must be users, groups or both.")
        if "enable-new" in options and options["enable-new"] not in ("0", "1"):
            self.module.fail_json(msg="Sync enable-new must be 0 or 1.")
        removed = options.get("remove-vanished", "none")
        if removed != "none" and not set(removed.split(";")) <= {"acl", "properties", "entry"}:
            self.module.fail_json(msg="Sync remove-vanished must be none or a semicolon-separated set of acl, properties and entry.")

    def _normalized_sync_options(self, value):
        options = self._parse_formatted_options(value)
        # API detail reads can retain deprecated keys while exposing their mapped replacement.
        if "remove-vanished" not in options:
            removed = []
            if options.get("full", "0") != "0":
                removed.extend(("entry", "properties"))
            if options.get("purge", "0") != "0":
                removed.append("acl")
            options["remove-vanished"] = ";".join(removed) or "none"
        options.pop("full", None)
        options.pop("purge", None)
        options.setdefault("enable-new", "1")
        options["remove-vanished"] = ";".join(sorted(set(options["remove-vanished"].split(";"))))
        return options

    def _sync_options_equal(self, current, desired):
        current_options = self._normalized_sync_options(current)
        desired_options = self._normalized_sync_options(desired)
        return current_options.keys() == desired_options.keys() and self._formatted_options_equal(current_options, desired_options)

    def get_resource(self, validate_desired=True):
        self._validate_identifier()
        self.client.validate_pve_permissions(self.api, "/access/realm")
        realm = self.module.params["realm"]
        inventory = self.call_api("Listing authentication realms", self.api.get, "access/domains")
        exists = self.validate_proxmox_resource_exist("realm", realm, lambda: inventory, resource_key="realm", fail_on_missing=False)
        resource = None
        if exists:
            resource = dict(
                self.call_api("Reading authentication realm", self.api.get, REALM_RESOURCE_PATH.format(quote(realm, safe=""))), realm=realm
            )
        if not validate_desired:
            return resource
        if self.module.params.get("state", "present") == "absent":
            self._validate_digest(resource)
            return resource
        provider = resource.get("type") if resource else self.module.params.get("type")
        if provider not in self.required_fields:
            self.module.fail_json(msg="type is required for creation and must be ldap, ad or openid.")
        if resource and self.module.params.get("type") not in (None, provider):
            self.module.fail_json(msg="An existing realm's type cannot be changed.")
        self._validate_fields(provider, creating=not exists)
        self._validate_digest(resource)
        return resource

    def _validate_digest(self, resource):
        expected = self.module.params.get("digest")
        if expected is not None and (resource is None or expected != resource.get("digest")):
            self.module.fail_json(msg="The expected digest does not match current realm configuration.")

    def _desired_parameters(self):
        return {
            self.api_names.get(name, name): int(value) if isinstance(value, bool) else value
            for name in FIELDS
            if (value := self.module.params.get(name)) is not None
        }

    def get_update_parameters(self, resource):
        params = self.module.params
        desired = self._desired_parameters()
        if "username-claim" in desired and resource.get("username-claim") != desired["username-claim"]:
            self.client.fail_request("username_claim cannot be changed on an existing OpenID realm.")
        clear_comment = desired.get("comment") in ("", "0")
        if clear_comment:
            desired.pop("comment")
        sync_options = desired.pop("sync-defaults-options", None)
        updates = self.get_parameter_updates(resource, desired, self.defaults)
        if sync_options is not None and not self._sync_options_equal(resource.get("sync-defaults-options"), sync_options):
            updates["sync-defaults-options"] = sync_options
        removals = [self.api_names.get(name, name) for name in params.get("remove_properties") or []]
        if clear_comment:
            removals.append("comment")
        if params.get("mode") is not None:
            removals.append("secure")
        self._merge_formatted_deletions(updates, [name for name in removals if name in resource])
        if params.get("password") is not None and params.get("update_password", "on_create") == "always":
            updates["password"] = params["password"]
        return updates

    def _write_parameters(self, values):
        parameters = dict(values)
        check = self.module.params.get("check_connection")
        if check is not None:
            parameters["check-connection"] = int(check)
        return parameters

    def _read_after_write(self, operation, provider):
        result = self.get_resource(validate_desired=False)
        if result is None:
            return self.client.fail_request("{0} realm could not be read back.".format(operation))
        if result.get("type") != provider:
            return self.client.fail_request("Realm readback provider type does not match the requested configuration.")
        updates = self.get_update_parameters(result)
        updates.pop("password", None)
        if updates:
            self.client.fail_request("Realm readback does not match the requested configuration.")
        return result

    def create_resource(self):
        params = self.module.params
        values = self._desired_parameters()
        values.update(realm=params["realm"], type=params["type"])
        if params.get("password") is not None:
            values["password"] = params["password"]
        self.call_mutation("Creating authentication realm", self.api.post, "access/domains", **self._write_parameters(values))
        return self._read_after_write("Created", params["type"])

    def update_resource(self, resource, updates):
        parameters = self._write_parameters(updates)
        parameters["digest"] = resource["digest"]
        self.call_mutation("Updating authentication realm", self.api.put, REALM_RESOURCE_PATH.format(quote(resource["realm"], safe="")), **parameters)
        return self._read_after_write("Updated", resource["type"])

    def delete_resource(self, resource):
        self.call_mutation("Deleting authentication realm", self.api.delete, REALM_RESOURCE_PATH.format(quote(resource["realm"], safe="")))
        if self.get_resource(validate_desired=False) is not None:
            self.client.fail_request("Realm deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            return dict(self._desired_parameters(), realm=self.module.params["realm"], type=self.module.params["type"])
        result = super().get_check_mode_resource(operation, resource, updates)
        if result:
            result.pop("digest", None)
        return result

    def prepare_result(self, changed, resource=None, **values):
        if resource is not None:
            resource = {name: value for name, value in resource.items() if name not in ("password", "client-key", "tfa")}
        return super().prepare_result(changed, resource, **values)


def main():
    options = dict(FIELD_SPEC)
    options.update(
        {
            "realm": {"type": "str", "required": True},
            "type": {"type": "str", "choices": ["ldap", "ad", "openid"]},
            "password": {"type": "str", "no_log": True},
            "update_password": {"type": "str", "choices": ["on_create", "always"], "default": "on_create"},
            "remove_properties": {"type": "list", "elements": "str", "default": []},
            "digest": {"type": "str"},
            "check_connection": {"type": "bool"},
        }
    )
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(options),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxRealmModule(module).execute()


if __name__ == "__main__":
    main()
