# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_tfa/library/proxmox_tfa_fixture.py"
SPEC = importlib.util.spec_from_file_location("proxmox_tfa_fixture", FIXTURE_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


@pytest.mark.parametrize("timestamp,value", [
    (59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
    (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")
])
def test_fixture_code_matches_rfc6238_sha1_vectors(timestamp, value):
    assert HELPER.verification_code(b"12345678901234567890", timestamp) == value


def test_fixture_is_local_read_only_and_preserves_literal_user(monkeypatch):
    module = Mock(params={"userid": "owned?%41@pve"})
    monkeypatch.setattr(HELPER, "AnsibleModule", Mock(return_value=module))
    monkeypatch.setattr(HELPER.secrets, "token_bytes", lambda _size: b"12345678901234567890")
    monkeypatch.setattr(HELPER.time, "time", lambda: 59)
    HELPER.main()
    assert HELPER.AnsibleModule.call_args.kwargs["supports_check_mode"] is True
    result = module.exit_json.call_args.kwargs
    assert result["changed"] is False
    assert result["value"] == "287082"
    assert "owned%3F%2541%40pve" in result["uri"]
