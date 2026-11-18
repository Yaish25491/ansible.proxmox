#!/usr/bin/python
# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: proxmox_lxc
short_description: Manage a Proxmox VE LXC container
description:
  - Creates, updates, and deletes an LXC container in Proxmox VE.
  - The VMID is the unique identifier used for idempotent lifecycle operations.
version_added: "1.0.0"
author:
  - Steve Fulmer (@stevefulme1)
  - Shahar Golshani (@shahargolshani)
options:
  node:
    description: The Proxmox VE node on which to manage the container.
    type: str
    required: true
  vmid:
    description: The unique container ID. Valid values range from 100 to 999999999.
    type: int
    required: true
  ostemplate:
    description:
      - Template or backup volume used when creating a container; required only when a new container is created.
      - A template name without a storage prefix is looked up on the C(local) storage.
    type: str
  hostname:
    description: Host name configured inside the container.
    type: str
  password:
    description: Root password configured inside the container. Creation only; it is never compared or updated.
    type: str
  pool:
    description: Resource pool to which the new container is added. Creation only.
    type: str
  tags:
    description: Comma-separated container tags. Tags are meta information.
    type: str
  unprivileged:
    description:
      - Create an unprivileged container. Proxmox defaults this to C(1) for templates.
      - This setting is creation-only and should not be modified manually.
    type: bool
  storage:
    description:
      - Storage for the default root disk when O(rootfs) is omitted.
      - The default root disk is 4 GiB.
    type: str
    default: local-lvm
  rootfs:
    description:
      - Root filesystem volume and options in Proxmox property-string format.
      - When omitted during creation, C(<storage>:4) is used.
      - Allocation syntax such as C(local-lvm:4) is matched against the existing volume's storage and size on subsequent runs.
      - Mount options are merged without replacing the existing volume or removing unspecified options.
      - Resizing an existing disk through O(rootfs) is not supported. Migrate or resize the volume separately.
    type: str
  cores:
    description: Number of CPU cores, from 1 to 8192. Proxmox can use all available cores by default.
    type: int
  cpulimit:
    description: CPU usage limit, from 0 to 8192. C(0) means unlimited.
    type: float
  cpuunits:
    description:
      - CPU weight used by the kernel fair scheduler, from 8 to 10000.
      - This range is supported consistently on hosts using either cgroup v1 or cgroup v2.
      - The Proxmox default depends on whether the host uses cgroup v1 or cgroup v2.
    type: int
  memory:
    description: Amount of RAM in MiB, at least 16. Proxmox defaults to 512 MiB.
    type: int
  swap:
    description: Amount of swap in MiB, at least 0. Proxmox defaults to 512 MiB.
    type: int
  network:
    description:
      - Network interfaces to configure on the container.
      - Omit this option to preserve the current network configuration.
      - When specified, the list is authoritative; interfaces omitted from it are deleted.
      - Use an empty list to remove all configured network interfaces.
    type: list
    elements: dict
    suboptions:
      device:
        description: Indexed Proxmox interface parameter, such as C(net0) or C(net1).
        type: str
        required: true
      name:
        description: Container interface name, serialized as C(name=<value>).
        type: str
        required: true
      bridge:
        description: Bridge to attach the interface to.
        type: str
      firewall:
        description: Enable the Proxmox firewall on this interface.
        type: bool
      gw:
        description: IPv4 gateway.
        type: str
      gw6:
        description: IPv6 gateway.
        type: str
      host_managed:
        description: Mark the interface as managed by the host.
        type: bool
      hwaddr:
        description: Interface MAC address.
        type: str
      ip:
        description: IPv4 address and prefix, C(dhcp), or C(manual).
        type: str
      ip6:
        description: IPv6 address and prefix, C(auto), C(dhcp), or C(manual).
        type: str
      link_down:
        description: Create the interface with its link down.
        type: bool
      mtu:
        description: Interface MTU.
        type: int
      rate:
        description: Interface rate limit in MiB/s.
        type: float
      tag:
        description: VLAN tag.
        type: int
      trunks:
        description: Semicolon-separated VLAN trunk IDs.
        type: str
      type:
        description: Network interface type.
        type: str
        choices: [veth]
  nameserver:
    description: DNS server IP address or addresses for the container.
    type: str
  searchdomain:
    description: DNS search domain or domains for the container.
    type: str
  features:
    description:
      - Advanced container features as a comma-separated Proxmox property string.
      - Supported values include C(force_rw_sys=<1|0>), C(fuse=<1|0>), C(keyctl=<1|0>),
        C(mknod=<1|0>), C(mount=<fstype;fstype;...>), and C(nesting=<1|0>).
      - Values are passed through for Proxmox to validate, including features added by future releases.
      - Proxmox only allows C(root@pam) to change feature flags other than C(nesting).
    type: str
  arch:
    description:
      - Container operating-system architecture; defaults to C(amd64) for templates.
      - Currently documented values are C(amd64), C(i386), C(arm64), C(armhf), C(riscv32), and C(riscv64).
      - Values are validated by the Proxmox API, allowing architectures introduced in future releases.
    type: str
  ostype:
    description:
      - Operating-system type used to select container setup scripts.
      - Currently documented values are C(debian), C(devuan), C(ubuntu), C(centos), C(fedora), C(opensuse),
        C(archlinux), C(alpine), C(gentoo), C(nixos), and C(unmanaged).
      - Values are validated by the Proxmox API, allowing operating-system types introduced in future releases.
    type: str
  power_cycle:
    description:
      - C(fail) fails when configuration changes or deletion are requested for a running container.
      - C(on) starts a new container and stops, updates, then starts an existing container when changes are needed.
        An existing stopped container is started even when its configuration is unchanged.
      - C(off) stops a running container before updates or deletion and leaves a present container stopped.
      - C(hotplug) applies configuration while a container is running; Proxmox may defer unsupported live changes.
      - Only C(off) and C(fail) are valid with C(state=absent).
    type: str
    choices: ['on', 'off', fail, hotplug]
    default: fail
  state:
    description: Whether the container should be created or updated, or deleted.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - ansible.proxmox.proxmox_base
  - ansible.proxmox.proxmox_task
  - ansible.proxmox.proxmox_https_options
