# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_base import ProxmoxBase


@pytest.mark.parametrize("completed_write", [False, True])
def test_missing_dependency_preserves_completed_mutation(pool_environment, completed_write):
    module, client, _api = pool_environment()
    if completed_write:
        client.mark_changed()
    handler = ProxmoxBase(module, client)
    with pytest.raises(module.Fail) as failure:
        handler.validate_proxmox_resource_exist("user", "owned@pve", lambda: [], resource_key="userid")
    result = failure.value.args[0]
    assert result["msg"] == "User owned@pve does not exist"
    assert result.get("changed", False) is completed_write


def test_optional_missing_dependency_does_not_fail(pool_environment):
    module, client, _api = pool_environment()
    client.mark_changed()
    handler = ProxmoxBase(module, client)
    assert handler.validate_proxmox_resource_exist("user", "owned@pve", lambda: [], resource_key="userid", fail_on_missing=False) is False
    assert client._changed is True
