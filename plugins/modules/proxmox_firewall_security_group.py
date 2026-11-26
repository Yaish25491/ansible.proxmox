#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_firewall_security_group
short_description: Manage Proxmox VE firewall security groups
description:
- Reconciles one datacenter security group, its description and optional complete ordered ruleset.
- Supports native rename and rule creation, replacement, movement and deletion without managing the rules that apply the group.
version_added: 1.0.0
options:
  state:
    type: str
    choices: [present, absent]
    default: present
    description: Whether the selected definition should exist.
  name:
    type: str
    required: true
    description: Case-sensitive group name, 2 to 18 letters, digits, underscores or dashes, starting with a letter.
  rename:
    type: str
    description:
    - New name for an existing group; repeated calls recognize the already-renamed target.
    - Native rename updates group references in datacenter, host and guest firewall rules. This operation spans several configuration files.
    - If native rename fails partway through, inspect both names and references before retrying. A confirmed new copy is reported as changed even on failure.
  comment:
    type: str
    description: Omission preserves the description; empty clears it. Surrounding whitespace is stripped. Literal C(0) and line breaks are rejected.
  digest:
    type: str
    description: Optional expected security-group-list digest. Fresh native digests also guard metadata changes and rule updates/removal.
  force:
    type: bool
    default: false
    description: With C(state=absent), explicitly remove the group's rules before deleting a populated group. Otherwise nonempty deletion fails in both modes.
  rules:
    type: list
    elements: dict
    description:
    - Complete ordered rule definitions. Omission preserves the ruleset; empty removes every rule.
    - Within a supplied rule, omitted optional properties are cleared and enable defaults to false.
    - Native group rules do not accept interfaces or nested group references. IP version is derived by Proxmox, not assigned.
    suboptions:
      type:
        type: str
        required: true
        choices: [in, out, forward]
        description: Traffic direction.
      action:
        type: str
        required: true
        choices: [ACCEPT, DROP, REJECT]
        description: Rule action.
      enable:
        type: bool
        default: false
        description: Enable this rule within the group.
      comment:
        type: str
        description: Rule description; literal zero and line breaks are rejected.
      macro:
        type: str
        description: Native firewall macro name.
      source:
        type: str
        description: Native source address, range, list, alias or IP-set expression.
      dest:
        type: str
        description: Native destination address, range, list, alias or IP-set expression.
      proto:
        type: str
        description: Protocol name or number, as accepted by Proxmox.
      sport:
        type: str
        description: Native source port, service, range or list expression.
      dport:
        type: str
        description: Native destination port, service, range or list expression.
      icmp_type:
        type: str
        description: Native ICMP type; requires an ICMP protocol.
      log:
        type: str
        choices: [emerg, alert, crit, err, warning, notice, info, debug, nolog]
        description: Native rule log level. Omitted means nolog.
extends_documentation_fragment:
- ansible.proxmox.proxmox_base
- ansible.proxmox.proxmox_https_options
- ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Validates local arguments, current state and deletion guards and predicts changes without writes.
notes:
- Requires propagated C(Administrator) on C(/). Native group modification requires C(Sys.Modify); native listing allows authenticated users.
- With C(state=absent), O(rename), O(comment) and O(rules) are ignored. Deletion does not remove external rules that reference the group.
- Rule writes are sequential and use fresh rule-list digests where supported. Creation and whole-group deletion have no native digest guard.
- Server-specific macro, address and protocol compatibility is validated by the native write API; there is no native rule-validation dry run.
- Does not enable the firewall or attach a group to a guest/host. Changing an already-referenced group can affect active traffic.
- Successful writes remain C(changed=true) on subsequent failures; a multi-write reconciliation is not transactional.
"""

EXAMPLES = r"""
- name: Ensure a reusable security group and ordered rules
  ansible.proxmox.proxmox_firewall_security_group:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: web_servers
    rules:
      - type: in
        action: ACCEPT
        proto: tcp
        dport: '443'
        enable: true
      - type: in
        action: DROP
        enable: true

- name: Rename a group and its native references
  ansible.proxmox.proxmox_firewall_security_group:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: old_web
    rename: application_web

- name: Remove an unused group and its rules
  ansible.proxmox.proxmox_firewall_security_group:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    name: obsolete_web
    state: absent
    force: true
