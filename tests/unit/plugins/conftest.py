# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stateful API fixtures shared by paired module unit tests."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import hashlib
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
    """Model node configuration with the real shared error and mutation handling."""
    def create(check_mode=False, dns=None, **parameters):
        params = dict(node="pve01", search=None, dns_servers=None, gather_subset=None)
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
            "hosts": {"data": "127.0.0.1 localhost\n192.0.2.1 pve01.example.com pve01\n"},
            "time": {"timezone": "UTC", "time": 1770000000, "localtime": 1770000000},
        }
        records["hosts"]["digest"] = hashlib.sha1(records["hosts"]["data"].encode("utf-8"), usedforsecurity=False).hexdigest()
        dns_endpoint = SimpleNamespace(get=Mock(side_effect=lambda: copy.deepcopy(records["dns"])))

        def put(**values):
            # PVE writes supplied values only, drops empty search, compacts and deduplicates servers.
            result = {"search": values["search"]} if values.get("search") and values["search"] != "0" else {}
            servers = list(dict.fromkeys(values[key] for key in ("dns1", "dns2", "dns3") if values.get(key) not in (None, "", "0.0.0.0")))
            result.update({"dns{0}".format(index): server for index, server in enumerate(servers, 1)})
            records["dns"] = result

        dns_endpoint.put = Mock(side_effect=put)
        time_endpoint = SimpleNamespace(get=Mock(side_effect=lambda: copy.deepcopy(records["time"])))

        def set_timezone(timezone):
            # Model synchronous timezone writes, leaving the UTC clock unchanged.
            offsets = {"UTC": 0, "Etc/UTC": 0, "Europe/Berlin": 3600, "America/New_York": -18000, "Asia/Kathmandu": 20700}
            if timezone not in offsets:
                raise ValueError("No such timezone")
            records["time"].update(timezone=timezone, localtime=records["time"]["time"] + offsets[timezone])

        time_endpoint.put = Mock(side_effect=set_timezone)
        hosts_endpoint = SimpleNamespace(get=Mock(side_effect=lambda: copy.deepcopy(records["hosts"])))

        def post_hosts(data, digest):
            if digest != records["hosts"]["digest"]:
                raise RuntimeError("checksum mismatch (file changed by other user?)")
            records["hosts"] = {"data": data, "digest": hashlib.sha1(data.encode("utf-8"), usedforsecurity=False).hexdigest()}

        hosts_endpoint.post = Mock(side_effect=post_hosts)
        nodes = Mock(return_value=SimpleNamespace(dns=dns_endpoint, hosts=hosts_endpoint, time=time_endpoint))
        nodes.get.return_value = [{"node": "pve01", "status": "online"}, {"node": "pve02", "status": "offline"}]
        api = SimpleNamespace(nodes=nodes, records=records, dns=dns_endpoint, hosts=hosts_endpoint, time=time_endpoint)
        api.get = Mock(side_effect=lambda path: copy.deepcopy(records[path.rsplit("/", 1)[-1]]))
        client._api = api
        client.validate_pve_permissions = Mock()
        return module, client, api
    return create


class IdentityApi:
    """Stateful PVE identity API boundary shared by action/info unit consumers."""

    def __init__(self):
        self.users = {}
        self.groups = {}
        self.roles = {"Administrator": {"VM.Audit", "Sys.Audit", "VM.Console"}}
        self.tokens = {}
        self.domains = [{"realm": "pve"}, {"realm": "pam"}]
        self.get = Mock(side_effect=self._get)
        self.post = Mock(side_effect=self._post)
        self.put = Mock(side_effect=self._put)
        self.delete = Mock(side_effect=self._delete)

    def _get(self, path, **parameters):
        parts = path.split("/")
        if path == "access/domains":
            return copy.deepcopy(self.domains)
        if parts[1] == "roles":
            if len(parts) == 2:
                return [{"roleid": key, "privs": ",".join(sorted(value)), "special": int(key == "Administrator")}
                        for key, value in self.roles.items()]
            return dict.fromkeys(self.roles[parts[2]], 1)
        if parts[1] == "groups":
            if len(parts) == 2:
                return [dict(value, groupid=key, users=",".join(sorted(self._members(key)))) for key, value in self.groups.items()]
            return dict(self.groups[parts[2]], members=sorted(self._members(parts[2])))
        if len(parts) > 3:
            if len(parts) == 4:
                return [dict(value, tokenid=key[1]) for key, value in self.tokens.items() if key[0] == parts[2]]
            return copy.deepcopy(self.tokens[parts[2], parts[4]])
        if len(parts) == 2:
            result = [dict(copy.deepcopy(value), userid=key) for key, value in self.users.items()]
            if not parameters.get("full"):
                for entry in result:
                    entry.pop("groups", None)
            return result
        return copy.deepcopy(self.users[parts[2]])

    def _members(self, groupid):
        return {userid for userid, data in self.users.items() if groupid in data.get("groups", [])}

    def _post(self, path, **parameters):
        parts = path.split("/")
        if parts[1] == "roles":
            self.roles[parameters["roleid"]] = set(filter(None, parameters.get("privs", "").split(",")))
        elif parts[1] == "groups":
            self.groups[parameters["groupid"]] = {key: value for key, value in parameters.items()
                                                  if key == "comment" and value not in ("", "0", None)}
        elif len(parts) > 3:
            data = {"privsep": parameters.get("privsep", 1)}
            data.update({key: value for key, value in parameters.items() if key == "expire" or (key == "comment" and value not in ("", "0", None))})
            self.tokens[parts[2], parts[4]] = data
            return {"value": "EXAMPLE-one-time-secret", "info": copy.deepcopy(data), "full-tokenid": "{0}!{1}".format(parts[2], parts[4])}
        else:
            data = {"enable": parameters.get("enable", 1), "groups": list(filter(None, parameters.get("groups", "").split(",")))}
            data.update({key: value for key, value in parameters.items() if key in ("expire", "email", "firstname", "lastname", "comment")
                         and value not in ("", "0", None)})
            self.users[parameters["userid"]] = data
        return None

    def _put(self, path, **parameters):
        parts = path.split("/")
        if parts[1] == "roles":
            self.roles[parts[2]] = set(filter(None, parameters["privs"].split(",")))
            return None
        if parts[1] == "groups":
            self.groups[parts[2]].update(parameters)
        elif len(parts) > 3:
            self.tokens[parts[2], parts[4]].update({key: value for key, value in parameters.items()
                                                   if key != "comment" or value not in ("", "0", None)})
        else:
            data = dict(parameters)
            if "groups" in data:
                data["groups"] = list(filter(None, data["groups"].split(",")))
            self.users[parts[2]].update(data)
        return None

    def _delete(self, path):
        parts = path.split("/")
        if parts[1] == "roles":
            del self.roles[parts[2]]
        elif parts[1] == "groups":
            del self.groups[parts[2]]
            for user in self.users.values():
                user["groups"] = [group for group in user.get("groups", []) if group != parts[2]]
        elif len(parts) > 3:
            del self.tokens[parts[2], parts[4]]
        else:
            del self.users[parts[2]]
            self.tokens = {key: value for key, value in self.tokens.items() if key[0] != parts[2]}
        return None


@pytest.fixture
def identity_environment(pool_environment):
    """Reuse production error handling and result exceptions for identity APIs."""
    def create(check_mode=False, **parameters):
        module, client, _api = pool_environment(check_mode=check_mode, **parameters)
        api = IdentityApi()
        client._api = api
        return module, client, api
    return create
