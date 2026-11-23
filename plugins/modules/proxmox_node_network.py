#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: proxmox_node_network
short_description: Manage staged Proxmox VE node interface configuration
version_added: 1.0.0
description:
  - Creates, updates and deletes staged Linux bridge, bond, VLAN and Ethernet configuration on Proxmox VE 9.
  - Identifies an interface by its node and interface name and reconciles only explicitly supplied settings.
  - Does not reload networking, reboot the node or discard node-wide pending changes.
options:
  node:
    description: Node on which to manage configuration.
    type: str
    required: true
  iface:
    description: Interface name. Ethernet configuration refers to a discovered physical NIC, not new hardware.
    type: str
    required: true
  type:
    description:
      - Linux interface type. Required when creating a bridge, bond or VLAN; inferred from an existing interface when omitted.
      - Existing types cannot be changed in place. A physical Ethernet device must already exist. Linux bond names must use C(bond) followed by digits.
    type: str
    choices: [bridge, bond, vlan, eth]
  bridge_ports:
    description:
      - Exact list of ports for a bridge. Omission preserves ports; an empty list configures a portless bridge.
      - Ports must already be discovered. Implicit VLAN-port creation is not performed; configure a VLAN explicitly first.
      - Does not reconfigure port addresses or move ports from another master automatically.
    type: list
    elements: str
  bridge_vlan_aware:
    description: Enable VLAN filtering on a Linux bridge. False removes the native setting and allowed-VLAN list; omission preserves both.
    type: bool
  bridge_vids:
    description: Allowed VLAN IDs or ranges for a VLAN-aware bridge, such as C(2 100-200). Empty string resets the native setting.
    type: str
  bond_mode:
    description: Linux bonding mode. Omission preserves the native setting.
    type: str
    choices: [balance-rr, active-backup, balance-xor, broadcast, '802.3ad', balance-tlb, balance-alb]
  slaves:
    description: Exact list of bond slaves. Omission preserves slaves; an empty list clears them. Does not move existing slave assignments automatically.
    type: list
    elements: str
  bond_primary:
    description: Primary slave for an active-backup bond. Empty string resets it. Must belong to the effective slave list.
    type: str
  bond_xmit_hash_policy:
    description: Transmit hash policy for balance-xor or 802.3ad bonding.
    type: str
    choices: [layer2, 'layer2+3', 'layer3+4']
  vlan_raw_device:
    description: Existing parent interface for a VLAN. Dotted names also encode the parent and must agree with an explicit value.
    type: str
  vlan_id:
    description: VLAN tag from 1 to 4094. Dotted names and C(vlanN) names encode the tag and must agree with an explicit value.
    type: int
  address:
    description: IPv4 address. Supply O(netmask) too when creating an address. Empty string clears the IPv4 address and mask.
    type: str
  netmask:
    description: IPv4 prefix length or dotted mask. Can update an existing IPv4 address's mask. Omit when using O(cidr).
    type: str
  cidr:
    description: IPv4 address with prefix. Mutually exclusive with O(address) and O(netmask). Empty string clears the address and mask.
    type: str
  gateway:
    description: IPv4 default gateway. Empty string removes it. Omission preserves it.
    type: str
  address6:
    description: IPv6 address. Supply O(netmask6) too when creating an address. Empty string clears the IPv6 address and mask.
    type: str
  netmask6:
    description: IPv6 prefix length from 0 to 128. Can update an existing address's prefix. Omit when using O(cidr6).
    type: int
  cidr6:
    description: IPv6 address with prefix. Mutually exclusive with O(address6) and O(netmask6). Empty string clears the address and mask.
    type: str
  gateway6:
    description: IPv6 default gateway. Empty string removes it. Omission preserves it.
    type: str
  mtu:
    description: MTU from 1280 to 65520. Omission preserves it; use O(reset) to remove an explicit MTU.
    type: int
  autostart:
    description: Start the interface on boot. Omission preserves existing configuration and uses the server's creation behavior.
    type: bool
  comments:
    description: Interface comment. Empty string clears it; omission preserves it. Trailing whitespace is normalized; the unpersistable value C(0) is rejected.
    type: str
  reset:
    description:
      - Settings to remove using the API delete directive. Omission or an empty list removes nothing.
      - Cannot also supply the same setting. Address resets remove the associated mask; reset gateways separately when needed.
    type: list
    elements: str
    choices: [address, address6, gateway, gateway6, mtu, comments, bond_primary, bridge_vids, bond_xmit_hash_policy]
    default: []
  state:
    description:
      - Whether the interface configuration should exist in the effective staged configuration.
      - Ethernet deletion removes its configuration stanza, not its hardware or active link. The physical device remains discoverable.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_https_options
  - ansible.proxmox.proxmox_task
