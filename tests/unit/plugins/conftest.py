# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stateful API fixtures shared by paired module unit tests."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import ProxmoxClient


class PoolExit(Exception):
    """Capture an Ansible result."""


class PoolFail(Exception):
    """Capture an Ansible failure."""


class PoolApi:
    """Minimal stateful API with distinct reads and mutation recording."""

    def __init__(self):
        self.records = {}
        self.reads = []
        self.writes = []
        self.guests = [{"vmid": 100, "type": "qemu", "id": "qemu/100", "node": "pve01"},
                       {"vmid": 101, "type": "lxc", "id": "lxc/101", "node": "pve01"}]
        self.pools = self
        self.cluster = SimpleNamespace(resources=SimpleNamespace(get=Mock(side_effect=lambda **_kw: copy.deepcopy(self.guests))))
        self.storage = SimpleNamespace(get=Mock(return_value=[{"storage": "local"}, {"storage": "local-lvm"}]))

    def get(self, **parameters):
        self.reads.append(parameters)
        poolid = parameters.get("poolid")
        if poolid is None:
            return [{"poolid": key, "comment": value.get("comment", "")} for key, value in self.records.items()]
        pool = copy.deepcopy(self.records[poolid])
        if parameters.get("type"):
            pool["members"] = [member for member in pool["members"] if member["type"] == parameters["type"]]
        return [pool]

    def post(self, **parameters):
        self.writes.append(("POST", parameters))
        self.records[parameters["poolid"]] = dict(parameters, members=[])

    def put(self, **parameters):
        self.writes.append(("PUT", parameters))
        pool = self.records[parameters["poolid"]]
        if "comment" in parameters:
            pool["comment"] = parameters["comment"]
        for option, key in (("vms", "vmid"), ("storage", "storage")):
            for value in filter(None, parameters.get(option, "").split(",")):
                value = int(value) if option == "vms" else value
                if parameters.get("delete"):
                    pool["members"] = [member for member in pool["members"] if member.get(key) != value]
                else:
                    if option == "vms":
                        member = next(dict(guest) for guest in self.guests if guest["vmid"] == value)
                        if parameters.get("allow-move"):
                            for other in self.records.values():
                                other["members"] = [entry for entry in other["members"] if entry.get("vmid") != value]
                        next(guest for guest in self.guests if guest["vmid"] == value)["pool"] = parameters["poolid"]
                    else:
                        member = {"type": "storage", "storage": value}
                    pool["members"].append(member)

    def delete(self, **parameters):
        self.writes.append(("DELETE", parameters))
        del self.records[parameters["poolid"]]


@pytest.fixture
def pool_environment():
    """Build an isolated module/client/API with the real shared error handling."""
    def create(check_mode=False, records=None, **parameters):
        params = {"poolid": "applications", "state": "present", "comment": None, "vms": None,
                  "storage": None, "membership_mode": "exact", "allow_move": False}
        params.update(parameters)
        module = SimpleNamespace(params=params, check_mode=check_mode, Exit=PoolExit, Fail=PoolFail)

        def exit_json(**result):
            raise PoolExit(result)

        def fail_json(**result):
            raise PoolFail(result)

        module.exit_json = exit_json
        module.fail_json = fail_json
        client = ProxmoxClient(module)
        api = PoolApi()
        api.records = copy.deepcopy(records or {})
        client._api = api
        client.validate_pve_permissions = Mock()
        return module, client, api
    return create


@pytest.fixture
def node_environment():
    """Model DNS replacement semantics with the real shared error and mutation handling."""
    def create(check_mode=False, dns=None, **parameters):
        params = dict(node="pve01", state="present", search=None, dns_servers=None, gather_subset=None)
        params.update(parameters)
        module = SimpleNamespace(params=params, check_mode=check_mode, Exit=PoolExit, Fail=PoolFail)

        def exit_json(**result):
            raise PoolExit(result)

        def fail_json(**result):
            raise PoolFail(result)

        module.exit_json = exit_json
        module.fail_json = fail_json
        client = ProxmoxClient(module)
        records = {
            "dns": copy.deepcopy(dns if dns is not None else {"search": "example.com", "dns1": "192.0.2.53", "dns2": "2001:db8::53"}),
            "status": {"uptime": 123, "cpuinfo": {"cpus": 4}},
            "hosts": {"data": "127.0.0.1 localhost\n192.0.2.1 pve01.example.com pve01\n", "digest": "abc123"},
            "time": {"timezone": "UTC", "time": 1770000000, "localtime": 1770000000},
        }
        dns_endpoint = SimpleNamespace(get=Mock(side_effect=lambda: copy.deepcopy(records["dns"])))

        def put(**values):
            # PVE writes supplied values only, drops empty search, compacts and deduplicates servers.
            result = {"search": values["search"]} if values.get("search") and values["search"] != "0" else {}
            servers = list(dict.fromkeys(values[key] for key in ("dns1", "dns2", "dns3") if values.get(key) not in (None, "", "0.0.0.0")))
            result.update({"dns{0}".format(index): server for index, server in enumerate(servers, 1)})
            records["dns"] = result

        dns_endpoint.put = Mock(side_effect=put)
        nodes = Mock(return_value=SimpleNamespace(dns=dns_endpoint))
        nodes.get.return_value = [{"node": "pve01", "status": "online"}, {"node": "pve02", "status": "offline"}]
        api = SimpleNamespace(nodes=nodes, records=records, dns=dns_endpoint)
        api.get = Mock(side_effect=lambda path: copy.deepcopy(records[path.rsplit("/", 1)[-1]]))
        client._api = api
        client.validate_pve_permissions = Mock()
        return module, client, api
    return create
