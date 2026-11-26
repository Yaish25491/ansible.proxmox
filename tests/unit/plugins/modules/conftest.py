# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native firewall storage semantics for paired resource/info module tests."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import hashlib
import ipaddress
import json
import re
from collections import defaultdict
from urllib.parse import unquote
from unittest.mock import Mock

import pytest
from proxmoxer.core import ResourceException


class FirewallApi:
    def __init__(self):
        self.aliases = defaultdict(dict)
        self.ipsets = defaultdict(dict)
        self.groups = {}
        self.nodes = [{"node": "pve01"}, {"node": "pve02"}]
        self.guests = [{"node": "pve01", "type": "qemu", "vmid": 100}, {"node": "pve01", "type": "lxc", "vmid": 101}]
        self.extra_references = []
        self.get = Mock(side_effect=self._get)
        self.post = Mock(side_effect=self._post)
        self.put = Mock(side_effect=self._put)
        self.delete = Mock(side_effect=self._delete)

    @staticmethod
    def digest(rows):
        data = json.dumps(rows, sort_keys=True).encode()
        return hashlib.sha1(data, usedforsecurity=False).hexdigest()

    def _rows(self, records):
        digest = self.digest(records)
        return [dict(copy.deepcopy(row), digest=digest) for row in records]

    @staticmethod
    def _path(path):
        match = re.fullmatch(r"(.*/firewall)/(aliases|ipset|groups|refs)(?:/(.*))?", path)
        if match is None:
            raise AssertionError("Unexpected firewall endpoint: " + path)
        root, kind, suffix = match.groups()
        return root, kind, [unquote(part) for part in suffix.split("/")] if suffix else []

    def _get(self, path, **parameters):
        if path == "nodes":
            return copy.deepcopy(self.nodes)
        if path == "cluster/resources":
            assert parameters == {"type": "vm"}
            return copy.deepcopy(self.guests)
        root, kind, parts = self._path(path)
        if kind == "aliases":
            return self._rows([dict(data, name=name) for name, data in sorted(self.aliases[root].items(), key=lambda item: item[0].lower())])
        if kind == "groups":
            if not parts:
                return self._rows([dict(group=name, **{k: v for k, v in row.items() if k != "rules"}) for name, row in sorted(self.groups.items())])
            return [dict(row, pos=i) for i, row in enumerate(self._rows(self.groups[parts[0]]["rules"]))]
        if kind == "ipset":
            if not parts:
                return self._rows([dict(name=name, **{k: v for k, v in row.items() if k != "entries"}) for name, row in sorted(self.ipsets[root].items())])
            return self._rows(sorted(self.ipsets[root][parts[0]]["entries"], key=lambda row: row["cidr"]))
        references = list(self.extra_references)
        for origin, context in [("dc", "cluster/firewall")] + ([] if root == "cluster/firewall" else [("guest", root)]):
            for resource, definitions in [("alias", self.aliases[context]), ("ipset", self.ipsets[context])]:
                references.extend(
                    dict(
                        type=resource,
                        name=name,
                        scope=origin,
                        ref=("+" if resource == "ipset" else "") + name,
                        **{k: v for k, v in row.items() if k == "comment"}
                    )
                    for name, row in definitions.items()
                )
        return [copy.deepcopy(row) for row in references if row["type"] == parameters["type"]]

    @staticmethod
    def _comment(values, target):
        if "comment" in values:
            target.pop("comment", None)
            value = values["comment"]
            if value not in (None, "", "0"):
                target["comment"] = value.strip()

    def _assert_digest(self, path, values):
        rows = self._get(path)
        expected = rows[0]["digest"] if rows else None
        if values.get("digest") != expected:
            raise ResourceException(409, "Conflict", "configuration digest changed")

    def _post(self, path, **values):
        root, kind, parts = self._path(path)
        if kind == "aliases":
            address = ipaddress.ip_interface(values["cidr"])
            row = {"cidr": str(address.ip) if address.network.prefixlen == address.max_prefixlen else str(address)}
            if any(name.lower() == values["name"].lower() for name in self.aliases[root]):
                raise ResourceException(400, "Conflict", "alias already exists")
            self._comment(values, row)
            self.aliases[root][values["name"]] = row
            return
        if kind == "groups" and parts:
            row = {k: v for k, v in values.items() if k not in ("digest", "pos", "comment")}
            self._comment(values, row)
            row.setdefault("enable", 0)
            self.groups[parts[0]]["rules"].insert(0, row)
            return
        if kind == "ipset" and parts:
            row = {"cidr": values["cidr"]}
            if values.get("nomatch"):
                row["nomatch"] = 1
            self._comment(values, row)
            entries = self.ipsets[root][parts[0]]["entries"]
            if any(entry["cidr"] == row["cidr"] for entry in entries):
                raise ResourceException(400, "Conflict", "member already exists")
            entries.insert(0, row)
            return
        key = "group" if kind == "groups" else "name"
        records = self.groups if kind == "groups" else self.ipsets[root]
        old = values.get("rename")
        name = values[key]
        if old is not None:
            self._assert_digest(path, values)
            if name != old and name in records:
                raise ResourceException(400, "Conflict", "rename destination exists")
            row = records.pop(old)
        else:
            if name in records:
                raise ResourceException(400, "Conflict", "resource already exists")
            row = {"rules" if kind == "groups" else "entries": []}
        self._comment(values, row)
        records[name] = row

    def _put(self, path, **values):
        root, kind, parts = self._path(path)
        if kind == "aliases":
            self._assert_digest(root + "/aliases", values)
            key = next(name for name in self.aliases[root] if name.lower() == parts[0].lower())
            target = values.get("rename", parts[0])
            row = self.aliases[root].pop(key)
            if "cidr" in values:
                address = ipaddress.ip_interface(values["cidr"])
                row["cidr"] = str(address.ip) if address.network.prefixlen == address.max_prefixlen else str(address)
            self._comment(values, row)
            self.aliases[root][target] = row
            return
        self._assert_digest(path.rsplit("/", 1)[0], values)
        if kind == "ipset":
            row = next(row for row in self.ipsets[root][parts[0]]["entries"] if row["cidr"] == parts[1])
            row.pop("nomatch", None)
            if values.get("nomatch"):
                row["nomatch"] = 1
            # Native update resets BOTH fields when omitted.
            self._comment({"comment": values.get("comment")}, row)
            return
        rules = self.groups[parts[0]]["rules"]
        position = int(parts[1])
        if "moveto" in values:
            # Native moveto inserts before the ORIGINAL target index, or appends
            # when the target is at/after the original list length.
            moving = rules[position]
            reordered = []
            for index, rule in enumerate(rules):
                if index == position:
                    continue
                if index == values["moveto"]:
                    reordered.append(moving)
                reordered.append(rule)
            if values["moveto"] >= len(rules):
                reordered.append(moving)
            rules[:] = reordered
            return
        row = rules[position]
        row.update({k: v for k, v in values.items() if k not in ("digest", "delete", "comment")})
        self._comment(values, row)
        for key in values.get("delete", "").split(","):
            row.pop(key, None)

    def _delete(self, path, **values):
        root, kind, parts = self._path(path)
        if kind == "aliases":
            self._assert_digest(root + "/aliases", values)
            key = next(name for name in self.aliases[root] if name.lower() == parts[0].lower())
            del self.aliases[root][key]
        elif kind == "groups":
            if len(parts) == 1:
                if self.groups[parts[0]]["rules"]:
                    raise ResourceException(400, "Not empty", "group still has rules")
                del self.groups[parts[0]]
            else:
                self._assert_digest(path.rsplit("/", 1)[0], values)
                self.groups[parts[0]]["rules"].pop(int(parts[1]))
        elif len(parts) == 1:
            if self.ipsets[root][parts[0]]["entries"] and not values.get("force"):
                raise ResourceException(400, "Not empty", "IP set still has members")
            del self.ipsets[root][parts[0]]
        else:
            self._assert_digest(path.rsplit("/", 1)[0], values)
            entries = self.ipsets[root][parts[0]]["entries"]
            entries[:] = [row for row in entries if row["cidr"] != parts[1]]


@pytest.fixture
def firewall_environment(pool_environment):
    def create(check_mode=False, **parameters):
        module, client, _api = pool_environment(check_mode=check_mode)
        module.params = dict(
            scope="cluster",
            node=None,
            vmid=None,
            name="example_net",
            state="present",
            rename=None,
            cidr=None,
            comment=None,
            digest=None,
            entries=None,
            rules=None,
            force=False,
            include_references=False,
            include_entries=True,
            include_rules=True,
        )
        module.params.update(parameters)
        api = FirewallApi()
        client._api = api
        return module, client, api

    return create


@pytest.fixture
def firewall_run():
    def run(handler_class, module, client):
        handler = handler_class(module, client)
        with pytest.raises(module.Exit) as result:
            handler.execute()
        return result.value.args[0]

    return run