attributes:
  check_mode:
    support: full
    description: Predicts container configuration and power changes without modifying Proxmox VE.
notes:
  - For Proxmox VE, the authenticated identity must have all privileges of the C(Administrator) role,
    with propagation enabled, on C(/vms); creating a container also requires these privileges on C(/storage).
  - With C(power_cycle=hotplug), pending configuration is used for idempotency checks.
  - Power operations use the Proxmox start and stop APIs and wait for their tasks to finish.
"""

EXAMPLES = r"""
- name: Create a container with the minimum required values
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 1003
    ostemplate: fedora-44-default_20260713_amd64.tar.xz
    storage: local-lvm
    power_cycle: 'on'

- name: Create a container from a backup archive
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 1005
    ostemplate: local:backup/vzdump-lxc-100-2026_11_17-12_00_00.tar.zst
    rootfs: local-lvm:16

- name: Create a fully configured container
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 1004
    ostemplate: fedora-44-default_20260713_amd64.tar.xz
    hostname: fedora-nested
    password: "{{ vault_container_root_password }}"
    pool: "{{ proxmox_pool }}"
    tags: ansible,nested
    unprivileged: true
    rootfs: local-lvm:8
    cores: 4
    cpulimit: 4
    cpuunits: 1000
    memory: 4096
    swap: 1024
    network:
      - device: net0
        name: eth0
        type: veth
        bridge: vmbr0
        firewall: true
        hwaddr: 02:00:00:00:10:04
        ip: 192.0.2.100/24
        gw: 192.0.2.1
        ip6: 2001:db8::100/64
        gw6: 2001:db8::1
        host_managed: false
        link_down: false
        mtu: 1500
        rate: 100
        tag: 20
      - device: net1
        name: eth1
        bridge: vmbr1
        firewall: true
        ip: dhcp
        ip6: dhcp
        trunks: "100;200"
    nameserver: 1.1.1.1
    searchdomain: example.net
    features: nesting=1,keyctl=1
    arch: amd64
    ostype: fedora
    power_cycle: 'on'
    state: present

- name: Configure a container network interface
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_token_id: ansible_user_token
    api_token_secret: "{{ vault_token_secret }}"
    node: pve01
    vmid: 1003
    network:
      - device: net0
        name: eth0
        bridge: vmbr0
        ip: dhcp
        firewall: true
    power_cycle: hotplug

- name: Remove a container
  ansible.proxmox.proxmox_lxc:
    api_host: proxmox.example.com
    api_user: root@pam
    api_password: "{{ vault_password }}"
    node: pve01
    vmid: 1003
    state: absent
    power_cycle: 'off'
"""

RETURN = r"""
lxc:
  description:
    - Container configuration, including the targeted VMID as a string.
    - When the container is absent or has been deleted, contains only C(vmid).
  returned: always
  type: dict
  sample: {"vmid": "1003", "hostname": "fedora", "memory": 512, "swap": 512}