attributes:
  check_mode:
    support: full
    description: Reads and validates current configuration and predicts changes without writes.
notes:
  - Requires the Administrator role propagated on C(/nodes/{node}) and C(/sdn/zones/localnetwork); native configuration writes require C(Sys.Modify).
  - Local-network permissions are checked before discovery so ordinary bridge-visibility filtering cannot hide parent/port dependencies.
  - Use an identity with visibility of all node interfaces; narrower child ACL overrides can still restrict native discovery.
  - Changes are staged in C(/etc/network/interfaces.new). Returned configuration is not proof of live activation.
  - Applying or discarding pending changes affects the entire node, including other operators' changes, and must be performed separately.
  - The API has no writable Linux bridge STP parameter. Existing STP configuration is preserved; this module cannot set it.
  - DHCP, SLAAC, loopback and other non-static/non-manual methods cannot be safely rewritten through this API and are refused before updates.
  - Addresses in both families are carried forward on partial updates because the API recalculates configuration methods from submitted addresses.
  - Rejects cycles in the effective bridge, bond and VLAN dependency graph before configuration writes.
  - Refuses deletion of an interface referenced by another interface's bridge ports, bond slaves or VLAN parent.
  - Native network writes serialize the node-wide staged file and may normalize other stanzas. Bond slave handling can enable native slave autostart.
  - Native writes have no optimistic digest parameter. Coordinate concurrent network writers and re-read after applying changes.
  - Removing a stanza for a physical device does not prevent a later native rewrite from emitting a default stanza for that device.
  - Custom VLAN names require Proxmox ifupdown2. OVS, SDN, node-wide reload and rollback are outside this module's scope.
  - The inherited task timeout option is accepted for consistency; staged interface CRUD is synchronous and does not start a task.
"""

EXAMPLES = r"""
- name: Stage an isolated VLAN-aware bridge
  ansible.proxmox.proxmox_node_network:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    iface: vmbr100
    type: bridge
    bridge_ports: []
    bridge_vlan_aware: true
    cidr: 192.0.2.1/24
    autostart: false

- name: Stage a bond using designated spare NICs
  ansible.proxmox.proxmox_node_network:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    iface: bond100
    type: bond
    bond_mode: active-backup
    slaves: [eno3, eno4]
    bond_primary: eno3

- name: Predict a VLAN configuration without writing it
  ansible.proxmox.proxmox_node_network:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    iface: vmbr100.42
    type: vlan
    vlan_raw_device: vmbr100
    vlan_id: 42
  check_mode: true

- name: Remove only an owned interface configuration
  ansible.proxmox.proxmox_node_network:
    api_host: pve.example.com
    api_token_id: ansible@pve!automation
    api_token_secret: "{{ vault_proxmox_token_secret }}"
    node: pve01
    iface: vmbr100.42
    state: absent
"""

RETURN = r"""
interface:
  description:
    - Effective staged configuration after reconciliation, or predicted configuration in check mode.
    - Null after configuration deletion. Predictions recompute address-family methods and CIDR aliases from the desired addressing.
    - Server observations such as active or exists are retained from reads where available and are not synthesized for new interfaces.
    - Native records may contain additional fields, including STP settings that are not writable through this API.
  type: dict
  returned: always
  contains:
    iface:
      description: Interface name.
      type: str
      returned: when present
    type:
      description: Native interface type.
      type: str
      returned: when present
