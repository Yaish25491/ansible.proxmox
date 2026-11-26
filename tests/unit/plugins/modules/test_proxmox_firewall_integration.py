# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]


def run_assertions(tmp_path, tasks):
    """Execute the target's actual guard expressions through Ansible without API calls."""
    play = tmp_path / "guards.yml"
    play.write_text(yaml.safe_dump([{"hosts": "all", "gather_facts": False, "tasks": tasks}], sort_keys=False))
    environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"), ANSIBLE_COLLECTIONS_PATH=str(ROOT.parents[2]))
    result = subprocess.run(
        [sys.executable, "-m", "ansible.cli.playbook", "-i", "localhost,", "-c", "local", str(play)],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "kind,key,record,unexpected",
    [
        ("alias", "aliases", {"cidr": "192.0.2.1"}, {"cidr": "198.51.100.55"}),
        ("ipset", "ipsets", {"entries": [{"cidr": "192.0.2.0/24"}]}, {"entries": [{"cidr": "198.51.100.55"}]}),
        ("security_group", "security_groups", {"rules": [{"enable": 0}]}, {"rules": [{"enable": 1}]}),
    ],
)
def test_actual_cleanup_guards_refuse_failed_reads_foreign_metadata_and_unrecognized_state(tmp_path, kind, key, record, unexpected):
    path = ROOT / "tests/integration/targets" / ("proxmox_firewall_" + kind) / "tasks/cleanup_one.yml"
    tasks = yaml.safe_load(path.read_text())
    expression = tasks[1]["ansible.builtin.set_fact"]["firewall_cleanup_safe"][2:-2].strip()
    owned = dict(record, comment="Owned Ansible firewall fixture")
    cases = [
        ({"failed": False, key: []}, True),
        ({"failed": False, key: [owned]}, True),
        ({"failed": True}, False),
        ({"failed": False, key: [dict(owned, comment="another owner")]}, False),
        ({"failed": False, key: [dict(owned, **unexpected)]}, False),
    ]
    checks = [
        {
            "name": "Verify real cleanup guard case " + str(index),
            "vars": {"firewall_cleanup_read": value},
            "ansible.builtin.assert": {"that": ["(" + expression + ") == " + ("true" if expected else "false")]},
        }
        for index, (value, expected) in enumerate(cases)
    ]
    run_assertions(tmp_path, checks)


@pytest.mark.parametrize("kind", ["alias", "ipset"])
def test_configured_guest_selection_does_not_evaluate_absent_automatic_discovery(tmp_path, kind):
    path = ROOT / "tests/integration/targets" / ("proxmox_firewall_" + kind) / "tasks/main.yml"
    tasks = yaml.safe_load(path.read_text())[0]["block"]
    configured = next(task for task in tasks if task["name"] == "Use explicitly designated guest scopes")
    selection = configured["ansible.builtin.set_fact"]
    guests = [{"scope": "qemu", "node": "pve02", "vmid": 200}, {"scope": "lxc", "node": "pve02", "vmid": 201}]
    run_assertions(
        tmp_path,
        [
            dict(name="Use explicit guests without discovery facts", vars={"proxmox_firewall_test_guests": guests}, **{"ansible.builtin.set_fact": selection}),
            {
                "name": "Verify configured contexts",
                "ansible.builtin.assert": {
                    "that": ["firewall_contexts | length == 3", "firewall_contexts[1].node == 'pve02'", "firewall_contexts[2].vmid == 201"]
                },
            },
        ],
    )
