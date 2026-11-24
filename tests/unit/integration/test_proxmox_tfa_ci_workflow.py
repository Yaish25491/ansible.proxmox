# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/integration.yml").read_text())
STEPS = WORKFLOW["jobs"]["integration"]["steps"]
TFA_STEP = next(step for step in STEPS if step["name"] == "Run password-authenticated TFA integration tests")
TOKEN_STEP = next(step for step in STEPS if step["name"] == "Run integration tests")


@pytest.fixture
def workflow_runner(tmp_path):
    """Run the real Bash steps and Make config target, stubbing live integration."""
    root = tmp_path / "ansible_collections/ansible/proxmox"
    integration = root / "tests/integration"
    integration.mkdir(parents=True)
    shutil.copyfile(ROOT / "Makefile", root / "Makefile")
    for filename in ("generate_integration_config.sh", "integration_config.yml.tpl"):
        shutil.copyfile(ROOT / "tests/integration" / filename, integration / filename)
    activation = tmp_path / "activation/bin"
    activation.mkdir(parents=True)
    (activation / "activate").write_text("# Inert fixture for the existing CI activation step.\n")
    executable = tmp_path / "bin"
    executable.mkdir()
    recorder = executable / "make"
    recorder.write_text("#!" + sys.executable + '''
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml

arguments = sys.argv[1:]
if "generate-integration-config" in arguments:
    sys.exit(subprocess.run([os.environ["REAL_MAKE"], *arguments], check=False).returncode)
if not arguments or arguments[0] != "integration":
    raise SystemExit("Unexpected make operation in workflow test")
config = yaml.safe_load(Path("tests/integration/integration_config.yml").read_text())
record = {
    "targets": next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("INTEGRATION_TARGETS=")),
    "user": config["proxmox_api_user"],
    "password": bool(config["proxmox_api_password"]),
    "token": bool(config["proxmox_api_token_secret"]),
    "enrollment": config["tfa_test_allow_enrollment"],
}
with Path(os.environ["WORKFLOW_RECORDS"]).open("a") as output:
    output.write(json.dumps(record) + "\\n")
''')
    recorder.chmod(0o700)
    records = tmp_path / "records.jsonl"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PROXMOX_")}
    environment.update(PATH=str(executable) + os.pathsep + os.environ["PATH"], INTEGRATION_VENV=str(activation.parent),
                       REAL_MAKE=shutil.which("make"), WORKFLOW_RECORDS=str(records))
    contexts = {
        "secrets": {"PROXMOX_HOST": "pve.example.invalid", "PROXMOX_USER": "ci@pve", "PROXMOX_TOKEN_ID": "automation",
                    "PROXMOX_TOKEN_SECRET": "EXAMPLE-token", "PROXMOX_VALIDATE_CERTS": "true", "PROXMOX_CA_PATH": ""},
        "vars": {}, "github": {"workspace": str(root.parents[2])}, "matrix": {"ansible_version": "stable-2.21"},
    }

    def run(step, password="EXAMPLE-root-password", opt_in="true"):
        contexts["secrets"]["PROXMOX_TFA_ROOT_PASSWORD"] = password
        contexts["vars"]["PROXMOX_TFA_ALLOW_ENROLLMENT"] = opt_in
        values = dict(environment)
        for key, value in step.get("env", {}).items():
            reference = re.fullmatch(r"\$\{\{\s*(\w+)\.(\w+)\s*\}\}", str(value))
            values[key] = contexts[reference[1]].get(reference[2], "") if reference else str(value)
        return subprocess.run(["bash", "-c", step["run"]], cwd=root, env=values, capture_output=True, text=True, check=False)

    return root, records, run


@pytest.mark.parametrize("targets", [[], ["proxmox_user"], ["proxmox_tfa"], ["proxmox_tfa", "proxmox_user"]])
@pytest.mark.parametrize("stale_password", [False, True])
def test_workflow_selects_auth_per_target_and_replaces_cached_configuration(workflow_runner, targets, stale_password):
    root, records, run = workflow_runner
    config = root / "tests/integration/integration_config.yml"
    config.write_text(yaml.safe_dump({"proxmox_api_user": "stale", "proxmox_api_password": "EXAMPLE-stale" if stale_password else "",
                                     "proxmox_api_token_secret": "" if stale_password else "EXAMPLE-stale", "tfa_test_allow_enrollment": False}))
    # Make normally considers this newer file up to date; each auth transition must regenerate it.
    (root / "targets.txt").write_text("".join(target + "\n" for target in targets))
    tfa = run(TFA_STEP)
    assert tfa.returncode == 0, tfa.stderr
    token = run(TOKEN_STEP)
    assert token.returncode == 0, token.stderr
    actual = [json.loads(line) for line in records.read_text().splitlines()] if records.exists() else []
    expected = []
    if "proxmox_tfa" in targets:
        expected.append({"targets": "proxmox_tfa", "user": "root@pam", "password": True, "token": False, "enrollment": True})
    if "proxmox_user" in targets:
        expected.append({"targets": "proxmox_user", "user": "ci@pve", "password": False, "token": True, "enrollment": False})
    assert actual == expected
    assert "EXAMPLE-root-password" not in tfa.stdout + tfa.stderr + token.stdout + token.stderr
    assert "EXAMPLE-token" not in tfa.stdout + tfa.stderr + token.stdout + token.stderr


@pytest.mark.parametrize("password,opt_in", [("", "true"), ("EXAMPLE-password", ""), ("EXAMPLE-password", "false"), ("EXAMPLE-password", "TRUE")])
def test_selected_tfa_refuses_missing_credentials_or_authorization_before_running(workflow_runner, password, opt_in):
    root, records, run = workflow_runner
    (root / "targets.txt").write_text("proxmox_tfa\n")
    result = run(TFA_STEP, password, opt_in)
    assert result.returncode != 0
    assert "PROXMOX_TFA_ROOT_PASSWORD" in result.stdout
    assert not records.exists()
    assert not (root / "tests/integration/integration_config.yml").exists()
    assert "EXAMPLE-password" not in result.stdout + result.stderr


def test_unselected_tfa_does_not_require_password_or_enrollment_opt_in(workflow_runner):
    root, records, run = workflow_runner
    (root / "targets.txt").write_text("proxmox_user\n")
    assert run(TFA_STEP, "", "").returncode == 0
    assert run(TOKEN_STEP, "", "").returncode == 0
    assert json.loads(records.read_text())["targets"] == "proxmox_user"


def test_workflow_preserves_manual_approval_and_valid_bash_steps():
    assert WORKFLOW["jobs"]["approve"]["environment"] == "eco-proxmox-protected"
    assert "approve" in WORKFLOW["jobs"]["integration"]["needs"]
    for step in STEPS:
        if "run" in step:
            result = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True, check=False)
            assert result.returncode == 0, step["name"] + ": " + result.stderr