task_upid:
  description: Proxmox task identifier for the last asynchronous create, update, delete, start, or stop operation.
  returned: when the API returns an asynchronous task UPID
  type: str
  sample: "UPID:pve01:003E0E8C:09394C3A:6ADF0AE7:vzcreate:1003:ansible@pve!part:"
"""

import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common.text.formatters import human_to_bytes

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import (
    proxmox_mutually_exclusive,
    proxmox_required_together,
    proxmox_resource_argument_spec,
)
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_guest import ProxmoxGuestModule

FORMATTED_PARAMETERS = ["network"]
FORMATTED_PARAMETER_PREFIXES = {"network": "net"}
FORMATTED_PARAMETER_NAME_FIELDS = {"network": "device"}
LXC_PARAMETER_DEFAULTS = {
    "arch": "amd64",
    "cores": 0,
    "cpulimit": 0,
    "memory": 512,
    "swap": 512,
    "unprivileged": 1,
}
LXC_PARAMETERS = (
    "hostname",
    "password",
    "pool",
    "tags",
    "unprivileged",
    "rootfs",
    "cores",
    "cpulimit",
    "cpuunits",
    "memory",
    "swap",
    "network",
    "features",
    "nameserver",
    "searchdomain",
    "arch",
    "ostype",
)


class ProxmoxLxcModule(ProxmoxGuestModule):
    """Reconcile LXC containers through the shared resource lifecycle helpers."""

    result_key = "lxc"
    guest_type = "lxc"
    config_update_method = "put"

    def _validated_request(self):
        """Validate the shared guest request and LXC numeric limits.

        Returns:
          The requested lifecycle state and power-cycle policy.
        """
        state, policy = super()._validated_request()
        for name, minimum, maximum in (
            ("cores", 1, 8192),
            ("cpulimit", 0, 8192),
            ("cpuunits", 8, 10000),
            ("memory", 16, None),
            ("swap", 0, None),
        ):
            value = self.module.params.get(name)
            if value is not None and not (minimum <= value and (maximum is None or value <= maximum)):
                self.module.fail_json(
                    msg="{0} must be at least {1}{2}.".format(
                        name,
                        minimum,
                        " and at most {0}".format(maximum) if maximum is not None else "",
                    )
                )
        return state, policy

    def get_resource(self):
        """Look up the container by VMID and retrieve its configuration.

        Returns:
            Current or pending configuration, or None when absent.
        """
        node = self.module.params["node"]
        vmid = self.module.params["vmid"]
        self._vm_status = None
        self.client.validate_pve_permissions(self.api, "/vms")
        self.validate_proxmox_resource_exist("node", node, self.api.nodes.get, resource_key="node")
        if not self.validate_proxmox_resource_exist(
            "LXC container",
            vmid,
            self.api.nodes(node).lxc.get,
            resource_key="vmid",
            fail_on_missing=False,
        ):
            return None

        status = self.power.get_status(return_none_on_404=True)
        if status is None:
            return None
        self._vm_status = status.get("status") if isinstance(status, dict) else None
        if self._vm_status not in ("running", "stopped"):
            self.module.fail_json(msg="Unable to determine LXC container power state from current status.")

        options = self.power.configuration_options(self.module.params.get("power_cycle", "fail"))
        return self.call_api("Retrieving LXC container {0} configuration from node {1}".format(vmid, node), self._guest().config.get, **options)

    def _desired_parameters(self, creating=False):
        """Select requested settings and normalize LXC network fields.

        Args:
            creating: Whether creation-only settings should be included.

        Returns:
            Requested API settings without unspecified values.
        """
        excluded = set() if creating else {"password", "pool", "unprivileged"}
        desired = {name: self.module.params[name] for name in LXC_PARAMETERS if name not in excluded and self.module.params.get(name) is not None}
        if "unprivileged" in desired:
            desired["unprivileged"] = int(desired["unprivileged"])
        if isinstance(desired.get("network"), list):
            desired["network"] = [{("host-managed" if key == "host_managed" else key): value for key, value in entry.items()} for entry in desired["network"]]
        return desired

    def _resolve_ostemplate(self):
        """Find the requested template or backup on the selected node.

        Returns:
            The matching Proxmox storage volume identifier and content type.
        """
        requested = self.module.params.get("ostemplate")
        if not requested:
            self.module.fail_json(msg="ostemplate is required when creating an LXC container.")
        storage, separator, path = requested.partition(":")
        template_storage = storage if separator else "local"
        content_type = "backup" if separator and path.startswith("backup/") else "vztmpl"
        contents = self.call_api(
            "Retrieving {0} files from storage {1} on node {2}".format(content_type, template_storage, self.module.params["node"]),
            self.api.nodes(self.module.params["node"]).storage(template_storage).content.get,
            content=content_type,
        )
        requested_name = requested.rsplit("/", 1)[-1]
        for item in contents or []:
            if not isinstance(item, dict):
                continue
            volume_id = item.get("volid", "")
            volume_path = volume_id.partition(":")[2]
            if requested in (volume_id, volume_path) or requested_name in (volume_id.rsplit("/", 1)[-1], item.get("name")):
                return volume_id or requested, content_type
        self.module.fail_json(
            msg="OS template or backup {0} does not exist in storage {1} on node {2}.".format(requested, template_storage, self.module.params["node"])
        )

    def _validate_rootfs_storage(self, rootfs):
        """Require enabled storage supporting container root filesystems.

        Args:
            rootfs: Root filesystem property string to validate.
        """
        volume = rootfs.split(",", 1)[0]
        if volume.startswith("volume="):
            volume = volume[len("volume=") :]
        storage, separator = volume.partition(":")[:2]
        if separator:
            node = self.module.params["node"]
            self.validate_proxmox_resource_exist(
                "root filesystem storage",
                storage,
                lambda: self.api.nodes(node).storage.get(content="rootdir", enabled=1),
                resource_key="storage",
            )

    def _creation_parameters(self):
        """Resolve creation-only options, the template, and initial root disk.

        Returns:
            Validated API parameters for container creation.
        """
        parameters = self._desired_parameters(creating=True)
        if parameters.get("rootfs") is None:
            parameters["rootfs"] = "{0}:4".format(self.module.params["storage"])
        parameters = self.serialize_formatted_parameters(
            parameters,
            FORMATTED_PARAMETERS,
            FORMATTED_PARAMETER_PREFIXES,
            entry_name_fields=FORMATTED_PARAMETER_NAME_FIELDS,
        )
        parameters = self.omit_default_parameters(parameters, LXC_PARAMETER_DEFAULTS)
        parameters["vmid"] = self.module.params["vmid"]
        parameters["ostemplate"], content_type = self._resolve_ostemplate()
        if content_type == "backup":
            parameters["restore"] = 1
        self.client.validate_pve_permissions(self.api, "/storage")
        self._validate_rootfs_storage(parameters["rootfs"])
        return parameters

    def _validate_rootfs_size(self, current_options, size, default_unit="B"):
        """Reject resizing an existing root filesystem through config updates.

        Args:
            current_options: Parsed existing root filesystem options.
            size: Requested size to compare with the existing disk.
            default_unit: Unit used for a requested size without a suffix.
        """
        try:
            matches = human_to_bytes(size, default_unit=default_unit) == human_to_bytes(current_options.get("size"), default_unit="B")
        except (TypeError, ValueError):
            self.module.fail_json(msg="Unable to verify the existing root filesystem size.")
        if not matches:
            self.module.fail_json(msg="Changing rootfs size is not supported; resize the existing volume separately.")

    def _rootfs_update(self, resource, desired):
        """Compare root filesystem options without replaying disk allocation.

        Args:
            resource: Current container configuration.
            desired: Requested root filesystem property string.

        Returns:
            A merged property string, or None when requested fields match.
        """
        options = self._current_formatted_options(desired, "volume", {})
        current_options = self._current_formatted_options(resource.get("rootfs"), "volume", {})
        allocation = re.fullmatch(r"([^:]+):(\d+(?:\.\d+)?)", options.get("volume", ""), flags=re.ASCII)
        if allocation:
            current_volume = current_options.get("volume", "")
            if current_volume.partition(":")[0] != allocation.group(1):
                self.module.fail_json(msg="Changing rootfs storage is not supported; migrate the existing volume separately.")
            self._validate_rootfs_size(current_options, allocation.group(2), default_unit="G")
            options["volume"] = current_volume
        if "size" in options:
            self._validate_rootfs_size(current_options, options.pop("size"))
        return self._formatted_entry_update("rootfs", options, resource, "volume", {})

    def _features_update(self, resource, desired):
        """Compare requested feature flags while preserving unspecified flags.

        Args:
            resource: Current container configuration.
            desired: Comma-separated Proxmox feature property string.

        Returns:
            Feature updates or deletions, or an empty mapping for a no-op.
        """
        if desired == "":
            return {} if resource.get("features") in (None, "") else {"delete": "features"}
        desired_options = self._parse_formatted_options(desired)
        if not desired_options:
            return {"features": desired}
        current_options = self._parse_formatted_options(resource.get("features"))
        if self._formatted_options_equal(current_options, desired_options):
            return {}
        updated_options = dict(current_options)
        updated_options.update(desired_options)
        return {"features": self._serialize_formatted_options(updated_options)}

    @staticmethod
    def _normalize_tags(tags):
        """Compare comma-separated requests with Proxmox's semicolon form."""
        return {tag.strip().lower() for tag in re.split(r"[;,]", tags or "") if tag.strip()}

    def get_update_parameters(self, resource):
        """Compare requested container settings using LXC-safe reset rules.

        Args:
          resource: Existing container configuration.

        Returns:
          Changed settings and optional stale network deletions.
        """
        desired = self._desired_parameters()
        rootfs = desired.pop("rootfs", None)
        features = desired.pop("features", None)
        tags = desired.pop("tags", None)
        updates = self.get_parameter_updates(
            resource,
            {key: value for key, value in desired.items() if key not in FORMATTED_PARAMETERS},
            {key: value for key, value in LXC_PARAMETER_DEFAULTS.items() if key != "memory"},
        )
        if tags is not None and self._normalize_tags(tags) != self._normalize_tags(resource.get("tags")):
            updates["tags"] = tags
        formatted_updates = self.get_formatted_parameter_updates(
            resource,
            desired,
            FORMATTED_PARAMETERS,
            FORMATTED_PARAMETER_PREFIXES,
            entry_name_fields=FORMATTED_PARAMETER_NAME_FIELDS,
        )
        if "delete" in formatted_updates:
            self._merge_formatted_deletions(updates, formatted_updates.pop("delete").split(","))
        updates.update(formatted_updates)
        if features is not None:
            feature_updates = self._features_update(resource, features)
            if "delete" in feature_updates:
                self._merge_formatted_deletions(updates, feature_updates.pop("delete").split(","))
            updates.update(feature_updates)
        if rootfs is not None:
            rootfs_update = self._rootfs_update(resource, rootfs)
            if rootfs_update is not None:
                self._validate_rootfs_storage(rootfs_update)
                updates["rootfs"] = rootfs_update
        if updates:
            self.power.validate_update(getattr(self, "_vm_status", None), self.module.params.get("power_cycle", "fail"))
        return updates