"""

import ipaddress
import re
from graphlib import CycleError, TopologicalSorter
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_resource import ProxmoxResourceModule


CIDR_FORMAT = "{0}/{1}"


class ProxmoxNodeNetworkModule(ProxmoxResourceModule):
    """Reconcile only one interface's staged configuration."""

    result_key = "interface"
    parameter_map = {"bond_primary": "bond-primary", "vlan_raw_device": "vlan-raw-device", "vlan_id": "vlan-id"}
    settings = ("type", "bridge_ports", "bridge_vlan_aware", "bridge_vids", "bond_mode", "slaves", "bond_primary",
                "bond_xmit_hash_policy", "vlan_raw_device", "vlan_id", "address", "netmask", "cidr", "gateway",
                "address6", "netmask6", "cidr6", "gateway6", "mtu", "autostart", "comments")
    scoped_settings = {"bridge": ("bridge_ports", "bridge_vlan_aware", "bridge_vids"),
                       "bond": ("slaves", "bond_mode", "bond_primary", "bond_xmit_hash_policy"),
                       "vlan": ("vlan_raw_device", "vlan_id")}

    @staticmethod
    def _valid_iface(value):
        return isinstance(value, str) and 2 <= len(value) <= 20 and re.fullmatch(r"[a-zA-Z]\w{1,20}([:.]\d+)?", value, flags=re.ASCII) is not None

    def _validate_parameters(self):
        params = self.module.params
        if not isinstance(params.get("node"), str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", params["node"]) is None:
            self.client.fail_request("node must be a nonempty node name without path separators.")
        if not self._valid_iface(params.get("iface")) or params["iface"] == "lo":
            self.client.fail_request("iface must be a valid non-loopback interface name.")
        self._validate_lists()
        self._validate_resets()
        self._validate_strings()
        for name, low, high in (("mtu", 1280, 65520), ("vlan_id", 1, 4094), ("netmask6", 0, 128)):
            value = params.get(name)
            if value is not None and not low <= value <= high:
                self.client.fail_request("{0} must be between {1} and {2}.".format(name, low, high))

    def _validate_lists(self):
        params = self.module.params
        for name in ("bridge_ports", "slaves"):
            values = params.get(name)
            if values is not None and (len(values) != len(set(values)) or any(not self._valid_iface(value) or value == params["iface"] for value in values)):
                self.client.fail_request("{0} must contain distinct interface names other than iface.".format(name))

    def _validate_resets(self):
        params = self.module.params
        for name in params.get("reset") or []:
            aliases = {"address": ("address", "netmask", "cidr"), "address6": ("address6", "netmask6", "cidr6")}
            if any(params.get(key) is not None for key in aliases.get(name, (name,))):
                self.client.fail_request("reset cannot also supply {0}.".format(name))

    def _validate_strings(self):
        params = self.module.params
        for name in self.settings:
            value = params.get(name)
            if isinstance(value, str) and name != "comments" and ("\n" in value or "\r" in value):
                self.client.fail_request("{0} cannot contain line breaks.".format(name))

    def get_resource(self):
        self._validate_parameters()
        params = self.module.params
        self.client.validate_pve_permissions(self.api, "/nodes/{0}".format(params["node"]))
        self.client.validate_pve_permissions(self.api, "/sdn/zones/localnetwork")
        nodes = self.call_api("Listing Proxmox nodes", self.api.get, "nodes")
        if not isinstance(nodes, list) or any(not isinstance(item, dict) or not isinstance(item.get("node"), str) or not item["node"] for item in nodes):
            return self.client.fail_request("Node discovery returned an invalid node list.")
        if not self.validate_proxmox_resource_exist("node", params["node"], lambda: nodes, resource_key="node", fail_on_missing=False):
            return self.client.fail_request("Node {0} does not exist.".format(params["node"]))
        endpoint = "nodes/{0}/network".format(quote(params["node"], safe=""))
        records = self.call_api("Reading staged network configuration", self.api.get, endpoint)
        self._validate_inventory(records)
        self._inventory = records
        selected = next((item for item in records if item["iface"] == params["iface"]), None)
        if selected is not None and selected["type"] not in ("bridge", "bond", "vlan", "eth"):
            return self.client.fail_request("Only Linux bridge, bond, VLAN and Ethernet configuration can be managed.")
        if selected and selected["type"] == "eth" and params.get("state") == "absent" and not selected.get("priority") and not selected.get("autostart"):
            return None
        if selected is not None and params.get("type") is not None and selected["type"] != params["type"]:
            return self.client.fail_request("Changing an existing interface type is not supported.")
        if selected is not None and params.get("state") == "absent":
            self._validate_removal()
        return selected

    def _validate_inventory(self, records):
        if not isinstance(records, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("iface"), str) or not item["iface"]
            or not isinstance(item.get("type"), str) or not item["type"] for item in records
        ):
            return self.client.fail_request("Network API returned an invalid interface list.")
        if len({item["iface"] for item in records}) != len(records):
            return self.client.fail_request("Network API returned duplicate interface names.")
        for record in records:
            self._validate_record_fields(record)

    def _validate_record_fields(self, record):
        self._validate_record_flags(record)
        for name in ("vlan-id", "mtu", "netmask6"):
            if name in record and (isinstance(record[name], bool) or not isinstance(record[name], (str, int)) or not str(record[name]).isdigit()):
                self.client.fail_request("Network API returned an invalid {0} value.".format(name))
        if "netmask" in record and (isinstance(record["netmask"], bool) or not isinstance(record["netmask"], (str, int))):
            self.client.fail_request("Network API returned an invalid netmask value.")
        names = ("bridge_ports", "slaves", "vlan-raw-device", "address", "address6", "gateway", "gateway6", "method", "method6", "bridge_vids")
        for name in names:
            if name in record and not isinstance(record[name], str):
                self.client.fail_request("Network API returned an invalid {0} value.".format(name))

    def _validate_record_flags(self, record):
        for name in ("autostart", "bridge_vlan_aware"):
            if name in record and (not isinstance(record[name], (str, int, bool)) or record[name] not in (0, 1, "0", "1")):
                self.client.fail_request("Network API returned an invalid {0} flag.".format(name))

    def _validate_removal(self):
        iface = self.module.params["iface"]
        for entry in self._inventory:
            if entry["iface"] == iface:
                continue
            references = (str(entry.get("bridge_ports", "")) + " " + str(entry.get("slaves", ""))).split()
            parent = entry.get("vlan-raw-device")
            if entry["type"] == "vlan" and not parent and "." in entry["iface"]:
                parent = entry["iface"].rsplit(".", 1)[0]
            if iface in references or iface == parent:
                self.client.fail_request("Interface {0} is referenced by {1}; remove dependent configuration first.".format(iface, entry["iface"]))

    def _input_address(self, current, suffix):
        params = self.module.params
        address, mask, cidr = (name + suffix for name in ("address", "netmask", "cidr"))
        if params.get(cidr) is not None:
            return params[cidr]
        if params.get(address) is None and params.get(mask) is None:
            return None
        ip = params[address] if params.get(address) is not None else current.get(address)
        prefix = params[mask] if params.get(mask) is not None else current.get(mask)
        if ip == "":
            if params.get(mask) is not None:
                raise ValueError("A cleared address cannot also supply its mask.")
            return ""
        if ip is None or prefix is None:
            raise ValueError("{0} and {1} are required together for a new address.".format(address, mask))
        return CIDR_FORMAT.format(ip, prefix)

    def _address_parameters(self, current, desired, removals):
        for suffix, version in (("", 4), ("6", 6)):
            self._family_parameters(current, desired, removals, suffix, version)

    def _family_parameters(self, current, desired, removals, suffix, version):
        address, mask, gateway = (name + suffix for name in ("address", "netmask", "gateway"))
        value = self._input_address(current, suffix)
        if value == "":
            removals.update((address, mask))
        elif value is not None:
            network = ipaddress.ip_interface(value)
            if network.version != version or "%" in value:
                raise ValueError("{0} requires an unscoped IPv{1} address.".format(address, version))
            desired[address], desired[mask] = str(network.ip), network.network.prefixlen
        if address in removals:
            removals.update((mask, "cidr" + suffix))
        if self.module.params.get(gateway):
            value = ipaddress.ip_address(self.module.params[gateway])
            if value.version != version or "%" in str(value):
                raise ValueError("{0} requires an unscoped IPv{1} address.".format(gateway, version))
            desired[gateway] = str(value)

    @staticmethod
    def _normalize_vids(value):
        parts = re.split(r"[;,\s]+", value.strip())
        identifiers = set()
        for part in parts:
            match = re.fullmatch(r"(\d+)(?:-(\d+))?", part, flags=re.ASCII)
            if match is None:
                raise ValueError("bridge_vids must contain VLAN IDs or ranges between 1 and 4094.")
            start, end = int(match[1]), int(match[2] or match[1])
            if not 1 <= start <= end <= 4094:
                raise ValueError("bridge_vids must contain VLAN IDs or ranges between 1 and 4094.")
            identifiers.update(range(start, end + 1))
        return ProxmoxNodeNetworkModule._vid_ranges(sorted(identifiers))

    @staticmethod
    def _vid_ranges(identifiers):
        ranges = []
        start = previous = identifiers[0]
        for identifier in identifiers[1:]:
            if identifier == previous + 1:
                previous = identifier
                continue
            ranges.append(str(start) if start == previous else "{0}-{1}".format(start, previous))
            start = previous = identifier
        ranges.append(str(start) if start == previous else "{0}-{1}".format(start, previous))
        return " ".join(ranges)

    def _serialize_setting(self, name, value):
        if name in ("bridge_ports", "slaves"):
            return " ".join(sorted(value))
        if name == "bridge_vids" and value:
            try:
                return self._normalize_vids(value)
            except ValueError as error:
                return self.client.fail_request(str(error))
        if name == "comments":
            if "<%" in value or "${" in value:
                return self.client.fail_request("comments cannot contain template directives.")
            value = "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").splitlines()).rstrip()
            if value == "0":
                return self.client.fail_request("comments='0' cannot be persisted by Proxmox; use an empty string to clear.")
            return value
        return int(value) if isinstance(value, bool) else value

    def _validate_scope(self, kind, removals):
        params = self.module.params
        for owner, settings in self.scoped_settings.items():
            if owner != kind and any(params.get(key) is not None or self.parameter_map.get(key, key) in removals for key in settings):
                self.client.fail_request("{0} settings require type={0}.".format(owner))

    def _desired_parameters(self, resource):
        params = self.module.params
        kind = params.get("type") or resource.get("type")
        if kind is None:
            return self.client.fail_request("type is required to create an interface.")
        if not resource and kind == "eth":
            return self.client.fail_request("Ethernet configuration requires an existing discovered physical interface.")
        if kind == "bond" and re.fullmatch(r"bond\d+", params["iface"], flags=re.ASCII) is None:
            return self.client.fail_request("Linux bond names must use bond followed by digits for native type inference.")
        removals = {self.parameter_map.get(name, name) for name in params.get("reset") or []}
        desired = self._configured_settings(kind, removals)
        try:
            self._address_parameters(resource, desired, removals)
        except ValueError as error:
            return self.client.fail_request(str(error))
        self._validate_scope(kind, removals)
        effective = self.apply_parameter_updates(resource, dict(desired, delete=",".join(sorted(removals))))
        self._validate_topology(kind, effective)
        if removals:
            desired["delete"] = ",".join(sorted(removals))
        return desired

    def _configured_settings(self, kind, removals):
        params = self.module.params
        desired = {"type": kind}
        for name in self.settings:
            value = params.get(name)
            if value is None or name in ("type", "address", "netmask", "cidr", "address6", "netmask6", "cidr6"):
                continue
            key = self.parameter_map.get(name, name)
            value = self._serialize_setting(name, value)
            if name == "bridge_vlan_aware" and not value:
                removals.update(("bridge_vlan_aware", "bridge_vids"))
                continue
            if value == "" and name not in ("bridge_ports", "slaves"):
                removals.add(key)
            else:
                desired[key] = value
        return desired

    def _validate_topology(self, kind, effective):
        names = {entry["iface"] for entry in self._inventory}
        iface = self.module.params["iface"]
        for name in ("bridge_ports", "slaves"):
            for port in str(effective.get(name, "")).split():
                if port not in names or port == iface:
                    self.client.fail_request("{0} references an unavailable interface: {1}.".format(name, port))
        self._validate_port_ownership(effective)
        self._validate_dependency_cycles(effective)
        if kind == "bridge" and self.module.params.get("bridge_vids") and not effective.get("bridge_vlan_aware"):
            self.client.fail_request("bridge_vids requires an effectively VLAN-aware bridge.")
        if kind == "bond":
            self._validate_bond(effective)
        if kind != "vlan":
            return
        self._validate_vlan(effective)

    def _validate_port_ownership(self, effective):
        desired_ports = set((str(effective.get("bridge_ports", "")) + " " + str(effective.get("slaves", ""))).split())
        for entry in self._inventory:
            if entry["iface"] == self.module.params["iface"]:
                continue
            assigned = set((str(entry.get("bridge_ports", "")) + " " + str(entry.get("slaves", ""))).split())
            if desired_ports & assigned:
                self.client.fail_request("Requested ports are already assigned to {0}; detach them explicitly first.".format(entry["iface"]))

    @staticmethod
    def _dependencies(record):
        ports = (str(record.get("bridge_ports", "")) + " " + str(record.get("slaves", ""))).split()
        if record["type"] == "vlan":
            parent = record.get("vlan-raw-device")
            if not parent and "." in record["iface"]:
                parent = record["iface"].rsplit(".", 1)[0]
            if parent:
                ports.append(parent)
        return ports

    def _validate_dependency_cycles(self, effective):
        record = dict(effective, iface=self.module.params["iface"])
        graph = {entry["iface"]: self._dependencies(entry) for entry in self._inventory}
        graph[record["iface"]] = self._dependencies(record)
        try:
            TopologicalSorter(graph).prepare()
        except CycleError:
            self.client.fail_request("Interface configuration contains a dependency cycle; no changes were written.")

    def _validate_bond(self, effective):
        if effective.get("bond_xmit_hash_policy") and effective.get("bond_mode", "balance-rr") not in ("balance-xor", "802.3ad"):
            self.client.fail_request("bond_xmit_hash_policy requires balance-xor or 802.3ad mode; reset it before changing mode.")
        if effective.get("bond-primary"):
            if effective["bond-primary"] not in str(effective.get("slaves", "")).split() or effective.get("bond_mode", "balance-rr") != "active-backup":
                self.client.fail_request("bond_primary requires active-backup and must be an effective slave.")

    def _validate_vlan(self, effective):
        names = {entry["iface"] for entry in self._inventory}
        iface = self.module.params["iface"]
        parent, tag = effective.get("vlan-raw-device"), effective.get("vlan-id")
        parent, tag = self._vlan_identity(iface, parent, tag)
        if parent not in names or parent == iface or tag is None or not 1 <= int(tag) <= 4094:
            self.client.fail_request("VLAN configuration requires an existing parent and tag between 1 and 4094.")

    def _vlan_identity(self, iface, parent, tag):
        match = re.fullmatch(r"(.+)\.(\d+)", iface, flags=re.ASCII)
        if match:
            if parent is not None and parent != match[1] or tag is not None and int(tag) != int(match[2]):
                self.client.fail_request("VLAN parent and tag must agree with the dotted interface name.")
            parent, tag = match[1], int(match[2])
        elif re.fullmatch(r"vlan\d+", iface, flags=re.ASCII):
            inferred = int(iface[4:])
            if tag is not None and int(tag) != inferred:
                self.client.fail_request("vlan_id must agree with the vlanN interface name.")
            tag = inferred
        return parent, tag

    @staticmethod
    def _comparable(resource):
        result = dict(resource)
        for key in ("bridge_ports", "slaves"):
            result[key] = " ".join(sorted(str(result.get(key, "")).split()))
        for key in ("autostart", "bridge_vlan_aware"):
            result[key] = int(bool(int(result.get(key, 0))))
        for suffix in ("", "6"):
            address, mask = "address" + suffix, "netmask" + suffix
            if result.get(address) and result.get(mask) is not None:
                network = ipaddress.ip_interface(CIDR_FORMAT.format(result[address], result[mask]))
                result[address], result[mask] = str(network.ip), network.network.prefixlen
        for key in ("gateway", "gateway6"):
            if result.get(key):
                result[key] = str(ipaddress.ip_address(result[key]))
        if result.get("bridge_vids"):
            result["bridge_vids"] = ProxmoxNodeNetworkModule._normalize_vids(result["bridge_vids"])
        result["comments"] = str(result.get("comments", "")).rstrip()
        return result

    def get_update_parameters(self, resource):
        desired = self._desired_parameters(resource)
        try:
            current = self._comparable(resource)
        except ValueError as error:
            return self.client.fail_request("Existing network configuration cannot be safely compared: {0}".format(error))
        deleted = [key for key in desired.pop("delete", "").split(",") if self._needs_reset(key, resource)]
        updates = self.get_parameter_updates(current, desired, {})
        if deleted:
            updates["delete"] = ",".join(sorted(deleted))
        if resource["type"] == "eth" and not resource.get("priority"):
            updates["type"] = "eth"
        if not updates:
            return {}
        self._preserve_address_methods(resource, updates, deleted)
        return updates

    def _needs_reset(self, key, resource):
        if not key:
            return False
        if key in ("cidr", "cidr6") and resource.get("address" + key[4:]):
            return True
        if key == "bridge_vids" and resource.get(key) and self.module.params.get("bridge_vlan_aware") is not False:
            return self._normalize_vids(resource[key]) != "2-4094"
        return resource.get(key) not in (None, "")

    def _preserve_address_methods(self, resource, updates, deleted):
        for suffix in ("", "6"):
            if resource.get("method" + suffix, "manual") not in ("manual", "static"):
                return self.client.fail_request("Cannot safely update DHCP, SLAAC or other non-static/non-manual interface methods through this API.")
            for key in ("address" + suffix, "netmask" + suffix):
                if key not in deleted and key not in updates and resource.get(key) is not None:
                    updates[key] = resource[key]

    def _endpoint(self):
        return "nodes/{0}/network/{1}".format(quote(self.module.params["node"], safe=""), quote(self.module.params["iface"], safe=""))

    def create_resource(self):
        desired = self._desired_parameters({})
        desired.pop("delete", None)
        self.call_mutation("Creating staged network configuration", self.api.post, self._endpoint().rsplit("/", 1)[0],
                           iface=self.module.params["iface"], **desired)
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Created network configuration was not found during verification.")
        return result

    def update_resource(self, resource, updates):
        self.call_mutation("Updating staged network configuration", self.api.put, self._endpoint(), **updates)
        result = self.get_resource()
        if result is None:
            self.client.fail_request("Updated network configuration was not found during verification.")
        return result

    def delete_resource(self, resource):
        self.call_mutation("Deleting staged network configuration", self.api.delete, self._endpoint())
        if self.get_resource() is not None:
            self.client.fail_request("Deleted interface configuration remains present during verification.")
        return None

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        if operation == "create":
            desired = self._desired_parameters({})
            desired.pop("delete", None)
            result = dict(desired, iface=self.module.params["iface"])
        else:
            result = super().get_check_mode_resource(operation, resource, updates)
        if result is None:
            return None
        for suffix in ("", "6"):
            self._predict_address_family(result, suffix)
        if result.get("type") == "bridge":
            if result.get("bridge_vlan_aware"):
                result.setdefault("bridge_vids", "2-4094")
            else:
                result.pop("bridge_vids", None)
        return result

    @staticmethod
    def _predict_address_family(result, suffix):
        address, mask, cidr = (name + suffix for name in ("address", "netmask", "cidr"))
        if result.get(address) and result.get(mask) is not None:
            network = ipaddress.ip_interface(CIDR_FORMAT.format(result[address], result[mask]))
            result[cidr] = CIDR_FORMAT.format(network.ip, network.network.prefixlen)
            result["method" + suffix] = "static"
        else:
            result.pop(cidr, None)
            result["method" + suffix] = "manual"


