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
- Create, reconcile or delete realm configuration, manage protected built-in settings with explicit opt-in, and synchronize LDAP or AD identities.
- Exposes the authentication-domain API, including native synchronization options, previews and asynchronous task results.
version_added: 1.0.0
options:
  state:
    description:
    - C(present) reconciles provider configuration. C(absent) deletes a custom realm; other reconciliation options are ignored.
    - C(synced) runs synchronization for an existing LDAP or AD realm without reconciling configuration in the same task.
    - C(reset) invokes native deletion of persisted PAM/PVE settings; those built-in realms are always synthesized by Proxmox and remain present.
    - Sync and reset are explicit operations and report changed when requested, even if the resulting data is identical;
      a native dry-run reports unchanged. Check mode predicts operations without starting tasks or contacting the directory.
    type: str
    choices: [present, absent, synced, reset]
    default: present
  realm:
    description:
    - Realm identifier, 2 through 32 characters; starts with a letter and then letters, digits, dots, underscores or hyphens.
    - Built-in pam and pve realms and the authentication identity's realm require O(allow_protected=true).
    type: str
    required: true
  type:
    description: Provider type. Required for creation and immutable afterward. PAM and PVE types can only be updated on their built-in realms.
    type: str
    choices:
    - ldap
    - ad
    - openid
    - pam
    - pve
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
    - LDAP user synchronization filter used when O(state=synced).
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
    - Proxmox property string of LDAP or AD synchronization defaults used when O(state=synced).
    - Supports scope=users|groups|both, enable-new=0|1 and remove-vanished=none or a semicolon-separated set of acl, properties and entry.
    - Replaces the whole defaults property string; field order and implicit server defaults do not cause changes.
    - Native deprecated full and purge flags are accepted as 0 or 1. Prefer remove-vanished; use remove_properties to clear the property.
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
    - For LDAP or AD, password removes the stored bind credential through the native API. Its existence cannot be read;
      an explicit password removal therefore requests a write and reports changed on every invocation.
    - Do not supply a value and request its removal together.
    - Removing a built-in realm's comment restores the native synthesized comment; that effective default is a no-op on subsequent runs.
    type: list
    elements: str
    default: []
  digest:
    description:
    - Optional expected domains configuration digest. A differing digest fails before a mutation, including in check mode.
    - Configuration updates send the freshly read digest when omitted. Deletion, reset and synchronization have only a preflight digest check;
      those native operations have no atomic digest parameter.
    type: str
  check_connection:
    description:
    - Check provider bind connectivity when an LDAP or AD configuration write occurs.
    - Omit for OpenID, PAM and PVE; even false is rejected for those providers. A no-op does not perform a connectivity check.
    type: bool
  secure:
    description:
    - Native deprecated LDAP or AD LDAPS flag. Supported for compatibility; mode is preferred and takes precedence when both are stored.
    - Supplying mode alone removes a legacy secure flag. Supplying both retains native mode precedence.
    type: bool
  tfa:
    description:
    - Native realm-wide TFA property string for LDAP, AD, PAM or PVE, at most 128 characters.
    - Requires type=oath or type=yubico. Supports id, key, url, digits from 6 through 8, and step of at least 10 seconds.
    - The property is authoritative; omission preserves it and remove_properties with tfa clears it.
    - Yubico API keys are sensitive. Results include only nonsecret TFA metadata, and property ordering/defaults are compared semantically.
    type: str
  allow_protected:
    description:
    - Explicitly allow supported operations on PAM/PVE built-ins or the authentication identity's realm.
    - Changing authentication settings or synchronizing the controller realm can affect access. Built-in creation and permanent removal remain impossible.
    type: bool
    default: false
  wait:
    description:
    - With state=synced, wait for the native worker to finish. Omission waits; false returns its UPID immediately.
    - Task timeout does not cancel the worker. Not accepted for other states.
    type: bool
  sync_options:
    description:
    - Native per-run LDAP or AD synchronization options, accepted only with state=synced.
    - Omitted options use the server's realm defaults and schema defaults. Supply scope here or configure it in sync_defaults_options.
    type: dict
    suboptions:
      scope:
        description: Synchronize users, groups, or both.
        type: str
        choices: [users, groups, both]
      enable_new:
        description: Enable newly imported users. Omission uses the realm default or the native default true.
        type: bool
      remove_vanished:
        description:
        - Native semicolon-separated removal options acl, properties and entry, or none. Omission uses native defaults.
        - These options can remove users, groups, local properties and ACL assignments absent from the directory.
        type: str
      full:
        description: Native deprecated full-sync alias; passed through to the server. Prefer remove_vanished.
        type: bool
      purge:
        description: Native deprecated ACL-purge alias; passed through to the server. Prefer remove_vanished.
        type: bool
      dry_run:
        description:
        - Run the native preview worker without writing users, groups or ACLs. The operation still contacts the directory and creates a task record.
        - Ansible check mode validates and predicts this request without submitting it.
        type: bool
        default: false
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads and validates inputs and predicts configuration changes or operation submission without writes, task submission or directory contact.
notes:
- Requires Administrator privileges propagated on /access/realm, including reads and check mode. Native configuration writes require Realm.Allocate.
- Synchronization additionally requires propagated Administrator privileges on /access/groups and /access/realm/REALM;
  native sync requires User.Modify and Realm.AllocateUser respectively.
