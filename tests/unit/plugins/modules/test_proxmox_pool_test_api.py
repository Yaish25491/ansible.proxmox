# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


FIXTURE_PATH = Path(__file__).resolve().parents[3] / "integration/targets/proxmox_pool/library/proxmox_pool_test_api.py"
SPEC = importlib.util.spec_from_file_location("proxmox_pool_test_api", FIXTURE_PATH)
fixture_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_api)


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_fixture_transport_reuses_common_calls_and_returns_data(pool_environment, method):
    module, client, api = pool_environment(method=method, path="cluster/resources", data={"type": "vm"}, task_timeout=300)
    response = [{"vmid": 100}] if method == "get" else None
    endpoint = Mock(return_value=response)
    setattr(api, method, endpoint)
    handler = fixture_api.PoolTestApi(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": method != "get", "data": response, "task": {}}
    endpoint.assert_called_once_with("cluster/resources", type="vm")


def test_fixture_waits_for_async_task_using_the_same_client(monkeypatch, pool_environment):
    module, client, api = pool_environment(method="post", path="nodes/pve01/lxc", data={}, task_timeout=123)
    upid = "UPID:pve01:00000001:00000002:create"
    api.post = Mock(return_value=upid)
    waiter = Mock()
    waiter.wait_for_task.return_value = {"status": "stopped", "exitstatus": "OK"}
    factory = Mock(return_value=waiter)
    monkeypatch.setattr(fixture_api, "ProxmoxTask", factory)
    handler = fixture_api.PoolTestApi(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    factory.assert_called_once_with(module, client=client)
    waiter.wait_for_task.assert_called_once_with(upid, timeout=123)
    assert result.value.args[0]["task"]["exitstatus"] == "OK"


@pytest.mark.parametrize("method", ["post", "delete"])
def test_fixture_refuses_check_mode_mutations_before_api_access(pool_environment, method):
    module, client, api = pool_environment(check_mode=True, method=method)
    client._api = None
    handler = fixture_api.PoolTestApi(module, client)
    with pytest.raises(module.Fail, match="must not run in check mode"):
        handler.execute()
    assert api.writes == []


def test_fixture_reads_in_check_mode(pool_environment):
    module, client, api = pool_environment(check_mode=True, method="get", path="cluster/resources", data={}, task_timeout=300)
    api.get = Mock(return_value=[])
    handler = fixture_api.PoolTestApi(module, client)
    with pytest.raises(module.Exit) as result:
        handler.execute()
    assert result.value.args[0] == {"changed": False, "data": [], "task": {}}
