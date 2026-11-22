# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "tests/integration/targets/proxmox_firewall_log_info"
RECORD = {"n": 1, "t": "100 6 PVEFW-HOST-IN 01/Nov/2026:12:00:00 +0000 ACCEPT"}
EPOCH = int(datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp())


def run_assertions(tmp_path, filename, variables):
    """Execute the real target assertions with supplied observations, without API calls."""
    tasks = yaml.safe_load((TARGET / "tasks" / filename).read_text())
    tasks = [task for task in tasks if "ansible.proxmox.proxmox_firewall_log_info" not in task
             and task.get("ansible.builtin.include_tasks") != "queries.yml"]
    for task in tasks:
        if task.get("ansible.builtin.include_tasks") == "records.yml":
            task["ansible.builtin.include_tasks"] = str(TARGET / "tasks/records.yml")
    defaults = yaml.safe_load((TARGET / "defaults/main.yml").read_text())
    play = [{"hosts": "localhost", "gather_facts": False, "vars": dict(defaults, **variables), "tasks": tasks}]
    path = tmp_path / "assertions.yml"
    path.write_text(yaml.safe_dump(play))
    environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"), ANSIBLE_NOCOLOR="1")
    return subprocess.run([sys.executable, "-m", "ansible.cli.playbook", "-i", "localhost,", "-c", "local", str(path)],
                          cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("records,require_content,success", [
    ([], True, False), ([], False, True),
    ([{"n": 1, "t": "no content"}], True, False), ([{"n": 1, "t": "no content"}], False, True),
    ([{"n": 1, "t": "unable to open file - No such file or directory"}], True, False),
    ([{"n": 1, "t": "unable to open file - No such file or directory"}], False, True),
    ([RECORD], True, True), ([RECORD], False, True),
    ([{"n": "invalid", "t": "no content"}], False, False),
])
def test_content_opt_in_preserves_shape_checks(tmp_path, records, require_content, success):
    result = run_assertions(tmp_path, "reads.yml", {
        "security_read": {"changed": False, "firewall_log": records},
        "firewall_log_require_content": require_content,
    })
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    if success and not any(record == RECORD for record in records):
        assert "coverage remain unverified" in result.stdout


@pytest.mark.parametrize("records,since,until,vmid,success", [
    ([RECORD], EPOCH, EPOCH, 100, True),
    ([RECORD], EPOCH + 1, 0, 100, False),
    ([RECORD], 0, EPOCH - 1, 100, False),
    ([RECORD], 0, 0, 100, True),
    ([RECORD], 0, 0, 101, False),
    ([{"n": 1, "t": "unable to open file - No such file or directory"}], 1, 0, 100, True),
])
def test_optional_content_still_checks_available_timestamps_and_guest_ownership(tmp_path, records, since, until, vmid, success):
    result = run_assertions(tmp_path, "filters.yml", {
        "firewall_log_filtered": {"changed": False, "firewall_log": records},
        "firewall_log_case": [{"since": since, "until": until}, False],
        "firewall_log_query_vmid": vmid,
        "firewall_log_require_content": False,
    })
    assert (result.returncode == 0) is success, result.stdout + result.stderr
