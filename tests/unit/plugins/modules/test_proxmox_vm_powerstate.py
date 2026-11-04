from __future__ import absolute_import, division, print_function

__metaclass__ = type

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils import proxmox_base
from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox_task import ProxmoxTask
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_vm_powerstate, proxmox_vm_powerstate_info


@pytest.mark.parametrize("entrypoint", [proxmox_vm_powerstate, proxmox_vm_powerstate_info])
@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("status,expected", [
    ({"status": "running", "qmpstatus": "running"}, "running"),
    ({"status": "running", "qmpstatus": "paused"}, "paused"),
    ({"status": "stopped", "qmpstatus": "stopped"}, "stopped"),
    ({"status": "stopped", "qmpstatus": "stopped", "lock": "suspended"}, "suspended"),
])
def test_module_entrypoints(entrypoint, check_mode, status, expected, monkeypatch):
    """Verify both module entry points use shared power-state behavior.

    Args:
        entrypoint: Power action or information module under test.
        check_mode: Whether the module should predict changes without writes.
        status: Initial VM status returned by the API.
        expected: Normalized initial power state.
        monkeypatch: Pytest fixture for replacing module dependencies.
    """
    module = SimpleNamespace(
        params={"vmid": 100, "powerstate": "start"}, check_mode=check_mode,
        exit_json=Mock(side_effect=SystemExit), fail_json=Mock(side_effect=RuntimeError("module failed")),
    )
    constructor = Mock(return_value=module)
    monkeypatch.setattr(entrypoint, "AnsibleModule", constructor)
    api = Mock()
    api.cluster.resources.get.return_value = [{"type": "qemu", "vmid": 100, "node": "pve01"}]
    qemu = api.nodes.return_value.qemu.return_value
    qemu.status.current.get.side_effect = [status, {"status": "running", "qmpstatus": "running"}]
    qemu.status.start.post.return_value = "UPID:pve01:start"
    qemu.status.resume.post.return_value = "UPID:pve01:resume"
    client = SimpleNamespace(api=api)
    monkeypatch.setattr(proxmox_base, "ProxmoxClient", Mock(return_value=client))
    wait = Mock()
    monkeypatch.setattr(ProxmoxTask, "wait_for_task", wait)

    with pytest.raises(SystemExit):
        entrypoint.main()

    module.exit_json.assert_called_once()

    arguments = constructor.call_args.kwargs
    assert arguments["supports_check_mode"] is True
    assert arguments["argument_spec"]["vmid"] == {"type": "int", "required": True}
    if entrypoint is proxmox_vm_powerstate:
        assert arguments["argument_spec"]["task_timeout"] == {"type": "int"}
    else:
        assert "task_timeout" not in arguments["argument_spec"]
    assert "node" not in arguments["argument_spec"]
    assert arguments["required_together"] == [["api_token_id", "api_token_secret"]]
    assert arguments["mutually_exclusive"] == [["api_password", "api_token_id"]]
    api.cluster.resources.get.assert_called_once_with(type="vm")
    api.nodes.assert_called_with("pve01")
    changing = entrypoint is proxmox_vm_powerstate and expected != "running"
    result = module.exit_json.call_args.kwargs
    assert result["changed"] is changing
    if changing and not check_mode:
        assert result["powerstate"] == "running"
        assert result["task_upid"].startswith("UPID:")
        wait.assert_called_once()
    else:
        assert result["powerstate"] == expected
        assert "task_upid" not in result
        wait.assert_not_called()
        qemu.status.start.post.assert_not_called()
        qemu.status.resume.post.assert_not_called()