def main():
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec({
            "node": {"type": "str", "required": True}, "iface": {"type": "str", "required": True},
            "type": {"type": "str", "choices": ["bridge", "bond", "vlan", "eth"]},
            "bridge_ports": {"type": "list", "elements": "str"}, "bridge_vlan_aware": {"type": "bool"}, "bridge_vids": {"type": "str"},
            "bond_mode": {"type": "str", "choices": ["balance-rr", "active-backup", "balance-xor", "broadcast", "802.3ad", "balance-tlb", "balance-alb"]},
            "slaves": {"type": "list", "elements": "str"}, "bond_primary": {"type": "str"},
            "bond_xmit_hash_policy": {"type": "str", "choices": ["layer2", "layer2+3", "layer3+4"]},
            "vlan_raw_device": {"type": "str"}, "vlan_id": {"type": "int"},
            "address": {"type": "str"}, "netmask": {"type": "str"}, "cidr": {"type": "str"}, "gateway": {"type": "str"},
            "address6": {"type": "str"}, "netmask6": {"type": "int"}, "cidr6": {"type": "str"}, "gateway6": {"type": "str"},
            "mtu": {"type": "int"}, "autostart": {"type": "bool"}, "comments": {"type": "str"},
            "reset": {"type": "list", "elements": "str", "default": [],
                      "choices": ["address", "address6", "gateway", "gateway6", "mtu", "comments", "bond_primary", "bridge_vids", "bond_xmit_hash_policy"]},
        }),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive([["cidr", "address"], ["cidr", "netmask"], ["cidr6", "address6"], ["cidr6", "netmask6"]]),
    )
    ProxmoxNodeNetworkModule(module).execute()


if __name__ == "__main__":
    main()
