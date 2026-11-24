# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

SOURCE = Path(__file__).resolve().parents[2] / "integration"


@pytest.mark.parametrize("opt_in", [None, "false", "true"])
@pytest.mark.parametrize("password_auth", [False, True])
def test_generated_config_preserves_credentials_and_explicit_tfa_opt_in(tmp_path, opt_in, password_auth):
    for name in ("generate_integration_config.sh", "integration_config.yml.tpl"):
        shutil.copyfile(SOURCE / name, tmp_path / name)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PROXMOX_")}
    secret = 'EXAMPLE: "quoted"\nmultiline $value ${PROXMOX_TFA_ALLOW_ENROLLMENT} ${PROXMOX_TOKEN_ID}'
    env.update(PROXMOX_HOST="pve.example.com", PROXMOX_USER="root@pam")
    if password_auth:
        env["PROXMOX_PASSWORD"] = secret
    else:
        env.update(PROXMOX_TOKEN_ID="automation", PROXMOX_TOKEN_SECRET=secret)
    if opt_in is not None:
        env["PROXMOX_TFA_ALLOW_ENROLLMENT"] = opt_in
    process = subprocess.run(["bash", str(tmp_path / "generate_integration_config.sh")], env=env, capture_output=True, text=True, check=False)
    assert process.returncode == 0
    assert process.stdout == ""
    assert secret not in process.stderr
    config = yaml.safe_load((tmp_path / "integration_config.yml").read_text())
    assert config["tfa_test_allow_enrollment"] is (opt_in == "true")
    assert config["proxmox_api_password"] == (secret if password_auth else "")
    assert config["proxmox_api_token_secret"] == ("" if password_auth else secret)


@pytest.mark.parametrize("invalid", ["yes", "TRUE", "true\ninjected: true"])
def test_generator_refuses_invalid_tfa_opt_in_before_writing(tmp_path, invalid):
    shutil.copyfile(SOURCE / "generate_integration_config.sh", tmp_path / "generate_integration_config.sh")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PROXMOX_")}
    env.update(PROXMOX_HOST="pve.example.com", PROXMOX_PASSWORD="EXAMPLE-secret", PROXMOX_TFA_ALLOW_ENROLLMENT=invalid)
    process = subprocess.run(["bash", str(tmp_path / "generate_integration_config.sh")], env=env, capture_output=True, text=True, check=False)
    assert process.returncode == 1
    assert "must be true or false" in process.stderr
    assert "EXAMPLE-secret" not in process.stderr
    assert not (tmp_path / "integration_config.yml").exists()
