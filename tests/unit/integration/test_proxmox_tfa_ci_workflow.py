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
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/proxmox_tfa.yml").read_text())
STEPS = WORKFLOW["jobs"]["integration"]["steps"]
TFA_STEP = next(step for step in STEPS if step["name"] == "Run password-authenticated TFA integration tests")
CLEANUP_STEP = next(step for step in STEPS if step["name"] == "Clean integration files")
REVISION_STEP = next(step for step in STEPS if step.get("id") == "revision")


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
import os
import subprocess
import sys

arguments = sys.argv[1:]
if "generate-integration-config" not in arguments:
    raise SystemExit("Only configuration generation may use make")
sys.exit(subprocess.run([os.environ["REAL_MAKE"], *arguments], check=False).returncode)
''')
    recorder.chmod(0o700)
    recorder = executable / "ansible-test"
    recorder.write_text("#!" + sys.executable + '''
import json
import os
from pathlib import Path
import sys
import yaml

arguments = sys.argv[1:]
if arguments[:2] != ["integration", "proxmox_tfa"]:
    raise SystemExit("Unexpected integration target in workflow test")
config = yaml.safe_load(Path("tests/integration/integration_config.yml").read_text())
record = {
    "targets": arguments[1],
    "cli_args": " ".join(arguments[2:]),
    "user": config["proxmox_api_user"],
    "password": bool(config["proxmox_api_password"]),
    "token": bool(config["proxmox_api_token_secret"]),
    "enrollment": config["tfa_test_allow_enrollment"],
}
with Path(os.environ["WORKFLOW_RECORDS"]).open("a") as output:
    output.write(json.dumps(record) + "\\n")
sys.exit(int(os.environ["INTEGRATION_EXIT"]))
''')
    recorder.chmod(0o700)
    records = tmp_path / "records.jsonl"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PROXMOX_")}
    environment.update(PATH=str(executable) + os.pathsep + os.environ["PATH"], INTEGRATION_VENV=str(activation.parent),
                       REAL_MAKE=shutil.which("make"), WORKFLOW_RECORDS=str(records))
    contexts = {
        "secrets": {"PROXMOX_HOST": "pve.example.invalid", "PROXMOX_USER": "ci@pve", "PROXMOX_TOKEN_ID": "automation",
                    "PROXMOX_TOKEN_SECRET": "EXAMPLE-token", "PROXMOX_VALIDATE_CERTS": "true", "PROXMOX_CA_PATH": ""},
        "vars": {}, "github": {"workspace": str(root.parents[2])},
    }

    def run(step, password="EXAMPLE-root-password", opt_in="true", integration_exit=0):
        contexts["secrets"]["PROXMOX_TFA_ROOT_PASSWORD"] = password
        contexts["vars"]["PROXMOX_TFA_ALLOW_ENROLLMENT"] = opt_in
        values = dict(environment)
        values["INTEGRATION_EXIT"] = str(integration_exit)
        for key, value in step.get("env", {}).items():
            reference = re.fullmatch(r"\$\{\{\s*(\w+)\.(\w+)\s*\}\}", str(value))
            values[key] = contexts[reference[1]].get(reference[2], "") if reference else str(value)
        return subprocess.run(["bash", "-c", step["run"]], cwd=root, env=values, capture_output=True, text=True, check=False)

    return root, records, run


@pytest.mark.parametrize("stale_password", [False, True])
def test_tfa_workflow_replaces_cached_configuration_and_runs_only_its_target(workflow_runner, stale_password):
    root, records, run = workflow_runner
    config = root / "tests/integration/integration_config.yml"
    config.write_text(yaml.safe_dump({"proxmox_api_user": "stale", "proxmox_api_password": "EXAMPLE-stale" if stale_password else "",
                                     "proxmox_api_token_secret": "" if stale_password else "EXAMPLE-stale", "tfa_test_allow_enrollment": False}))
    config.chmod(0o644)
    # Make would consider the stale file up to date; it must be recreated privately.
    tfa = run(TFA_STEP)
    assert tfa.returncode == 0, tfa.stderr
    assert json.loads(records.read_text()) == {
        "targets": "proxmox_tfa", "cli_args": "--allow-disabled -v --color", "user": "root@pam",
        "password": True, "token": False, "enrollment": True,
    }
    assert config.stat().st_mode & 0o777 == 0o600
    assert "EXAMPLE-root-password" not in tfa.stdout + tfa.stderr
    assert "EXAMPLE-token" not in tfa.stdout + tfa.stderr


@pytest.mark.parametrize("password,opt_in", [("", "true"), ("EXAMPLE-password", ""), ("EXAMPLE-password", "false"), ("EXAMPLE-password", "TRUE")])
def test_tfa_refuses_missing_credentials_or_authorization_before_running(workflow_runner, password, opt_in):
    root, records, run = workflow_runner
    result = run(TFA_STEP, password, opt_in)
    assert result.returncode != 0
    assert "PROXMOX_TFA_ROOT_PASSWORD" in result.stdout
    assert not records.exists()
    assert not (root / "tests/integration/integration_config.yml").exists()
    assert "EXAMPLE-password" not in result.stdout + result.stderr


@pytest.mark.parametrize("integration_exit", [0, 1])
def test_tfa_cleanup_removes_credentials_and_environment_after_success_or_failure(workflow_runner, integration_exit):
    root, records, run = workflow_runner
    result = run(TFA_STEP, integration_exit=integration_exit)
    assert result.returncode == integration_exit
    assert records.exists()
    assert (root / "tests/integration/integration_config.yml").exists()
    assert (root.parents[2] / "activation").exists()
    assert CLEANUP_STEP["if"] == "always()"
    cleanup = run(CLEANUP_STEP)
    assert cleanup.returncode == 0, cleanup.stderr
    assert not (root / "tests/integration/integration_config.yml").exists()
    assert not (root.parents[2] / "activation").exists()


def test_workflow_preserves_manual_approval_and_valid_bash_steps():
    assert WORKFLOW["jobs"]["integration"]["environment"] == "eco-proxmox-protected"
    assert WORKFLOW["concurrency"]["cancel-in-progress"] is False
    events = WORKFLOW.get("on", WORKFLOW.get(True))
    assert "pull_request_target" not in events
    assert "pull_request" in events
    assert "github.event.pull_request.head.repo.id == github.event.repository.id" in WORKFLOW["jobs"]["integration"]["if"]
    for step in STEPS:
        if "run" in step:
            result = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True, check=False)
            assert result.returncode == 0, step["name"] + ": " + result.stderr


@pytest.mark.parametrize("revision", [
    "a" * 40, "0123456789abcdef" * 2 + "01234567", "main", "refs/pull/43/head", "a" * 39, "a" * 41,
    "$(touch injected)", "a" * 40 + "\nother",
])
def test_reviewed_revision_requires_one_immutable_commit(tmp_path, revision):
    output = tmp_path / "outputs"
    environment = dict(os.environ, CHECKOUT_SHA=revision, GITHUB_OUTPUT=str(output))
    result = subprocess.run(["bash", "-c", REVISION_STEP["run"]], cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)
    if len(revision) == 40 and all(char in "0123456789abcdef" for char in revision):
        assert result.returncode == 0
        assert output.read_text() == "sha=" + revision + "\n"
    else:
        assert result.returncode != 0
        assert not output.exists()
    assert not (tmp_path / "injected").exists()
