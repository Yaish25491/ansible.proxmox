#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_tfa_info
short_description: Retrieve Proxmox VE two-factor authentication metadata
description:
- Lists visible users' TFA metadata, one user's factors, or one selected factor.
- Excludes secrets, challenges, verification values and recovery codes. Never modifies configuration.
- Also reads native user/realm TFA types or the cluster's WebAuthn/U2F configuration.
version_added: 1.0.0
options:
  query:
    description: Whether to read factors, native user/realm types, or TFA-specific cluster configuration.
    type: str
    choices: [factors, user_types, configuration]
    default: factors
  multiple:
    description:
    - Native C(multiple) flag for O(query=user_types). Defaults to true when omitted for that query.
    - True requests all configured user types; false requests the legacy single-type representation.
    type: bool
  realm:
    description: Existing realm for O(query=configuration). Omit to read cluster WebAuthn/U2F settings. Yubico API keys are excluded.
    type: str
  userid:
    description: Optional literal C(user@realm) identifier for factors. Required for user types; invalid for configuration.
    type: str
  factor_id:
    description:
    - Optional existing factor ID within O(userid). Exact C(.) and C(..) are rejected.
    - A missing selected factor returns an empty list. Requires O(userid).
    type: str
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Returns current metadata without writes.
notes:
- Requires all privileges of the C(Administrator) role propagated on C(/access), or C(/) for cluster configuration.
- Unlike TFA mutations, these reads can use API-token authentication.
- Native API visibility is limited to self or users allowed by C(User.Modify) or C(Sys.Audit) group privileges.
- An explicit user with no factors returns one record with an empty C(entries) list; a missing selected factor returns no records.
- Factor descriptions and lockout metadata are sensitive operational data; hide task output where appropriate.
"""

EXAMPLES = r"""
- name: Read factor metadata without secrets
  ansible.proxmox.proxmox_tfa_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
  no_log: true
- name: Inspect native personal and realm TFA types
  ansible.proxmox.proxmox_tfa_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    query: user_types
    userid: application@pve
    multiple: true
- name: Inspect existing WebAuthn and U2F settings without modifying them
  ansible.proxmox.proxmox_tfa_info:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    query: configuration
"""

RETURN = r"""
users:
  description: Sanitized user TFA metadata. Empty when the selected factor does not exist.
  returned: when query=factors
  type: list
  elements: dict
  contains:
    userid:
      description: Owner of the factors.
      returned: always
      type: str
    entries:
      description: Factor metadata; never includes secrets or recovery codes.
      returned: always
      type: list
      elements: dict
      contains:
        id:
          description: Server-generated factor ID.
          returned: always
          type: str
        type:
          description: Factor type.
          returned: always
          type: str
        description:
          description: User-provided description, defaulting to an empty string.
          returned: always
          type: str
        created:
          description: Creation time as a UNIX epoch.
          returned: when available
          type: int
        enable:
          description: Whether the factor is enabled, defaulting to true when omitted by the API.
          returned: always
          type: bool
    totp_locked:
      description: Whether TOTP factors are locked out.
      returned: when supplied by the list-all API
      type: bool
    tfa_locked_until:
      description: UNIX epoch until which TFA is locked.
      returned: when supplied by the list-all API
      type: int
user_types:
  description:
  - Native user/realm TFA type response, including the selected userid.
  - The server returns C(user) as an array with C(multiple=true) or a legacy type string with C(multiple=false).
  - API versions may alternatively expose a C(types) array; the original field names are preserved.
  type: dict
  returned: when query=user_types
  contains:
    userid:
      description: Selected user.
      type: str
      returned: always
    realm:
      description: Native realm TFA provider type, such as oath or yubico.
      type: str
      returned: when supplied by the API
    types:
      description: Native factor types on API versions using this field instead of user.
      type: list
      elements: str
      returned: when supplied by the API
configuration:
  description:
  - Existing cluster C(u2f)/C(webauthn), or the selected realm's C(tfa), realm ID and digest. API keys are excluded.
  - Cluster properties retain the API's property-string or mapping representation.
  - C(u2f) has C(appid)/C(origin); C(webauthn) has C(rp)/C(id)/C(origin) and may include C(allow-subdomains).
  - Realm C(tfa) is a mapping with C(type)/C(id)/C(url) strings and native numeric or numeric-string C(digits)/C(step) values when configured.
  type: dict
  returned: when query=configuration
  contains:
    realm:
      description: Selected authentication realm.
      type: str
      returned: in realm scope
    digest:
      description: Domains configuration digest.
      type: str
      returned: in realm scope
    tfa:
      description: Realm provider configuration with its API key excluded.
      type: dict
      returned: when configured in realm scope
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