def main():
    """Parse Ansible parameters and reconcile the selected LXC container."""
    module = AnsibleModule(
        argument_spec=proxmox_resource_argument_spec(
            {
                "node": {"type": "str", "required": True},
                "vmid": {"type": "int", "required": True},
                "ostemplate": {"type": "str"},
                "hostname": {"type": "str"},
                "password": {"type": "str", "no_log": True},
                "pool": {"type": "str"},
                "tags": {"type": "str"},
                "features": {"type": "str"},
                "unprivileged": {"type": "bool"},
                "storage": {"type": "str", "default": "local-lvm"},
                "rootfs": {"type": "str"},
                "cores": {"type": "int"},
                "cpulimit": {"type": "float"},
                "cpuunits": {"type": "int"},
                "memory": {"type": "int"},
                "swap": {"type": "int"},
                "network": {
                    "type": "list",
                    "elements": "dict",
                    "options": {
                        "device": {"type": "str", "required": True},
                        "name": {"type": "str", "required": True},
                        "bridge": {"type": "str"},
                        "firewall": {"type": "bool"},
                        "gw": {"type": "str"},
                        "gw6": {"type": "str"},
                        "host_managed": {"type": "bool"},
                        "hwaddr": {"type": "str"},
                        "ip": {"type": "str"},
                        "ip6": {"type": "str"},
                        "link_down": {"type": "bool"},
                        "mtu": {"type": "int"},
                        "rate": {"type": "float"},
                        "tag": {"type": "int"},
                        "trunks": {"type": "str"},
                        "type": {"type": "str", "choices": ["veth"]},
                    },
                },
                "nameserver": {"type": "str"},
                "searchdomain": {"type": "str"},
                "arch": {"type": "str"},
                "ostype": {"type": "str"},
                "power_cycle": {
                    "type": "str",
                    "choices": ["on", "off", "fail", "hotplug"],
                    "default": "fail",
                },
            }
        ),
        supports_check_mode=True,
        required_together=proxmox_required_together(),
        mutually_exclusive=proxmox_mutually_exclusive(),
    )
    ProxmoxLxcModule(module).execute_resource_module()


if __name__ == "__main__":
    main()
