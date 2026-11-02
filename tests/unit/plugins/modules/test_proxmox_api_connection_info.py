# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import proxmox_argument_spec
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_api_connection_info import (
    EXAMPLES,
    ProxmoxConnectionInfoModule,
)


class ExitJson(Exception):
    def __init__(self, result):
        super().__init__()
        self.result = result


def test_connection_examples_cover_supported_authentication_modes():
    examples = yaml.safe_load(EXAMPLES)
    argument_spec = proxmox_argument_spec()
    params = [example["ansible.proxmox.proxmox_api_connection_info"] for example in examples]

    assert len(params) == 2
    assert all("api_host" in example_params for example_params in params)
    assert all(set(example_params).issubset(argument_spec) for example_params in params)

    password_example, token_example = params
    assert password_example["api_user"]
    assert password_example["api_password"]
    assert password_example["validate_certs"] is True
    assert password_example["ca_path"]

    assert token_example["api_user"]
    assert token_example["api_token_id"]
    assert token_example["api_token_secret"]
    assert token_example["validate_certs"] is True
    assert token_example["ca_path"]


def test_connection_check_returns_api_version_without_changes():
    version = {"version": "9.0", "release": "9.0.1", "repoid": "test"}
    version_get = Mock(return_value=version)
    client = SimpleNamespace(api=SimpleNamespace(version=SimpleNamespace(get=version_get)))

    def exit_json(**result):
        raise ExitJson(result)

    module = SimpleNamespace(params={}, exit_json=exit_json)
    connection_test = ProxmoxConnectionInfoModule(module, client=client)

    with pytest.raises(ExitJson) as result:
        connection_test.execute()

    assert result.value.result == {"changed": False, "version": version}
    version_get.assert_called_once_with()
