# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned, read-only LDAP fixture for the realm integration target; no dependencies."""

import argparse
import json
import socket
import socketserver
import subprocess
import sys
import time
from pathlib import Path


BASE_DN = "dc=ansible,dc=invalid"
BIND_DN = "cn=fixture," + BASE_DN
BIND_PASSWORD = "EXAMPLE-LDAP-fixture"


def encode(tag, value):
    """Encode one BER element used by the fixture's LDAP subset."""
    size = len(value)
    if size < 128:
        length = bytes([size])
    else:
        raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
        length = bytes([128 + len(raw)]) + raw
    return bytes([tag]) + length + value


def number(value, tag=2):
    """Encode a nonnegative LDAP message/result integer."""
    data = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if data[0] & 128:
        data = b"\x00" + data
    return encode(tag, data)


def string(value):
    """Encode a UTF-8 LDAP string."""
    return encode(4, value.encode("utf-8"))


def elements(data):
    """Decode the immediate BER children, rejecting truncated/oversized input."""
    result = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 2:
            raise ValueError("Truncated BER element")
        tag, size = data[offset:offset + 2]
        offset += 2
        if size & 128:
            count = size & 127
            if not 1 <= count <= 3 or offset + count > len(data):
                raise ValueError("Invalid BER length")
            size = int.from_bytes(data[offset:offset + count], "big")
            offset += count
        if size > 65536 or offset + size > len(data):
            raise ValueError("Invalid BER payload")
        result.append((tag, data[offset:offset + size]))
        offset += size
    return result


def matches(expression, attributes):
    """Evaluate the presence/equality/logical filters used by native PVE sync."""
    tag, value = expression
    attrs = {name.lower(): values for name, values in attributes.items()}
    if tag in (160, 161):
        children = [matches(child, attrs) for child in elements(value)]
        return all(children) if tag == 160 else any(children)
    if tag == 162:
        children = elements(value)
        return len(children) == 1 and not matches(children[0], attrs)
    if tag == 135:
        return bool(attrs.get(value.decode("utf-8").lower()))
    if tag == 163:
        parts = elements(value)
        if len(parts) != 2:
            raise ValueError("Invalid equality filter")
        name, expected = (part[1].decode("utf-8").lower() for part in parts)
        return expected in [str(item).lower() for item in attrs.get(name, [])]
    raise ValueError("LDAP fixture supports only native sync presence/equality/logical filters")


def search_entries(dataset, base, scope, expression):
    """Select fixture entries by base/scope and the requested native filter."""
    if not base and scope == 0:
        records = [{"dn": "", "attributes": {"objectClass": ["top"], "defaultNamingContext": [BASE_DN]}}]
    else:
        records = dataset
    selected = []
    for record in records:
        dn = record["dn"].lower()
        parent = base.lower()
        if scope == 0 and dn != parent:
            continue
        if scope != 0 and parent and dn != parent and not dn.endswith("," + parent):
            continue
        if scope == 1 and dn.removesuffix("," + parent).count(","):
            continue
        if matches(expression, record["attributes"]):
            selected.append(record)
    return selected


class LdapHandler(socketserver.BaseRequestHandler):
    """Support LDAPv3 simple bind, search and unbind; reject directory writes."""

    def read_exact(self, count):
        data = b""
        while len(data) < count:
            part = self.request.recv(count - len(data))
            if not part:
                raise EOFError
            data += part
        return data

    def result(self, message_id, operation, code=0):
        payload = number(code, 10) + string("") + string("")
        self.request.sendall(encode(48, number(message_id) + encode(operation, payload)))

    def handle(self):
        self.request.settimeout(5)
        try:
            while True:
                tag, size = self.read_exact(2)
                if tag != 48:
                    return
                if size & 128:
                    count = size & 127
                    if not 1 <= count <= 3:
                        return
                    size = int.from_bytes(self.read_exact(count), "big")
                if size > 65536:
                    return
                packet = elements(self.read_exact(size))
                message_id = int.from_bytes(packet[0][1], "big")
                operation, payload = packet[1]
                if operation == 66:
                    return
                if operation == 96:
                    fields = elements(payload)
                    dn, password = fields[1][1].decode(), fields[2][1].decode()
                    valid = (not dn and not password) or (dn == BIND_DN and password == BIND_PASSWORD)
                    self.result(message_id, 97, 0 if valid else 49)
                elif operation == 99:
                    self.search(message_id, payload)
                else:
                    self.result(message_id, 101, 53)
        except (EOFError, OSError, ValueError, IndexError, UnicodeError):
            return

    def search(self, message_id, payload):
        fields = elements(payload)
        base = fields[0][1].decode()
        scope = int.from_bytes(fields[1][1], "big")
        requested = {value.decode().lower() for _tag, value in elements(fields[7][1])}
        dataset = json.loads((self.server.directory / "entries.json").read_text())
        for entry in search_entries(dataset, base, scope, fields[6]):
            attributes = []
            for name, values in entry["attributes"].items():
                if requested and "*" not in requested and name.lower() not in requested:
                    continue
                attributes.append(encode(48, string(name) + encode(49, b"".join(string(str(value)) for value in values))))
            result = string(entry["dn"]) + encode(48, b"".join(attributes))
            self.request.sendall(encode(48, number(message_id) + encode(100, result)))
        # An absent paged-results response control means there are no further pages.
        self.result(message_id, 101)


class LdapServer(socketserver.ThreadingTCPServer):
    """Allow clean termination of the target-owned server and its connections."""

    allow_reuse_address = True
    daemon_threads = True


def serve(directory):
    """Listen on an ephemeral port until the owned stop file is created."""
    with LdapServer(("0.0.0.0", 0), LdapHandler) as server:
        server.directory = directory
        server.timeout = 0.2
        ready = directory / "ready.tmp"
        ready.write_text(json.dumps({"port": server.server_address[1]}))
        ready.replace(directory / "ready.json")
        while not (directory / "stop").exists():
            server.handle_request()
    (directory / "stopped").touch()


def advertise_host(api_host, explicit):
    """Use an explicit reachable address, or the controller route to the API host."""
    if explicit:
        return explicit
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect((api_host, 443))
        return connection.getsockname()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "serve", "stop"])
    parser.add_argument("--directory", required=True)
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--advertise-host", default="")
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if not directory.is_dir() or not directory.name.startswith("ansible-realm-ldap-"):
        parser.error("Use the target's owned ansible-realm-ldap- temporary directory")
    if args.action == "serve":
        serve(directory)
        return
    if args.action == "stop":
        (directory / "stop").touch()
        for _attempt in range(100):
            if (directory / "stopped").exists() or not (directory / "ready.json").exists():
                print(json.dumps({"stopped": True}))
                return
            time.sleep(0.05)
        raise RuntimeError("Owned LDAP fixture did not acknowledge shutdown")
    host = advertise_host(args.api_host, args.advertise_host)
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", "--directory", str(directory)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    for _attempt in range(100):
        ready = directory / "ready.json"
        if ready.exists():
            print(json.dumps(dict(json.loads(ready.read_text()), host=host)))
            return
        if process.poll() is not None:
            raise RuntimeError("Owned LDAP fixture failed to start")
        time.sleep(0.05)
    (directory / "stop").touch()
    raise RuntimeError("Owned LDAP fixture did not become ready")


if __name__ == "__main__":
    main()