- LDAP/AD bind passwords are write-only. OpenID client secrets and realm TFA keys are excluded from all returned records.
- Setting default=true changes the login default for the whole cluster; deleting that realm does not restore the previous default.
- Omission preserves values. Use remove_properties for explicit deletion of optional fields. Other fields are reconciled individually.
- Built-in and controller realms require explicit allow_protected opt-in. Realm deletion does not remove realm users, groups or ACLs.
- Native synchronization supports LDAP and AD only; OpenID, PAM and PVE do not provide a synchronization operation.
- Repeated real synchronization submits another native job. Changed indicates job submission, not an independently calculated per-entry difference.
  Native dry-run is available explicitly; check mode cannot infer directory-side differences without executing that operation.
- Proxmox can write an LDAP/AD bind credential file before checking connectivity or saving realm configuration.
  A rejected request can therefore leave a partial credential change; inspect and retry deliberately after a failure.
  The module cannot determine whether a failed HTTP request wrote that file and does not infer changed=true for every API error.
- Configuration writes are synchronous. Synchronization uses the shared asynchronous task waiter and task_timeout, with a default of 300 seconds;
  zero waits indefinitely and timeout does not cancel server work.
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

- name: Synchronize directory users and groups into an existing realm
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate
    state: synced
    sync_options:
      scope: both
      enable_new: false
      remove_vanished: none
    task_timeout: 600

- name: Preview synchronization using the native dry-run worker
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: corporate
    state: synced
    sync_options:
      scope: both
      dry_run: true

- name: Update protected built-in realm settings explicitly
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: pam
    allow_protected: true
    comment: Linux accounts

- name: Reset persisted built-in realm settings to Proxmox defaults
  ansible.proxmox.proxmox_realm:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: pam
    state: reset
    allow_protected: true
"""

RETURN = r"""
realm:
  description:
  - Sanitized realm configuration, or null when absent. Check mode returns predicted values without a new digest.
  - Provider fields retain Proxmox API names, including hyphenated OpenID options; unset fields are omitted.
  - LDAP and AD records can include server1, base_dn, domain, transport and synchronization settings.
  - OpenID records can include issuer-url, client-id, claims, scopes and group settings.
  - Passwords, client-key and TFA API keys are excluded; safe TFA metadata is returned as a dictionary.
  - Native keys containing hyphens require bracket notation, for example C(record['issuer-url']).
  - C(case-sensitive) (int, when configured) - Whether LDAP or AD usernames are case sensitive. The server default is true.
  - C(sync-defaults-options) (str, when configured) - Proxmox property string of LDAP or AD synchronization defaults used when O(state=synced).
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
      description: Native legacy LDAP or AD LDAPS flag when configured.
      type: int
      returned: when configured
    tfa:
      description: Nonsecret native TFA property fields type, id, url, digits and step, with string values. The key field is never returned.
      type: dict
      returned: when configured