class ProxmoxTfaInfoModule(ProxmoxInfoModule):
    """Read independently and allowlist metadata at the public boundary."""

    result_key = "users"

    def _validate_parameters(self):
        self._validate_query()
        userid = self.module.params.get("userid")
        factor_id = self.module.params.get("factor_id")
        if userid is not None and re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use user@realm without whitespace, slash or colon.")
        if factor_id is not None:
            if not userid or not factor_id or factor_id in (".", "..") or re.search(r"[\s/]", factor_id):
                self.module.fail_json(msg="factor_id requires userid and must be nonempty, without whitespace, slash or exact dot segments.")

    def _validate_query(self):
        params = self.module.params
        userid = params.get("userid")
        factor_id = params.get("factor_id")
        query = params.get("query", "factors")
        realm = params.get("realm")
        if realm is not None and (query != "configuration" or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,31}", realm) is None):
            self.module.fail_json(msg="realm is only valid for configuration and must be a valid 2 to 32 character realm identifier.")
        if query == "configuration" and (userid is not None or factor_id is not None):
            self.module.fail_json(msg="configuration does not accept userid or factor_id.")
        if query == "user_types" and (userid is None or factor_id is not None):
            self.module.fail_json(msg="user_types requires userid and does not accept factor_id.")
        if query != "user_types" and params.get("multiple") is not None:
            self.module.fail_json(msg="multiple is only valid for user_types.")

    @staticmethod
    def _metadata(entry):
        metadata = {key: entry[key] for key in ("id", "type", "created") if key in entry}
        metadata.update(description=entry.get("description", ""), enable=bool(int(entry.get("enable", 1))))
        return metadata

    def _user_metadata(self, user, factor_id):
        entries = [entry for entry in user.get("entries", []) if factor_id is None or entry.get("id") == factor_id]
        if factor_id is not None and not entries:
            return None
        record = {"userid": user["userid"], "entries": [self._metadata(entry) for entry in entries]}
        if "totp-locked" in user:
            record["totp_locked"] = bool(int(user["totp-locked"]))
        if "tfa-locked-until" in user:
            record["tfa_locked_until"] = user["tfa-locked-until"]
        return record

    def get_info(self):
        self._validate_parameters()
        userid = self.module.params.get("userid")
        factor_id = self.module.params.get("factor_id")
        query = self.module.params.get("query", "factors")
        if query == "configuration":
            return self._configuration_info()
        self.client.validate_pve_permissions(self.api, "/access")
        if query == "user_types":
            multiple = self.module.params.get("multiple")
            data = self.call_api("Reading native TFA types", self.api.get, "access/users/{0}/tfa".format(quote(userid, safe="")),
                                 multiple=int(True if multiple is None else multiple))
            return {"userid": userid, **{key: data[key] for key in ("realm", "user", "types") if key in data}}
        if userid is None:
            users = self.call_api("Listing TFA metadata", self.api.get, "access/tfa")
        elif factor_id is not None:
            path = "access/tfa/{0}/{1}".format(quote(userid, safe=""), quote(factor_id, safe=""))
            entry = self.call_api("Reading selected TFA metadata", self.api.get, path, return_none_on_404=True)
            users = [{"userid": userid, "entries": [entry] if entry is not None else []}]
        else:
            entries = self.call_api("Reading user TFA metadata", self.api.get, "access/tfa/{0}".format(quote(userid, safe="")))
            users = [{"userid": userid, "entries": entries}]
        result = []
        for user in users:
            record = self._user_metadata(user, factor_id)
            if record is not None:
                result.append(record)
        return result

    def _configuration_info(self):
        realm = self.module.params.get("realm")
        self.client.validate_pve_permissions(self.api, "/access" if realm else "/")
        if realm:
            data = self.call_api("Reading realm TFA configuration", self.api.get, "access/domains/{0}".format(quote(realm, safe="")))
            result = {"realm": realm, "digest": data["digest"]}
            if "tfa" in data:
                value = data["tfa"]
                if not isinstance(value, dict):
                    value = {key: field_value for key, separator, field_value in (field.partition("=") for field in value.split(",")) if separator}
                result["tfa"] = {key: value[key] for key in ("type", "id", "url", "digits", "step") if key in value}
            return result
        data = self.call_api("Reading cluster TFA configuration", self.api.get, "cluster/options")
        return {key: data[key] for key in ("u2f", "webauthn") if key in data}

    def execute(self):
        self.result_key = {"factors": "users", "user_types": "user_types", "configuration": "configuration"}[
            self.module.params.get("query", "factors")
        ]
        super().execute()


def main():
    module = AnsibleModule(
        argument_spec=proxmox_argument_spec({
            "userid": {"type": "str"},
            "factor_id": {"type": "str"},
            "query": {"type": "str", "choices": ["factors", "user_types", "configuration"], "default": "factors"},
            "multiple": {"type": "bool"},
            "realm": {"type": "str"},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
        required_by={"factor_id": "userid"},
    )
    ProxmoxTfaInfoModule(module).execute()


if __name__ == "__main__":
    main()