"""

RETURN = r"""
security_group:
  description: Verified or check-mode-predicted security group, or null after deletion. Native extension fields are preserved.
  type: dict
  returned: always
  contains:
    group:
      type: str
      description: Stored security-group name.
    comment:
      type: str
      description: Optional group description.
    digest:
      type: str
      description: Security-group-list digest; omitted from changed check-mode predictions.
    rules:
      type: list
      elements: dict
      description: Ordered native rule dictionaries, including position, rule-list digest and server-derived IP version when supplied.
"""

from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_firewall import ProxmoxFirewallScope
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule

RULE_FIELDS = ("type", "action", "enable", "comment", "macro", "source", "dest", "proto", "sport", "dport", "icmp-type", "log")


class ProxmoxFirewallSecurityGroupModule(ProxmoxFirewallScope, ProxmoxResourceModule):
    result_key = "security_group"
    groups_endpoint = "cluster/firewall/groups"

    def _rule_values(self, rule):
        result = {key: rule[key] for key in RULE_FIELDS if rule.get(key) not in (None, "")}
        result["enable"] = int(bool(rule.get("enable", False)))
        if result.get("log") == "nolog":
            result.pop("log")
        return result

    def _desired_rules(self):
        rules = self.module.params.get("rules")
        if rules is None:
            return None
        result = []
        for rule in rules:
            if rule.get("type") not in ("in", "out", "forward") or rule.get("action") not in ("ACCEPT", "DROP", "REJECT"):
                self.client.fail_request("Security-group rules require a supported direction and action.")
            data = {("icmp-type" if k == "icmp_type" else k): v for k, v in rule.items() if v is not None}
            if data.get("comment") is not None:
                data["comment"] = self.firewall_comment(data["comment"])
            result.append(self._rule_values(data))
        return result

    def _rules_values(self, rules):
        return [self._rule_values(rule) for rule in rules]

    def get_resource(self, readback=False):
        params = self.module.params
        name = self.firewall_name(params["name"], maximum=18)
        present = params.get("state", "present") == "present"
        self._target = self.firewall_name(params["rename"], maximum=18) if present and params.get("rename") is not None else name
        self._comment = self.firewall_comment(params.get("comment")) if present else None
        if present:
            self._desired_rules()
        self.client.validate_pve_permissions(self.api, "/")
        groups = self.firewall_records(self.groups_endpoint, "group")
        self._groups_digest = self.firewall_digest(groups, check_expected=not readback)
        if len({row["group"] for row in groups}) != len(groups):
            self.client.fail_request("Security-group discovery returned invalid or duplicate names.")
        original = next((row for row in groups if row["group"] == name), None)
        target = next((row for row in groups if row["group"] == self._target), None)
        if original is not None and target is not None and original is not target:
            self.client.fail_request("The requested security-group rename target already exists.")
        selected = original or target
        if selected is None:
            if self._target != name:
                self.client.fail_request("Neither the source security group nor its rename target exists.")
            return None
        rules = self.firewall_records(self.groups_endpoint + "/" + quote(selected["group"], safe=""), "pos", required=("type", "action"))
        if [row["pos"] for row in rules] != list(range(len(rules))):
            self.client.fail_request("Security-group API returned inconsistent rule positions.")
        if not present and rules and not params.get("force", False):
            self.client.fail_request("Security group is not empty; clear rules or explicitly set force=true.")
        return dict(selected, rules=rules)

    def get_update_parameters(self, resource):
        changes = {}
        if resource["group"] != self._target:
            changes["group"] = self._target
        if self._comment is not None and resource.get("comment", "") != self._comment:
            changes["comment"] = self._comment
        desired = self._desired_rules()
        if desired is not None and desired != self._rules_values(resource["rules"]):
            changes["rules"] = desired
        return changes

    def _observed(self):
        result = self.get_resource(readback=True)
        if result is None:
            self.client.fail_request("Security group disappeared while reconciling its state.")
        return result

    def create_resource(self):
        values = {"group": self.module.params["name"]}
        if self._comment is not None:
            values["comment"] = self._comment
        self.call_mutation("Creating firewall security group", self.api.post, self.groups_endpoint, **values)
        resource = self._observed()
        return self.update_resource(resource, self.get_update_parameters(resource))

    def _write_rule(self, resource, method, suffix, expected, **values):
        endpoint = self.groups_endpoint + "/" + quote(resource["group"], safe="") + suffix
        if method != "post":
            values["digest"] = self.firewall_digest(resource["rules"], check_expected=False)
        self.call_mutation("Reconciling firewall security-group rule", getattr(self.api, method), endpoint, **values)
        observed = self._observed()
        if self._rules_values(observed["rules"]) != expected:
            self.client.fail_request("Security-group rules changed concurrently or a write could not be verified.")
        return observed

    def _reconcile_rules(self, resource, desired):
        for position, rule in enumerate(desired):
            current = self._rules_values(resource["rules"])
            if position < len(current) and current[position] == rule:
                continue
            later = next((i for i in range(position + 1, len(current)) if current[i] == rule), None)
            if later is not None:
                current.insert(position, current.pop(later))
                resource = self._write_rule(resource, "put", "/{0}".format(later), current, moveto=position)
            elif position < len(current):
                values = dict(rule)
                deleted = sorted(set(current[position]) - set(rule))
                if deleted:
                    values["delete"] = ",".join(deleted)
                current[position] = rule
                resource = self._write_rule(resource, "put", "/{0}".format(position), current, **values)
            else:
                current.insert(0, rule)
                resource = self._write_rule(resource, "post", "", current, **rule)
                if position:
                    current.insert(position, current.pop(0))
                    resource = self._write_rule(resource, "put", "/0", current, moveto=position + 1)
        while len(resource["rules"]) > len(desired):
            position = len(resource["rules"]) - 1
            expected = self._rules_values(resource["rules"][:-1])
            resource = self._write_rule(resource, "delete", "/{0}".format(position), expected)
        return resource

    def update_resource(self, resource, updates):
        if "group" in updates or "comment" in updates:
            values = {"group": self._target, "rename": resource["group"], "digest": self._groups_digest}
            if self._comment is not None:
                values["comment"] = self._comment
            old_rules = self._rules_values(resource["rules"])
            self.call_mutation("Renaming/updating firewall security group", self._post_group_metadata, resource["group"], **values)
            resource = self._observed()
            if self._rules_values(resource["rules"]) != old_rules:
                self.client.fail_request("Security-group rules changed during metadata reconciliation.")
        if "rules" in updates:
            resource = self._reconcile_rules(resource, updates["rules"])
        if self.get_update_parameters(resource):
            self.client.fail_request("The requested security-group state could not be verified.")
        return resource

    def _post_group_metadata(self, source, **values):
        try:
            return self.api.post(self.groups_endpoint, **values)
        except Exception:
            # Native rename saves a new copy before updating host/guest files.
            # Best-effort discovery must not replace the original API error.
            if values["group"] != source and self._rename_copy_exists(values["group"]):
                self.client.mark_changed()
            raise

    def _rename_copy_exists(self, name):
        try:
            groups = self.api.get(self.groups_endpoint)
        except Exception:
            return False
        return isinstance(groups, list) and any(isinstance(row, dict) and row.get("group") == name for row in groups)

    def delete_resource(self, resource):
        if resource["rules"]:
            resource = self._reconcile_rules(resource, [])
        self.call_mutation("Deleting empty firewall security group", self.api.delete, self.groups_endpoint + "/" + quote(resource["group"], safe=""))
        if self.get_resource(readback=True) is not None:
            self.client.fail_request("Security-group deletion could not be verified.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "delete":
            return None
        result = dict(resource or {"group": self.module.params["name"], "rules": []})
        result["group"] = self._target
        if self._comment is not None:
            result["comment"] = self._comment
        desired = self._desired_rules()
        if desired is not None:
            result["rules"] = [dict(rule, pos=i) for i, rule in enumerate(desired)]
        result.pop("digest", None)
        return result


def main():
    rule_options = {name: {"type": "str"} for name in ("comment", "macro", "source", "dest", "proto", "sport", "dport", "icmp_type")}
    rule_options.update(
        {
            "type": {"type": "str", "required": True, "choices": ["in", "out", "forward"]},
            "action": {"type": "str", "required": True, "choices": ["ACCEPT", "DROP", "REJECT"]},
            "enable": {"type": "bool", "default": False},
            "log": {"type": "str", "choices": ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug", "nolog"]},
        }
    )
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(
            {
                "name": {"type": "str", "required": True},
                "rename": {"type": "str"},
                "comment": {"type": "str"},
                "digest": {"type": "str"},
                "force": {"type": "bool", "default": False},
                "rules": {"type": "list", "elements": "dict", "options": rule_options},
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxFirewallSecurityGroupModule(module).execute()


if __name__ == "__main__":
    main()