sync:
  description: Requested synchronization options and preflight-resolved scope. Native hyphenated option names remain dictionary keys under options.
  type: dict
  returned: with state=synced
  contains:
    scope:
      description: Users, groups or both scope resolved from the request or preflight realm defaults. The server resolves omitted flags again at submission.
      type: str
    dry_run:
      description: Whether the requested worker is a native no-write preview.
      type: bool
    options:
      description: Native API parameters sent to the worker, excluding unspecified options so server defaults remain authoritative.
      type: dict
task_upid:
  description: Native worker UPID, usable to inspect an asynchronous request. Never invented in check mode.
  type: str
  returned: after synchronization submission
task_status:
  description: Final status and exit status from the shared task waiter.
  type: dict
  returned: when synchronization was submitted and wait was not false
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
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask


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
    "secure",
    "tfa",
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
    "secure": {"type": "bool"},
    "tfa": {"type": "str", "no_log": True},
}


class ProxmoxRealmModule(ProxmoxResourceModule):
    """Manage realm configuration and explicitly requested native operations."""

    result_key = "realm"
    ldap_fields = FIELDS[2:24] + ("secure", "tfa")
    openid_fields = FIELDS[24:-2]
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
        "secure": False,
    }
    required_fields = {
        "ldap": ("server1", "base_dn", "user_attr"), "ad": ("server1", "domain"),
        "openid": ("issuer_url", "client_id"), "pam": (), "pve": (),
    }
    builtin_comments = {"pam": "Linux PAM standard authentication", "pve": "Proxmox VE authentication server"}
    sync_api_names = {"enable_new": "enable-new", "remove_vanished": "remove-vanished", "dry_run": "dry-run"}

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
        if realm in ("pam", "pve", controller_realm) and not self.module.params.get("allow_protected", False):
            self.module.fail_json(msg="Built-in and controller authentication realms cannot be managed without allow_protected=true.")
        if realm in self.builtin_comments and self.module.params.get("state", "present") == "absent":
            self.module.fail_json(msg="Proxmox always synthesizes built-in realms; use state=reset to reset their persisted configuration.")

    def _validate_fields(self, provider, creating):
        params = self.module.params
        applicable = {"comment", "default"}
        if provider in ("ldap", "ad"):
            applicable.update(self.ldap_fields)
        elif provider == "openid":
            applicable.update(self.openid_fields)
        else:
            applicable.add("tfa")
        if provider == "ldap":
            applicable.discard("domain")
        invalid = [name for name in FIELDS if params.get(name) is not None and name not in applicable]
        if invalid:
            self.module.fail_json(msg="Options do not apply to this provider: {0}.".format(", ".join(invalid)))
        if provider not in ("ldap", "ad") and (params.get("password") is not None or params.get("check_connection") is not None):
            self.module.fail_json(msg="password and check_connection apply only to LDAP and AD providers.")
        self._validate_required_fields(provider, creating)
        if provider in ("ldap", "ad"):
            applicable.add("password")
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
        self._validate_tfa()

    def _validate_sync_options(self):
        value = self.module.params.get("sync_defaults_options")
        if value is None:
            return
        fields = value.split(",")
        options = self._parse_formatted_options(value)
        if len(fields) != len(options) or any("=" not in field for field in fields):
            self.module.fail_json(msg="sync_defaults_options requires unique key=value fields; use remove_properties to clear it.")
        if options.keys() - {"scope", "enable-new", "remove-vanished", "full", "purge"}:
            self.module.fail_json(msg="sync_defaults_options contains an unsupported native synchronization option.")
        for name in ("full", "purge"):
            if name in options and options[name] not in ("0", "1"):
                self.module.fail_json(msg="Legacy sync flags must be 0 or 1.")
        if "scope" in options and options["scope"] not in ("users", "groups", "both"):
            self.module.fail_json(msg="Sync scope must be users, groups or both.")
        if "enable-new" in options and options["enable-new"] not in ("0", "1"):
            self.module.fail_json(msg="Sync enable-new must be 0 or 1.")
        removed = options.get("remove-vanished", "none")
        if removed != "none" and not set(removed.split(";")) <= {"acl", "properties", "entry"}:
            self.module.fail_json(msg="Sync remove-vanished must be none or a semicolon-separated set of acl, properties and entry.")

    def _validate_tfa(self):
        value = self.module.params.get("tfa")
        if value is None:
            return
        options = self._parse_formatted_options(value)
        if options.get("key") and hasattr(self.module, "no_log_values"):
            self.module.no_log_values.add(options["key"])
        if len(value) > 128 or len(value.split(",")) != len(options) or any("=" not in item for item in value.split(",")):
            self.module.fail_json(msg="tfa requires a native property string of unique key=value fields, at most 128 characters.")
        if options.keys() - {"type", "id", "key", "url", "digits", "step"} or options.get("type") not in ("oath", "yubico"):
            self.module.fail_json(msg="tfa requires type=oath or type=yubico and only native TFA properties.")
        for name, minimum, maximum in (("digits", 6, 8), ("step", 10, None)):
            if name not in options:
                continue
            if not options[name].isdigit() or int(options[name]) < minimum or (maximum is not None and int(options[name]) > maximum):
                self.module.fail_json(msg="tfa {0} is outside the native supported range.".format(name))

    def _tfa_options_equal(self, current, desired):
        def normalized(value):
            options = self._parse_formatted_options(value)
            options.setdefault("digits", "6")
            options.setdefault("step", "30")
            return options
        return self._formatted_options_equal(normalized(current), normalized(desired))

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
        state = self.module.params.get("state", "present")
        if state in ("synced", "reset"):
            if resource is None:
                self.client.fail_request("The realm must already exist for the requested operation.")
                return None
            if self.module.params.get("type") not in (None, resource.get("type")):
                self.module.fail_json(msg="The requested provider type does not match the existing realm.")
            self._validate_digest(resource)
            return resource
        if state == "absent":
            self._validate_digest(resource)
            return resource
        provider = resource.get("type") if resource else self.module.params.get("type")
        if provider not in self.required_fields:
            self.module.fail_json(msg="type is required for creation and must be a native provider type.")
        if resource and self.module.params.get("type") not in (None, provider):
            self.module.fail_json(msg="An existing realm's type cannot be changed.")
        if not exists and provider in self.builtin_comments:
            self.module.fail_json(msg="Proxmox does not allow creating built-in PAM/PVE provider types.")
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

    def _validate_username_claim_update(self, resource, desired):
        if "username-claim" in desired and resource.get("username-claim") != desired["username-claim"]:
            self.client.fail_request("username_claim cannot be changed on an existing OpenID realm.")

    def _add_realm_option_updates(self, resource, updates, sync_options, tfa):
        if tfa is not None and not self._tfa_options_equal(resource.get("tfa"), tfa):
            updates["tfa"] = tfa
        if sync_options is not None and not self._sync_options_equal(resource.get("sync-defaults-options"), sync_options):
            updates["sync-defaults-options"] = sync_options

    def _get_update_removals(self, resource, params, clear_comment):
        removals = [self.api_names.get(name, name) for name in params.get("remove_properties") or []]
        if clear_comment:
            removals.append("comment")
        if params.get("mode") is not None and params.get("secure") is None:
            removals.append("secure")
        if resource.get("comment") == self.builtin_comments.get(resource.get("realm")):
            removals = [name for name in removals if name != "comment"]
        return [name for name in removals if name in resource or name == "password"]

    def get_update_parameters(self, resource):
        params = self.module.params
        desired = self._desired_parameters()
        self._validate_username_claim_update(resource, desired)
        clear_comment = desired.get("comment") in ("", "0")
        if clear_comment:
            desired.pop("comment")
        sync_options = desired.pop("sync-defaults-options", None)
        tfa = desired.pop("tfa", None)
        defaults = dict(self.defaults)
        if params.get("secure") is not None:
            defaults.pop("mode", None)
        updates = self.get_parameter_updates(resource, desired, defaults)
        self._add_realm_option_updates(resource, updates, sync_options, tfa)
        removals = self._get_update_removals(resource, params, clear_comment)
        self._merge_formatted_deletions(updates, removals)
        self._add_password_update(params, updates)
        return updates

    def _write_parameters(self, values):
        parameters = dict(values)
        check = self.module.params.get("check_connection")
        if check is not None:
            parameters["check-connection"] = int(check)
        return parameters

    def _add_password_update(self, params, updates):
        if params.get("password") is not None and params.get("update_password", "on_create") == "always":
            updates["password"] = params["password"]

    def _read_after_write(self, operation, provider):
        result = self.get_resource(validate_desired=False)
        if result is None:
            return self.client.fail_request("{0} realm could not be read back.".format(operation))
        if result.get("type") != provider:
            return self.client.fail_request("Realm readback provider type does not match the requested configuration.")
        updates = self.get_update_parameters(result)
        updates.pop("password", None)
        pending_deletions = [name for name in updates.get("delete", "").split(",") if name and name != "password"]
        if pending_deletions:
            updates["delete"] = ",".join(pending_deletions)
        else:
            updates.pop("delete", None)
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

    def _validate_operation_options(self):
        params = self.module.params
        state = params.get("state", "present")
        if state not in ("synced", "reset"):
            if params.get("sync_options") is not None or params.get("wait") is not None:
                self.module.fail_json(msg="sync_options and wait apply only to state=synced.")
            return
        supplied = [name for name in FIELDS + ("password", "check_connection") if params.get(name) is not None]
        if supplied or params.get("remove_properties") or params.get("update_password", "on_create") != "on_create":
            self.module.fail_json(msg="Operation states do not reconcile configuration; use state=present in a separate task.")
        if state == "reset" and (params.get("sync_options") is not None or params.get("wait") is not None):
            self.module.fail_json(msg="sync_options and wait apply only to state=synced.")

    def _sync_parameters(self, resource):
        if resource.get("type") not in ("ldap", "ad"):
            self.module.fail_json(msg="Only LDAP and AD realms support native synchronization.")
        supplied = self.module.params.get("sync_options") or {}
        defaults = self._parse_formatted_options(resource.get("sync-defaults-options"))
        scope = supplied.get("scope") or defaults.get("scope")
        if scope not in ("users", "groups", "both"):
            self.module.fail_json(msg="Synchronization scope must be supplied or configured in the realm defaults.")
        removed = supplied.get("remove_vanished")
        if removed is not None and (removed != "none" and (not removed or not set(removed.split(";")) <= {"acl", "properties", "entry"})):
            self.module.fail_json(msg="sync_options.remove_vanished must be none or a semicolon-separated set of acl, properties and entry.")
        parameters = {
            self.sync_api_names.get(name, name): int(value) if isinstance(value, bool) else value
            for name, value in supplied.items() if value is not None
        }
        parameters["dry-run"] = int(supplied.get("dry_run") or False)
        return parameters, scope

    def _execute_sync(self, resource):
        parameters, scope = self._sync_parameters(resource)
        dry_run = bool(parameters["dry-run"])
        realm = resource["realm"]
        for path in ("/access/realm/{0}".format(realm), "/access/groups"):
            self.client.validate_pve_permissions(self.api, path)
        values = {"sync": {"scope": scope, "dry_run": dry_run, "options": parameters}}
        if self.module.check_mode:
            self.exit(changed=not dry_run, resource=resource, **values)
        path = REALM_RESOURCE_PATH.format(quote(realm, safe="")) + "/sync"
        request = self.call_api if dry_run else self.call_mutation
        upid = request("Starting realm synchronization", self.api.post, path, **parameters)
        if not isinstance(upid, str) or not upid.startswith("UPID:"):
            self.client.fail_request("Realm synchronization did not return a valid task UPID.")
        try:
            ProxmoxTask.task_node(upid)
        except ValueError:
            self.client.fail_request("Realm synchronization did not return a valid task UPID.")
        values["task_upid"] = upid
        if self.module.params.get("wait") is not False:
            values["task_status"] = ProxmoxTask(self.module, client=self.client).wait_for_task(upid)
        for path in ("/access/realm/{0}".format(realm), "/access/groups"):
            self.client.validate_pve_permissions(self.api, path)
        result = self.get_resource(validate_desired=False)
        if result is None:
            self.client.fail_request("Realm could not be read after synchronization submission.")
        self.exit(changed=not dry_run, resource=result, **values)

    def _execute_reset(self, resource):
        realm = resource["realm"]
        if realm not in self.builtin_comments:
            self.module.fail_json(msg="state=reset applies to built-in PAM/PVE realms; use state=absent to delete other realms.")
        if self.module.check_mode:
            self.exit(changed=True, resource={"realm": realm, "type": realm, "comment": self.builtin_comments[realm]})
        self.call_mutation("Resetting built-in realm configuration", self.api.delete, REALM_RESOURCE_PATH.format(realm))
        result = self.get_resource(validate_desired=False)
        if result is None or result.get("type") != realm:
            self.client.fail_request("Built-in realm could not be read after configuration reset.")
        self.exit(changed=True, resource=result)

    def execute(self):
        self._validate_operation_options()
        state = self.module.params.get("state", "present")
        if state == "synced":
            self._execute_sync(self.get_resource())
            return
        if state == "reset":
            self._execute_reset(self.get_resource())
            return
        super().execute()

    def prepare_result(self, changed, resource=None, **values):
        if resource is not None:
            original = resource
            resource = {name: value for name, value in resource.items() if name not in ("password", "client-key", "tfa")}
            if "tfa" in original:
                options = self._parse_formatted_options(original["tfa"])
                resource["tfa"] = {name: value for name, value in options.items() if name in ("type", "id", "url", "digits", "step")}
        return super().prepare_result(changed, resource, **values)


def main():
    options = dict(FIELD_SPEC)
    options.update(
        {
            "realm": {"type": "str", "required": True},
            "type": {"type": "str", "choices": ["ldap", "ad", "openid", "pam", "pve"]},
            "password": {"type": "str", "no_log": True},
            "update_password": {"type": "str", "choices": ["on_create", "always"], "default": "on_create"},
            "remove_properties": {"type": "list", "elements": "str", "default": []},
            "digest": {"type": "str"},
            "check_connection": {"type": "bool"},
            "state": {"type": "str", "choices": ["present", "absent", "synced", "reset"], "default": "present"},
            "allow_protected": {"type": "bool", "default": False},
            "wait": {"type": "bool"},
            "sync_options": {
                "type": "dict", "options": {
                    "scope": {"type": "str", "choices": ["users", "groups", "both"]},
                    "enable_new": {"type": "bool"},
                    "remove_vanished": {"type": "str"},
                    "full": {"type": "bool"},
                    "purge": {"type": "bool"},
                    "dry_run": {"type": "bool", "default": False},
                },
            },
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
