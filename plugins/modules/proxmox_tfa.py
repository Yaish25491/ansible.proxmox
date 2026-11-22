#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_tfa
short_description: Manage Proxmox VE two-factor authentication
description:
- Enrolls TOTP, Yubico OTP, WebAuthn, U2F and recovery factors using the native API workflows.
- Uses an existing factor ID, or a unique combination of factor type and description, for repeatable reconciliation.
- Also reconciles cluster or realm provider settings and resets user lockouts.
- Synchronous writes are independently read back. O(task_timeout) has no effect.
version_added: 1.0.0
options:
  userid:
    description:
    - Existing user in C(user@realm) format. Supply the literal identifier, without URL encoding.
    - Required for O(state=present), O(state=absent) and O(state=unlocked); not accepted for O(state=configured).
    type: str
  factor_id:
    description:
    - Existing factor ID. Cannot be chosen for enrollment, except the API's fixed C(recovery) ID with O(type=recovery).
    - Required for unambiguous management when descriptions are not unique. Exact C(.) and C(..) are rejected.
    - A missing ID is a no-op with O(state=absent) and an error with O(state=present).
    type: str
  type:
    description:
    - Factor type. Required with O(description) when O(factor_id) is omitted.
    - Immutable for an existing selected factor. Recovery codes use the fixed C(recovery) ID.
    - C(yubico) enrollment requires a Yubico-configured realm and a current device OTP in O(value), validated by Proxmox.
    - C(recovery) manages one set per user. Omit O(description) and O(enable); recovery-set metadata cannot be edited.
    - To replace recovery codes, explicitly remove the old set and create a new one. The API has no atomic rotation operation.
    type: str
    choices: [totp, u2f, webauthn, recovery, yubico]
  description:
    description:
    - Factor description, up to 255 characters. Omit to preserve when selecting by O(factor_id).
    - Without O(factor_id), a nonempty description identifies one factor of O(type). Multiple matches fail without writes.
    - Use O(factor_id) to rename a factor. Changing this selector without an ID identifies a different resource.
    - Unique-description enrollment has no server-side compare-and-create lock. Serialize concurrent enrollment for the same user and selector.
    type: str
  enable:
    description:
    - Whether the factor is enabled. Omit to preserve; newly enrolled TOTP factors default to enabled.
    - A new disabled factor is enrolled and then disabled by a second write. Failures after enrollment retain C(changed=true).
    type: bool
  totp_uri:
    description:
    - Complete C(otpauth://totp/) provisioning URI, including its secret, for initial TOTP enrollment.
    - Required with O(value) only when enrolling. Ignored when the selected factor exists; secrets are not compared or rotated.
    - Protect with Ansible Vault. Never returned by the module.
    type: str
  value:
    description:
    - Current TOTP code, Yubico OTP, or serialized WebAuthn/U2F registration response, for initial enrollment.
    - Required with O(challenge) to finish hardware registration. Never returned.
    type: str
  challenge:
    description:
    - Challenge identifier from an earlier WebAuthn or U2F registration request, used with O(value) to finish registration.
    - For WebAuthn, use C(publicKey.challenge) from the JSON returned as RV(challenge).
    - For U2F, pass the entire RV(challenge) string verbatim; parsing and reserializing it can invalidate registration.
    - Omit both O(challenge) and O(value) to start hardware registration and return RV(challenge). No completed factor exists at this stage.
    - Obtain an authenticator response externally, then call again with O(challenge) and O(value).
    - Starting registration changes pending state and can enable the user's TFA marker before a factor exists.
    - Challenges expire server-side and cannot be listed or canceled through the API. Do not start concurrent registrations for the same selector.
    type: str
  allow_protected_user:
    description:
    - Permit factor management or lockout reset for the authenticated operator or C(root@pam).
    - Only C(root@pam) can manage root authentication factors.
    - Keep disabled for ordinary provisioning to avoid changing the controller's authentication.
    type: bool
    default: false
  reauth_password:
    description:
    - Current password of the operator performing the change, not the target user.
    - Defaults to O(api_password) for operators other than C(root@pam), who must confirm their password.
    - For C(root@pam), omitted confirmation is not sent; supply this option explicitly only when needed.
    - Native factor writes require self or C(User.Modify) authorization in addition to the collection's Administrator precheck.
    - Must contain between 5 and 64 characters when supplied.
    type: str
  state:
    description:
    - C(present) enrolls or reconciles one factor; C(absent) removes the selected factor.
    - Deletion requires C(root@pam), including check-mode prediction for an existing factor, to avoid sending confirmation credentials in a URL.
    - Non-root operators can enroll and update factors. Deleting the final factor can disable the user's TFA configuration.
    - C(configured) reconciles WebAuthn/U2F cluster properties, or realm TFA settings when O(realm) is supplied.
    - Cluster settings apply to every cluster user and can invalidate existing hardware credentials. Configuration does not enroll devices.
    - Configuration string values cannot contain commas or line breaks. Nested omissions preserve values; an explicit empty mapping removes the whole property.
    - Configuration accepts only configuration options; O(userid) and factor options must be omitted.
    - C(unlocked) resets the selected user's lockout. It accepts O(userid) and O(allow_protected_user), without factor or configuration options.
    type: str
    choices: [present, absent, configured, unlocked]
    default: present
  realm:
    description: Existing realm identifier for O(state=configured). Selects realm TFA configuration instead of cluster WebAuthn/U2F settings.
    type: str
  realm_tfa:
    description: Native realm TFA property. Omit to preserve; use an empty mapping to remove TFA enforcement for the realm.
    type: dict
    suboptions:
      type:
        description: Native realm provider type, required when the realm has no TFA property yet.
        type: str
        choices: [oath, yubico]
      id:
        description: Yubico validation-service client ID.
        type: str
      key:
        description: Yubico validation-service API key. Preserved when omitted and never returned.
        type: str
      url:
        description: Yubico validation-service URL.
        type: str
      digits:
        description: Realm TOTP code length, from 6 to 8. Native default is 6.
        type: int
      step:
        description: Realm TOTP period in seconds, at least 10. Native default is 30.
        type: int
  digest:
    description:
    - Optional realm-configuration digest guard. When omitted, the freshly read realm digest is used when available.
    - Cluster configuration has no digest guard.
    type: str
  allow_protected_realm:
    description: Permit changing built-in realms or the operator's own realm. Such changes can affect the controller's authentication.
    type: bool
    default: false
  u2f:
    description: Legacy U2F settings. Omit to preserve; supply an empty mapping to remove.
    type: dict
    suboptions:
      appid:
        description: U2F AppId URL override. The server defaults to the origin when omitted.
        type: str
      origin:
        description: U2F origin URL override.
        type: str
  webauthn:
    description: WebAuthn relying-party settings. Omit to preserve; supply an empty mapping to remove.
    type: dict
    suboptions:
      rp:
        description: Relying-party display name. Changing it can break existing credentials.
        type: str
      id:
        description: Relying-party domain name without scheme, port or path. Changing it breaks existing credentials.
        type: str
      origin:
        description: Browser origin URL using HTTPS, or HTTP for localhost. Changing it can break existing credentials.
        type: str
      allow_subdomains:
        description: Allow origin subdomains. The native default is true; requires a server version supporting this property.
        type: bool
  remove_properties:
    description: With O(state=configured), explicitly remove whole properties. Cannot be combined with a nonempty desired value for the same property.
    type: list
    elements: str
    choices: [u2f, webauthn, tfa]
    default: []
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: partial
    description: Validates and predicts without writes, codes, challenges or generated IDs.
    details: Factor and configuration reconciliation is fully predicted. Unlock conservatively predicts changed; only its native response determines a no-op.
notes:
- Factor operations require password authentication, including for no-ops and check mode. Configuration and unlock can use API tokens.
- Requires the collection's propagated Administrator privileges on C(/access), or C(/) for cluster configuration, including in check mode.
- Cluster writes additionally require native C(Sys.Modify); realm writes require C(Realm.Allocate).
- Set task-level C(no_log=true) for enrollment and every task that handles challenges, authenticator responses or recovery codes.
- Failed enrollment can still change the user's TFA marker. Inspect factor metadata and user TFA configuration before retrying;
  the module cannot detect these internal partial writes after a rejected request.
"""

EXAMPLES = r"""
- name: Enroll a TOTP factor once
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: totp
    description: Application automation
    totp_uri: '{{ vault_application_totp_uri }}'
    value: '{{ application_current_totp }}'
  no_log: true
- name: Disable an existing factor
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    factor_id: 01234567-89ab-cdef-0123-456789abcdef
    enable: false
- name: Generate a user's recovery codes once
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: recovery
  register: recovery_enrollment
  no_log: true
- name: Start WebAuthn registration using existing cluster relying-party settings
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: webauthn
    description: Application security key
  register: registration
  no_log: true
- name: Finish WebAuthn registration with an externally obtained authenticator response
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: webauthn
    description: Application security key
    challenge: '{{ (registration.challenge | from_json).publicKey.challenge }}'
    value: '{{ authenticator_response | to_json }}'
  no_log: true
- name: Enroll a Yubico OTP device through the realm's configured validation service
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@yubico_realm
    type: yubico
    description: Application YubiKey
    value: '{{ current_yubico_otp }}'
  no_log: true
- name: Start legacy U2F registration using existing cluster U2F settings
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: u2f
    description: Application legacy security key
  register: u2f_registration
  no_log: true
- name: Finish U2F registration with the external authenticator response
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: administrator@pve
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    type: u2f
    description: Application legacy security key
    challenge: '{{ u2f_registration.challenge | string }}'
    value: '{{ u2f_authenticator_response | to_json }}'
  no_log: true
- name: Remove one factor without confirmation credentials in the URL
  ansible.proxmox.proxmox_tfa:
    api_host: pve.example.com
    api_user: root@pam
    api_password: '{{ vault_proxmox_password }}'
    userid: application@pve
    factor_id: 01234567-89ab-cdef-0123-456789abcdef
    state: absent

- name: Configure the WebAuthn relying party for a designated cluster
  ansible.proxmox.proxmox_tfa:
    state: configured
    api_host: pve.example.com
    api_token_id: administrator@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    webauthn:
      rp: Proxmox Operations
      id: example.com
      origin: https://pve.example.com:8006
- name: Predict removing legacy U2F settings
  ansible.proxmox.proxmox_tfa:
    state: configured
    api_host: pve.example.com
    api_token_id: administrator@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    remove_properties: [u2f]
  check_mode: true
- name: Configure Yubico OTP validation for an existing non-controller realm
  ansible.proxmox.proxmox_tfa:
    state: configured
    api_host: pve.example.com
    api_token_id: administrator@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    realm: contractors
    realm_tfa:
      type: yubico
      id: '12345'
      key: '{{ vault_yubico_api_key }}'
      url: https://api.yubico.com/wsapi/2.0/verify
  no_log: true

- name: Unlock an existing user's TFA without replacing their factors
  ansible.proxmox.proxmox_tfa:
    state: unlocked
    api_host: pve.example.com
    api_token_id: administrator@pve!automation
    api_token_secret: '{{ vault_proxmox_token_secret }}'
    userid: application@pve
"""

RETURN = r"""
factor:
  description: Sanitized current or predicted factor metadata, or null when absent or hardware enrollment is pending.
  returned: when state=present or state=absent
  type: dict
  contains:
    userid:
      description: Owner of the factor.
      type: str
      returned: when a factor exists or enrollment is predicted
    id:
      description: Server-generated factor ID. Not invented during enrollment predictions.
      type: str
      returned: when the factor exists
    type:
      description: Factor type.
      type: str
      returned: when a factor exists or enrollment is predicted
    description:
      description: User-provided description, or an empty string when omitted by the API.
      type: str
      returned: when a factor exists or non-recovery enrollment is predicted
    created:
      description: Creation time as a UNIX epoch, when supplied by the API.
      type: int
      returned: when available
    enable:
      description: Whether the factor is enabled.
      type: bool
      returned: when a factor exists or non-recovery enrollment is predicted
challenge:
  description: Native serialized registration request for the external authenticator. No factor ID exists until registration finishes.
  type: str
  returned: only when WebAuthn or U2F registration is started
enrollment_pending:
  description: Whether the request started a hardware registration rather than creating a completed factor.
  type: bool
  returned: only when hardware registration is started
recovery_codes:
  description:
  - One-time recovery codes, returned only after verified creation. Persist them immediately using tasks with C(no_log=true).
  - Repeated calls, information reads and check mode cannot recover these codes.
  type: list
  elements: str
  returned: only when a recovery set is created and verified

configuration:
  description:
  - Current or predicted cluster settings, or realm/digest/TFA settings. Missing properties and Yubico API keys are omitted.
  - C(u2f) contains C(appid) and C(origin) strings when configured.
  - C(webauthn) contains C(rp), C(id) and C(origin) strings, plus the native C(allow-subdomains) boolean when configured.
  - C(tfa) contains C(type), C(id) and C(url) strings, and C(digits)/C(step) in their native numeric or numeric-string representation.
  - Changed check-mode predictions omit the digest because the server has not generated the new value.
  type: dict
  returned: when state=configured
  contains:
    realm:
      description: Selected authentication realm.
      type: str
      returned: for realm configuration
    digest:
      description: Current domains configuration digest.
      type: str
      returned: for realm configuration read from the API
    u2f:
      description: Configured U2F settings.
      type: dict
      returned: when configured in cluster scope
    webauthn:
      description: Configured WebAuthn settings with native property names.
      type: dict
      returned: when configured in cluster scope
    tfa:
      description: Realm provider settings with the API key excluded.
      type: dict
      returned: when configured in realm scope

tfa:
  description:
  - Lockout reset result. The native boolean response determines whether the operation changed lockout state.
  - Reported lockout flags are independently verified after an actual reset.
  type: dict
  returned: when state=unlocked
  contains:
    userid:
      description: Selected user.
      type: str
      returned: always
    was_locked:
      description: Native API result indicating whether the request changed lockout state.
      type: bool
      returned: outside check mode
"""

import re
from urllib.parse import quote, quote_plus

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_action import ProxmoxActionModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

TFA_USER_PATH = "access/tfa/{0}"
TFA_ENTRY_PATH = "access/tfa/{0}/{1}"
ROOT_USER = "root@pam"
ACCESS_ACL_PATH = "/access"
TFA_CONFIG_PROPERTIES = ("u2f", "webauthn")
ALLOW_SUBDOMAINS = "allow-subdomains"


class ProxmoxTfaModule(ProxmoxResourceModule):
    """Reconcile one owned factor without exposing authentication secrets."""

    result_key = "factor"

    def __init__(self, module, client=None):
        super().__init__(module, client)
        self.enrollment_challenge = None
        self.recovery_codes = None

    def _validate_operation_options(self):
        params = self.module.params
        state = params.get("state", "present")
        factor_fields = ("factor_id", "type", "description", "enable", "totp_uri", "value", "challenge", "reauth_password")
        config_fields = ("realm", "realm_tfa", "digest", "u2f", "webauthn")
        if state not in ("present", "absent", "configured", "unlocked"):
            self.module.fail_json(msg="Unsupported TFA operation state.")
        forbidden = ()
        if state == "configured":
            forbidden = ("userid",) + factor_fields
            if params.get("allow_protected_user"):
                self.module.fail_json(msg="allow_protected_user applies only to factor and unlock operations.")
        else:
            forbidden = config_fields
            if params.get("remove_properties") or params.get("allow_protected_realm"):
                self.module.fail_json(msg="Configuration options require state=configured.")
            if not params.get("userid"):
                self.module.fail_json(msg="userid is required for factor and unlock operations.")
            if state == "unlocked":
                forbidden += factor_fields
        invalid = [name for name in forbidden if params.get(name) is not None]
        if invalid:
            self.module.fail_json(msg="Options do not apply to state={0}: {1}.".format(state, ", ".join(invalid)))

    def execute(self):
        self._validate_operation_options()
        state = self.module.params.get("state", "present")
        if state == "configured":
            _ProxmoxTfaConfiguration(self.module, client=self.client).execute()
            return
        if state == "unlocked":
            _ProxmoxTfaUnlock(self.module, client=self.client).execute()
            return
        super().execute()

    def _validate_parameters(self):
        params = self.module.params
        userid = params["userid"]
        if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use user@realm without whitespace, slash or colon.")
        authuser = params.get("api_user") or (params.get("api_token_id") or "").rsplit("!", 1)[0]
        if userid in (ROOT_USER, authuser) and not params.get("allow_protected_user", False):
            self.module.fail_json(msg="The root or authenticated account's TFA cannot be managed by this module.")
        if userid == ROOT_USER and authuser != ROOT_USER:
            self.module.fail_json(msg="Only root@pam can manage root@pam TFA entries.")
        self._validate_authentication(authuser)
        self._validate_selector()

    def _validate_authentication(self, authuser):
        params = self.module.params
        if params.get("api_token_id") or params.get("api_token_secret") or not params.get("api_password"):
            self.module.fail_json(msg="TFA management requires password authentication; API tokens cannot change TFA entries.")
        password = self._reauth_password()
        if password is not None and not 5 <= len(password) <= 64:
            self.module.fail_json(msg="The operator reauthentication password must contain between 5 and 64 characters.")
        if authuser != ROOT_USER and not password:
            self.module.fail_json(msg="The operator reauthentication password is required.")

    def _validate_selector(self):
        params = self.module.params
        factor_id = params.get("factor_id")
        if factor_id is not None and (not factor_id or factor_id in (".", "..") or re.search(r"[\s/]", factor_id)):
            self.module.fail_json(msg="factor_id must be nonempty, without whitespace or slash, and cannot equal '.' or '..'.")
        description = params.get("description")
        if description is not None and len(description) > 255:
            self.module.fail_json(msg="description cannot exceed 255 characters.")
        if factor_id is None and params.get("type") != "recovery" and (not params.get("type") or not description):
            self.module.fail_json(msg="Without factor_id, type and a nonempty description are required as the enrollment identity.")

    def _reauth_password(self):
        value = self.module.params.get("reauth_password")
        if value is None and self.module.params.get("api_user") == ROOT_USER:
            return None
        return value if value is not None else self.module.params.get("api_password")

    def _password_parameters(self):
        password = self._reauth_password()
        return {"password": password} if password is not None else {}

    @staticmethod
    def _metadata(userid, entry):
        result = {key: entry[key] for key in ("id", "type", "created") if key in entry}
        result.update(userid=userid, description=entry.get("description", ""), enable=bool(int(entry.get("enable", 1))))
        return result

    def _list_entries(self):
        userid = self.module.params["userid"]
        return self.call_api("Listing TFA metadata", self.api.get, TFA_USER_PATH.format(quote(userid, safe="")))

    def _validate_creation(self):
        params = self.module.params
        factor_type = params.get("type")
        if params.get("factor_id") is not None and not (factor_type == "recovery" and params["factor_id"] == "recovery"):
            self.client.fail_request("The selected factor ID does not exist; server-generated IDs cannot be chosen for enrollment.")
        if factor_type == "totp":
            self._validate_totp_creation()
            return
        if params.get("totp_uri") is not None:
            self.module.fail_json(msg="totp_uri is only valid for TOTP enrollment.")
        if factor_type in ("webauthn", "u2f"):
            if bool(params.get("challenge")) != bool(params.get("value")):
                self.module.fail_json(msg="Hardware enrollment requires both challenge and value to finish, or neither to start.")
            return
        if params.get("challenge") is not None:
            self.module.fail_json(msg="challenge is only valid for WebAuthn or U2F enrollment.")
        if factor_type == "yubico":
            if not params.get("value"):
                self.module.fail_json(msg="Yubico enrollment requires a current device OTP in value.")
            return
        if factor_type == "recovery":
            if params.get("value") is not None:
                self.module.fail_json(msg="Recovery generation does not accept value.")
            self._validate_recovery_options()
            return
        self.module.fail_json(msg="type is required for enrollment.")

    def _validate_totp_creation(self):
        params = self.module.params
        if params.get("challenge") is not None:
            self.module.fail_json(msg="challenge is only valid for WebAuthn or U2F enrollment.")
        uri = params.get("totp_uri") or ""
        if not uri.startswith("otpauth://totp/") or not uri.removeprefix("otpauth://totp/") or not params.get("value"):
            self.module.fail_json(msg="Initial TOTP enrollment requires an otpauth://totp/ URI and a current verification value.")

    def _validate_recovery_options(self):
        if self.module.params.get("description") is not None or self.module.params.get("enable") is not None:
            self.module.fail_json(msg="The API does not support changing recovery-set description or enable; omit both options.")

    def _target_exists(self):
        params = self.module.params
        self.client.validate_pve_permissions(self.api, ACCESS_ACL_PATH)
        users = self.call_api("Listing users", self.api.get, "access/users")
        exists = self.validate_proxmox_resource_exist("user", params["userid"], lambda: users, resource_key="userid", fail_on_missing=False)
        if not exists and params["state"] == "present":
            self.client.fail_request("The target user must exist before managing TFA.")
        return exists

    def _select_entry(self, entries):
        params = self.module.params
        if params.get("factor_id") is not None:
            matches = [entry for entry in entries if entry.get("id") == params["factor_id"]]
        elif params.get("type") == "recovery":
            matches = [entry for entry in entries if entry.get("type") == "recovery"]
        else:
            matches = [entry for entry in entries if entry.get("type") == params["type"] and entry.get("description", "") == params["description"]]
        if len(matches) > 1:
            self.client.fail_request("Multiple TFA entries match; select the intended factor_id explicitly.")
        return matches[0] if matches else None

    def get_resource(self):
        self._validate_parameters()
        params = self.module.params
        if not self._target_exists():
            return None
        entry = self._select_entry(self._list_entries())
        if entry is None:
            if params["state"] == "present":
                self._validate_creation()
            return None
        resource = self._metadata(params["userid"], entry)
        if params["state"] == "present" and params.get("type") is not None and params["type"] != resource["type"]:
            self.module.fail_json(msg="The type of an existing factor cannot be changed.")
        if params["state"] == "absent":
            self._validate_deletion()
        return resource

    def _validate_deletion(self):
        if self.module.params.get("api_user") != ROOT_USER:
            self.client.fail_request("TFA deletion requires root@pam because non-root password confirmation cannot be safely transported by this API.")

    def get_update_parameters(self, resource):
        if resource.get("type") == "recovery":
            self._validate_recovery_options()
            return {}
        desired = {key: self.module.params[key] for key in ("description", "enable") if self.module.params.get(key) is not None}
        return self.get_parameter_updates(resource, desired, {})

    def _entry_path(self, factor_id):
        return TFA_ENTRY_PATH.format(quote(self.module.params["userid"], safe=""), quote(factor_id, safe=""))

    def _read_factor(self, factor_id):
        self.client.validate_pve_permissions(self.api, ACCESS_ACL_PATH)
        entries = self._list_entries()
        entry = next((entry for entry in entries if entry.get("id") == factor_id), None)
        return self._metadata(self.module.params["userid"], entry) if entry is not None else None

    def create_resource(self):
        params = self.module.params
        parameters = {"type": params["type"], **self._password_parameters()}
        for name, api_name in (("description", "description"), ("totp_uri", "totp"), ("value", "value"), ("challenge", "challenge")):
            if params.get(name) is not None:
                parameters[api_name] = params[name]
        result = self.call_mutation(
            "Enrolling TFA factor", self.api.post, TFA_USER_PATH.format(quote(params["userid"], safe="")), **parameters
        )
        if self._accept_pending_challenge(result):
            return None
        if not isinstance(result, dict) or not result.get("id"):
            return self.client.fail_request("TFA enrollment returned no factor ID; inspect the user's TFA entries before retrying.")
        factor = self._read_factor(result["id"])
        if factor is None:
            return self.client.fail_request("Enrolled factor could not be read back; inspect TFA entries before retrying.")
        if factor.get("type") != params["type"] or (params["type"] != "recovery" and factor["description"] != params["description"]):
            return self.client.fail_request("Enrolled factor metadata does not match the requested enrollment.")
        if params["type"] == "recovery":
            self._accept_recovery_codes(result)
        updates = self.get_update_parameters(factor)
        return self.update_resource(factor, updates) if updates else factor

    def _accept_recovery_codes(self, result):
        codes = result.get("recovery")
        if result["id"] != "recovery" or not isinstance(codes, list) or not codes or not all(isinstance(code, str) and code for code in codes):
            return self.client.fail_request("Recovery creation returned no usable codes; inspect the user's recovery set before retrying.")
        self.recovery_codes = list(codes)

    def _accept_pending_challenge(self, result):
        params = self.module.params
        if params["type"] not in ("webauthn", "u2f") or params.get("challenge"):
            return False
        if not isinstance(result, dict) or result.get("id") or not isinstance(result.get("challenge"), str) or not result["challenge"]:
            return self.client.fail_request("Hardware registration returned no usable challenge; inspect TFA state before retrying.")
        self.enrollment_challenge = result["challenge"]
        return True

    def update_resource(self, resource, updates):
        parameters = dict(updates, **self._password_parameters())
        if "enable" in parameters:
            parameters["enable"] = int(parameters["enable"])
        self.call_mutation("Updating TFA metadata", self.api.put, self._entry_path(resource["id"]), **parameters)
        factor = self._read_factor(resource["id"])
        if factor is None or self.get_update_parameters(factor):
            self.client.fail_request("Updated TFA metadata could not be verified.")
        return factor

    def delete_resource(self, resource):
        self._validate_deletion()
        self.call_mutation("Deleting TFA entry", self.api.delete, self._entry_path(resource["id"]))
        if self._read_factor(resource["id"]) is not None:
            self.client.fail_request("TFA entry deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            if self.module.params["type"] == "recovery":
                return {"userid": self.module.params["userid"], "type": "recovery"}
            return {"userid": self.module.params["userid"], "type": self.module.params["type"], "description": self.module.params["description"],
                    "enable": self.module.params.get("enable") if self.module.params.get("enable") is not None else True}
        return super().get_check_mode_resource(operation, resource, updates)

    def prepare_result(self, changed, resource=None, **values):
        result = super().prepare_result(changed, resource, **values)
        if self.enrollment_challenge is not None:
            result.update(enrollment_pending=True, challenge=self.enrollment_challenge)
        if self.recovery_codes is not None:
            result["recovery_codes"] = self.recovery_codes
        return result


class _ProxmoxTfaConfiguration(ProxmoxResourceModule):
    """Reconcile the two TFA cluster properties through the shared resource framework."""

    result_key = "configuration"

    def get_resource(self):
        self._validate_scope()
        realm = self.module.params.get("realm")
        self.client.validate_pve_permissions(self.api, ACCESS_ACL_PATH if realm else "/")
        if realm:
            realms = self.call_api("Listing TFA realms", self.api.get, "access/domains")
            exists = self.validate_proxmox_resource_exist("realm", realm, lambda: realms, resource_key="realm", fail_on_missing=False)
            if not exists:
                return self.client.fail_request("The TFA configuration realm does not exist.")
            data = self.call_api("Reading realm TFA configuration", self.api.get, self._config_path())
            result = {"realm": realm, "digest": data["digest"]}
            if "tfa" in data:
                result["tfa"] = self._property_value(data["tfa"])
                self._mask_api_key(result["tfa"].get("key"))
            return result
        data = self.call_api("Reading cluster TFA configuration", self.api.get, self._config_path())
        return {key: self._property_value(data[key]) for key in TFA_CONFIG_PROPERTIES if key in data}

    def _config_path(self):
        realm = self.module.params.get("realm")
        return "access/domains/{0}".format(quote(realm, safe="")) if realm else "cluster/options"

    def _mask_api_key(self, value):
        if value:
            values = set(getattr(self.module, "no_log_values", ()))
            values.update((value, quote(value, safe=""), quote_plus(value)))
            self.module.no_log_values = values

    def _validate_scope(self):
        params = self.module.params
        self._validate_property_values()
        realm = params.get("realm")
        removals = params["remove_properties"]
        if realm is None:
            if params.get("realm_tfa") is not None or params.get("digest") is not None or "tfa" in removals:
                self.module.fail_json(msg="realm_tfa, digest and removing tfa require realm.")
            return
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,31}", realm) is None:
            self.module.fail_json(msg="realm must be a valid Proxmox realm identifier of 2 to 32 characters.")
        if any(params.get(name) is not None or name in removals for name in TFA_CONFIG_PROPERTIES):
            self.module.fail_json(msg="Realm configuration cannot be combined with cluster WebAuthn/U2F properties.")
        authuser = params.get("api_user") or (params.get("api_token_id") or "").rsplit("!", 1)[0]
        if realm in ("pam", "pve", authuser.rsplit("@", 1)[-1]) and not params["allow_protected_realm"]:
            if params.get("realm_tfa") is not None or "tfa" in removals:
                self.module.fail_json(msg="Changing built-in or controller realm TFA requires allow_protected_realm=true.")

    def _validate_property_values(self):
        for name in (*TFA_CONFIG_PROPERTIES, "realm_tfa"):
            for key, value in (self.module.params.get(name) or {}).items():
                if isinstance(value, str) and re.search(r"[,\r\n]", value):
                    self.client.fail_request("{0}.{1} cannot contain commas or line breaks in a native property string.".format(name, key))

    def _property_value(self, value):
        result = dict(value) if isinstance(value, dict) else self._parse_formatted_options(value)
        if ALLOW_SUBDOMAINS in result:
            result[ALLOW_SUBDOMAINS] = bool(int(result[ALLOW_SUBDOMAINS]))
        return result

    def create_resource(self):
        self.client.fail_request("Cluster configuration was unexpectedly absent.")

    def get_update_parameters(self, resource):
        if self.module.params.get("realm"):
            return self._realm_updates(resource)
        updates = {}
        removals = set(self.module.params["remove_properties"])
        for name in TFA_CONFIG_PROPERTIES:
            value = self.module.params.get(name)
            if value is None:
                continue
            desired = {key.replace("_", "-"): item for key, item in value.items() if item is not None}
            if name in removals and desired:
                self.module.fail_json(msg="Cannot update and remove the same TFA configuration property.")
            if not desired:
                removals.add(name)
                continue
            current = resource.get(name, {})
            comparable = {ALLOW_SUBDOMAINS: True, **current} if name == "webauthn" else current
            if self.get_parameter_updates(comparable, desired, {}):
                updates[name] = self._serialize_formatted_options({**current, **desired})
        removals.intersection_update(resource)
        if removals:
            updates["delete"] = ",".join(sorted(removals))
        return updates

    def _realm_updates(self, resource):
        params = self.module.params
        value = params.get("realm_tfa")
        desired = {key: item for key, item in (value or {}).items() if item is not None}
        remove = "tfa" in params["remove_properties"] or (value is not None and not desired)
        if remove and desired:
            self.module.fail_json(msg="Cannot update and remove the same TFA configuration property.")
        updates = {}
        current = resource.get("tfa", {})
        if remove and "tfa" in resource:
            updates["delete"] = "tfa"
        elif desired:
            self._validate_realm_values(desired, current)
            self._mask_api_key(desired.get("key"))
            comparable = {"digits": 6, "step": 30, **current}
            if self.get_parameter_updates(comparable, desired, {}):
                updates["tfa"] = self._serialize_formatted_options({**current, **desired})
        if updates:
            updates["digest"] = self._guarded_digest(resource)
        return updates

    def _guarded_digest(self, resource):
        supplied = self.module.params.get("digest")
        if supplied is not None and supplied != resource["digest"]:
            self.client.fail_request("The supplied realm digest is stale; refresh configuration before retrying.")
        return supplied if supplied is not None else resource["digest"]

    def _validate_realm_values(self, desired, current):
        if not desired.get("type", current.get("type")):
            self.client.fail_request("A provider type is required for new realm TFA configuration.")
        if "digits" in desired and not 6 <= desired["digits"] <= 8:
            self.client.fail_request("Realm TOTP digits must be between 6 and 8.")
        if "step" in desired and desired["step"] < 10:
            self.client.fail_request("Realm TOTP step must be at least 10 seconds.")

    def update_resource(self, resource, updates):
        self.call_mutation("Updating TFA configuration", self.api.put, self._config_path(), **updates)
        actual = self.get_resource()
        if self.get_update_parameters(actual):
            self.client.fail_request("Cluster TFA configuration readback does not match the requested properties.")
        return actual

    def delete_resource(self, resource):
        self.client.fail_request("Cluster configuration is a singleton; remove individual properties instead.")

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        predicted = self.apply_parameter_updates(resource or {}, updates or {})
        if self.module.params.get("realm"):
            predicted.pop("digest", None)
            if "tfa" in predicted:
                predicted["tfa"] = self._property_value(predicted["tfa"])
            return predicted
        return {key: self._property_value(value) for key, value in predicted.items()}

    def prepare_result(self, changed, resource=None, **values):
        if resource is not None and self.module.params.get("realm"):
            resource = dict(resource)
            if "tfa" in resource:
                resource["tfa"] = {key: value for key, value in resource["tfa"].items() if key != "key"}
        return super().prepare_result(changed, resource, **values)


class _ProxmoxTfaUnlock(ProxmoxActionModule):
    """Expose the native conditional unlock operation through the shared action lifecycle."""

    result_key = "tfa"

    def is_action_needed(self):
        params = self.module.params
        userid = params["userid"]
        if re.fullmatch(r"[^\s:/]+@[A-Za-z][A-Za-z0-9._-]+", userid) is None:
            self.module.fail_json(msg="userid must use user@realm without whitespace, slash or colon.")
        authuser = params.get("api_user") or (params.get("api_token_id") or "").rsplit("!", 1)[0]
        if userid in ("root@pam", authuser) and not params["allow_protected_user"]:
            self.module.fail_json(msg="Resetting root or authenticated operator lockout requires allow_protected_user=true.")
        self.client.validate_pve_permissions(self.api, ACCESS_ACL_PATH)
        users = self.call_api("Listing TFA lockout users", self.api.get, "access/users")
        self.validate_proxmox_resource_exist("user", userid, lambda: users, resource_key="userid")
        return True

    def get_action_result(self):
        return {"userid": self.module.params["userid"]}

    def perform_action(self):
        userid = self.module.params["userid"]
        response = self.call_mutation("Resetting TFA lockout", self.api.put, "access/users/{0}/unlock-tfa".format(quote(userid, safe="")))
        if not isinstance(response, (bool, int)) or response not in (0, 1):
            self.client.fail_request("TFA unlock returned an invalid result; inspect the user's lockout state.")
        response = bool(response)
        if response:
            self.client.validate_pve_permissions(self.api, ACCESS_ACL_PATH)
            users = self.call_api("Verifying TFA lockout reset", self.api.get, "access/tfa")
            user = next((entry for entry in users if entry["userid"] == userid), None)
            if user is None:
                return self.client.fail_request("Unlocked user TFA metadata could not be read back.")
            if bool(int(user.get("totp-locked") or 0)) or user.get("tfa-locked-until"):
                self.client.fail_request("TFA unlock did not clear the reported lockout state.")
        self.exit(changed=response, resource={"userid": userid, "was_locked": response})


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "state": {"type": "str", "choices": ["present", "absent", "configured", "unlocked"], "default": "present"},
            "userid": {"type": "str"},
            "factor_id": {"type": "str"},
            "type": {"type": "str", "choices": ["totp", "u2f", "webauthn", "recovery", "yubico"]},
            "description": {"type": "str"},
            "enable": {"type": "bool"},
            "totp_uri": {"type": "str", "no_log": True},
            "value": {"type": "str", "no_log": True},
            "challenge": {"type": "str", "no_log": True},
            "allow_protected_user": {"type": "bool", "default": False},
            "reauth_password": {"type": "str", "no_log": True},
            "realm": {"type": "str"},
            "realm_tfa": {"type": "dict", "options": {
                "type": {"type": "str", "choices": ["oath", "yubico"]},
                "id": {"type": "str"},
                "key": {"type": "str", "no_log": True},
                "url": {"type": "str"},
                "digits": {"type": "int"},
                "step": {"type": "int"},
            }},
            "digest": {"type": "str"},
            "allow_protected_realm": {"type": "bool", "default": False},
            "u2f": {"type": "dict", "options": {"appid": {"type": "str"}, "origin": {"type": "str"}}},
            "webauthn": {"type": "dict", "options": {
                "rp": {"type": "str"},
                "id": {"type": "str"},
                "origin": {"type": "str"},
                "allow_subdomains": {"type": "bool"},
            }},
            "remove_properties": {"type": "list", "elements": "str", "choices": [*TFA_CONFIG_PROPERTIES, "tfa"], "default": []},
        }),
        supports_check_mode=True,
        required_if=[["state", state, ["userid"]] for state in ("present", "absent", "unlocked")],
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxTfaModule(module).execute()


if __name__ == "__main__":
    main()
