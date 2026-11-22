# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from proxmoxer import ProxmoxAPI
from requests import Response

from ansible.module_utils import basic
from ansible_collections.ansible.proxmox.plugins.modules import proxmox_acl, proxmox_acl_info
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_acl import ProxmoxAclModule
from ansible_collections.ansible.proxmox.plugins.modules.proxmox_acl_info import ProxmoxAclInfoModule


class AclApi:
    """Represent independent configuration reads and bounded ACL updates."""

    def __init__(self, identity):
        self.identity = identity
        self.entries = []
        self.get = Mock(side_effect=self._get)
        self.put = Mock(side_effect=self._put)

    def _get(self, path, **parameters):
        if path == "access/acl":
            return copy.deepcopy(self.entries)
        return self.identity.get(path, **parameters)

    def _put(self, endpoint, **parameters):
        assert endpoint == "access/acl"
        subject_type = next(name for name in ("user", "group", "token") if name + "s" in parameters)
        target = dict(path=parameters["path"], type=subject_type, ugid=parameters[subject_type + "s"], roleid=parameters["roles"])
        self.entries = [entry for entry in self.entries if any(entry[name] != value for name, value in target.items())]
        if not parameters.get("delete"):
            self.entries.append(dict(target, propagate=parameters.get("propagate", 1)))


@pytest.fixture
def acl_environment(identity_environment):
    def create(check_mode=False, existing=False, **parameters):
        params = dict(path="/vms", subject_type="user", subject="application@pve", role="PVEAuditor", propagate=None)
        params.update(parameters)
        module, client, identity = identity_environment(check_mode=check_mode, **params)
        identity.users["application@pve"] = {}
        identity.users["ops%2F#?@pve"] = {}
        identity.users["ops!team@pve"] = {}
        identity.tokens["ops!team@pve", "automation"] = {}
        identity.groups["application"] = {}
        identity.roles["PVEAuditor"] = {"VM.Audit"}
        identity.roles["NoAccess"] = set()
        identity.tokens["application@pve", "automation"] = {}
        api = AclApi(identity)
        client._api = api
        if existing:
            api.entries.append(dict(path="/vms", type=params["subject_type"], ugid=params["subject"], roleid=params["role"], propagate=1))
        api.entries.append(dict(path="/storage", type="user", ugid="unrelated@pve", roleid="PVEAuditor", propagate=0))
        return module, client, api
    return create


def run(module, handler):
    with pytest.raises(module.Exit) as result:
        handler.execute()
    return result.value.args[0]


@pytest.mark.parametrize("subject_type,subject", [("user", "application@pve"), ("group", "application"),
                                                  ("token", "application@pve!automation"), ("user", "ops%2F#?@pve"),
                                                  ("user", "ops!team@pve"), ("token", "ops!team@pve!automation")])
@pytest.mark.parametrize("propagate", [None, False, True])
def test_lifecycle_independent_readback_and_neighbor_preservation(acl_environment, subject_type, subject, propagate):
    module, client, api = acl_environment(subject_type=subject_type, subject=subject, propagate=propagate)
    neighbor = copy.deepcopy(api.entries[0])
    created = run(module, ProxmoxAclModule(module, client))
    assert created["changed"] is True
    assert created["acl"]["ugid"] == subject
    assert created["acl"]["propagate"] is (True if propagate is None else propagate)
    assert run(module, ProxmoxAclInfoModule(module, client)) == {"changed": False, "acls": [created["acl"]]}
    assert run(module, ProxmoxAclModule(module, client))["changed"] is False
    module.params["propagate"] = not created["acl"]["propagate"]
    updated = run(module, ProxmoxAclModule(module, client))
    assert updated["changed"] is True
    assert updated["acl"]["propagate"] is module.params["propagate"]
    assert run(module, ProxmoxAclModule(module, client))["changed"] is False
    module.params["propagate"] = None
    assert run(module, ProxmoxAclModule(module, client))["changed"] is False
    module.params["state"] = "absent"
    assert run(module, ProxmoxAclModule(module, client)) == {"changed": True, "acl": None}
    assert run(module, ProxmoxAclModule(module, client)) == {"changed": False, "acl": None}
    assert api.entries == [neighbor]
    assert api.put.call_args.kwargs["delete"] == 1
    assert all(call.args == ("access/acl",) for call in api.put.call_args_list)


@pytest.mark.parametrize("operation", ["create", "update", "delete", "noop"])
def test_check_mode_never_mutates(acl_environment, operation):
    module, client, api = acl_environment(check_mode=True, existing=operation != "create", propagate=False if operation == "update" else None)
    if operation == "delete":
        module.params["state"] = "absent"
    before = copy.deepcopy(api.entries)
    result = run(module, ProxmoxAclModule(module, client))
    assert result["changed"] is (operation != "noop")
    assert api.entries == before
    api.put.assert_not_called()
    if operation == "delete":
        assert result["acl"] is None
    elif operation == "update":
        assert result["acl"]["propagate"] is False


@pytest.mark.parametrize("path", ["vms", "//vms//", "/vms/"])
def test_path_is_normalized_for_identity_and_writes(acl_environment, path):
    module, client, api = acl_environment(path=path)
    assert run(module, ProxmoxAclModule(module, client))["acl"]["path"] == "/vms"
    assert api.put.call_args.kwargs["path"] == "/vms"
    assert run(module, ProxmoxAclModule(module, client))["changed"] is False


@pytest.mark.parametrize("options", [{"path": ""}, {"path": "/vms?other=1"}, {"subject": "a@pve,b@pve"},
                                     {"subject": "a@pve;b@pve"}, {"subject": "a b@pve"}, {"role": "A,B"},
                                     {"role": ""}, {"subject_type": "group", "subject": "a/b"},
                                     {"subject_type": "token", "subject": "application@pve"}])
@pytest.mark.parametrize("check_mode", [False, True])
def test_reject_invalid_scalar_inputs_before_api(acl_environment, options, check_mode):
    module, client, api = acl_environment(check_mode=check_mode, **options)
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()
    api.put.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("options,missing", [({"subject": "missing@pve"}, "User"), ({"subject_type": "group", "subject": "missing"}, "Group"),
                                             ({"subject_type": "token", "subject": "application@pve!missing"}, "Token"),
                                             ({"role": "missing"}, "Role")])
def test_missing_dependency_rejected_and_absent_missing_is_noop(acl_environment, options, missing):
    module, client, api = acl_environment(**options)
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail, match=missing):
        handler.execute()
    api.put.assert_not_called()
    module.params["state"] = "absent"
    assert run(module, handler) == {"changed": False, "acl": None}


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("subject_type,subject,missing", [
    ("user", "application@pve", "User"),
    ("group", "application", "Group"),
    ("token", "application@pve!automation", "Token"),
    ("user", "application@pve", "Role"),
])
def test_missing_dependencies_block_stale_deletion_but_absent_bindings_are_noops(
        acl_environment, check_mode, existing, subject_type, subject, missing):
    module, client, api = acl_environment(existing=existing, state="absent", check_mode=check_mode,
                                          subject_type=subject_type, subject=subject)
    if missing == "User":
        del api.identity.users[subject]
    elif missing == "Group":
        del api.identity.groups[subject]
    elif missing == "Token":
        del api.identity.tokens["application@pve", "automation"]
    else:
        del api.identity.roles["PVEAuditor"]
    before = copy.deepcopy(api.entries)
    handler = ProxmoxAclModule(module, client)
    if existing:
        with pytest.raises(module.Fail, match=missing) as failure:
            handler.execute()
        assert failure.value.args[0].get("changed", False) is False
    else:
        assert run(module, handler) == {"changed": False, "acl": None}
    assert api.entries == before
    api.put.assert_not_called()


def test_noaccess_is_a_real_binding_not_an_empty_role_set(acl_environment):
    module, client, api = acl_environment(role="NoAccess", propagate=False)
    result = run(module, ProxmoxAclModule(module, client))
    assert result["acl"]["roleid"] == "NoAccess"
    assert api.put.call_args.kwargs["roles"] == "NoAccess"
    assert api.put.call_args.kwargs["propagate"] == 0


@pytest.mark.parametrize("check_mode", [False, True])
def test_info_list_filter_missing_and_immutability(acl_environment, check_mode):
    module, client, api = acl_environment(existing=True, check_mode=check_mode)
    before = copy.deepcopy(api.entries)
    assert len(run(module, ProxmoxAclInfoModule(module, client))["acls"]) == 1
    module.params.update(path=None, subject_type=None, subject=None, role=None)
    listed = run(module, ProxmoxAclInfoModule(module, client))
    assert listed["changed"] is False
    assert len(listed["acls"]) == 2
    assert listed["acls"][0]["path"] == "/storage"
    module.params.update(path="vms//", subject="application@pve", role="PVEAuditor")
    assert len(run(module, ProxmoxAclInfoModule(module, client))["acls"]) == 1
    module.params["role"] = "missing"
    assert run(module, ProxmoxAclInfoModule(module, client))["acls"] == []
    assert api.entries == before
    api.put.assert_not_called()


@pytest.mark.parametrize("options", [{"path": ""}, {"path": "/bad?"}, {"subject": ""}, {"role": "a,b"}])
def test_info_rejects_invalid_filters(acl_environment, options):
    module, client, api = acl_environment(**options)
    handler = ProxmoxAclInfoModule(module, client)
    with pytest.raises(module.Fail):
        handler.execute()
    api.get.assert_not_called()


@pytest.mark.parametrize("failure", ["readback", "mismatch"])
def test_successful_write_remains_changed_on_verification_failure(acl_environment, failure):
    module, client, api = acl_environment(propagate=False)
    original_get = api._get

    def read(path, **parameters):
        if path == "access/acl" and api.put.called:
            if failure == "readback":
                raise RuntimeError("verification unavailable")
            return []
        return original_get(path, **parameters)

    api.get.side_effect = read
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True


def test_rejected_first_write_reports_unchanged(acl_environment):
    module, client, api = acl_environment()
    api.put.side_effect = RuntimeError("server rejected request")
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0].get("changed", False) is False


@pytest.mark.parametrize("target,options", [
    (proxmox_acl, {"path": "/vms", "subject_type": "invalid", "subject": "a@pve", "role": "PVEAuditor"}),
    (proxmox_acl, {"path": "/vms", "subject_type": "user", "subject": "a@pve"}),
    (proxmox_acl_info, {"subject_type": "invalid"}),
])
def test_real_ansible_argument_validation(monkeypatch, target, options):
    arguments = dict(api_host="pve.example.com", api_token_id="controller@pve!automation", api_token_secret="EXAMPLE-only", **options)
    monkeypatch.setattr(basic, "_load_params", lambda: arguments)
    failure = Mock(side_effect=RuntimeError("invalid arguments"))
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", failure)
    with pytest.raises(RuntimeError, match="invalid arguments"):
        target.main()
    assert failure.call_args.kwargs["msg"]


@pytest.mark.parametrize("subject_type,subject", [("user", "ops%2F#?@pve"), ("group", "application"),
                                                  ("token", "ops%2F#?@pve!automation")])
def test_real_https_transport_keeps_subjects_in_form_and_encodes_token_owner_once(acl_environment, subject_type, subject):
    module, client, api = acl_environment(subject_type=subject_type, subject=subject)
    api.identity.tokens["ops%2F#?@pve", "automation"] = {}
    https_api = ProxmoxAPI("pve.example.com", user="controller@pve", token_name="automation", token_value="EXAMPLE-only")

    def respond(request, **_kwargs):
        url = urlsplit(request.url)
        endpoint = url.path.removeprefix("/api2/json/")
        parameters = {key: values[0] for key, values in parse_qs(request.body or "", keep_blank_values=True).items()}
        data = api._get(endpoint, **parameters) if request.method == "GET" else api._put(endpoint, **parameters)
        response = Response()
        response.status_code = 200
        response._content = json.dumps({"data": data}).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    session = https_api._store["session"]
    session.trust_env = False
    session.send = Mock(side_effect=respond)
    client._api = https_api
    assert run(module, ProxmoxAclModule(module, client))["changed"] is True
    module.params["state"] = "absent"
    assert run(module, ProxmoxAclModule(module, client))["acl"] is None
    requests = [call.args[0] for call in session.send.call_args_list]
    writes = [request for request in requests if request.method == "PUT"]
    assert len(writes) == 2
    for request in writes:
        assert urlsplit(request.url).path == "/api2/json/access/acl"
        assert parse_qs(request.body)[subject_type + "s"] == [subject]
        assert parse_qs(request.body)["path"] == ["/vms"]
    if subject_type == "token":
        assert any(urlsplit(request.url).path == "/api2/json/access/users/ops%252F%23%3F%40pve/token" for request in requests)


def test_create_omitted_propagation_verifies_server_default(acl_environment):
    module, client, api = acl_environment()
    original_write = api._put

    def inconsistent_write(endpoint, **parameters):
        original_write(endpoint, **parameters)
        api.entries[-1]["propagate"] = 0

    api.put.side_effect = inconsistent_write
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail) as result:
        handler.execute()
    assert result.value.args[0]["changed"] is True
    assert "readback" in result.value.args[0]["msg"]


def test_one_discovery_per_preflight_and_permission_scopes(acl_environment):
    module, client, api = acl_environment(check_mode=True)
    assert run(module, ProxmoxAclModule(module, client))["changed"] is True
    assert [call.args[0] for call in api.get.call_args_list] == ["access/acl", "access/roles", "access/users"]
    assert [call.args[1] for call in client.validate_pve_permissions.call_args_list] == ["/access", "/vms"]


def test_subject_disappearing_after_write_preserves_changed(acl_environment):
    module, client, api = acl_environment()
    original_put = api._put

    def put(endpoint, **parameters):
        original_put(endpoint, **parameters)
        del api.identity.users["application@pve"]
        api.entries = [entry for entry in api.entries if entry["ugid"] != "application@pve"]

    api.put.side_effect = put
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail, match="does not exist") as failure:
        handler.execute()
    assert failure.value.args[0]["changed"] is True


VALID_ACL_PATHS = [
    "/", "/access", "/access/groups", "/access/groups/ops-team", "/access/realm", "/access/realm/example.com",
    "/nodes", "/nodes/pve01", "/nodes/pve01/certificates", "/pool", "/pool/a", "/pool/a/b", "/pool/a/b/c",
    "/sdn", "/sdn/controllers", "/sdn/controllers/controller_1", "/sdn/dns", "/sdn/dns/dns1",
    "/sdn/fabrics", "/sdn/fabrics/fabric1", "/sdn/ipams", "/sdn/ipams/ipam1",
    "/sdn/prefix-lists", "/sdn/prefix-lists/prefix1", "/sdn/route-maps", "/sdn/route-maps/route1",
    "/sdn/zones", "/sdn/zones/zone-1", "/sdn/zones/zone-1/vnet_1", "/sdn/zones/zone-1/vnet_1/1",
    "/sdn/zones/zone-1/vnet_1/9999", "/storage", "/storage/local-lvm", "/vms", "/vms/100",
    "/vms/9999999999", "/mapping", "/mapping/pci", "/mapping/pci/gpu-1",
]
INVALID_ACL_PATHS = [
    "/no_path", "//no_path//", "/no_path/child", "/access/users", "/access/groups/team/child",
    "/nodes/pve01/unknown", "/nodes/pve01/certificates/child", "/pool/a/b/c/d",
    "/sdn/unknown", "/sdn/controllers/controller.1", "/sdn/dns/dns-1", "/sdn/fabrics/fabric_1",
    "/sdn/ipams/ipam.1", "/sdn/prefix-lists/prefix-1", "/sdn/route-maps/route_1",
    "/sdn/zones/z/v/0", "/sdn/zones/z/v/10000", "/sdn/zones/z/v/1/extra",
    "/storage/local/child", "/vms/99", "/vms/0100", "/vms/abc", "/mapping/pci/gpu/extra",
]


@pytest.mark.parametrize("path", INVALID_ACL_PATHS)
@pytest.mark.parametrize("check_mode", [False, True])
def test_unsupported_acl_paths_fail_before_discovery_and_writes(acl_environment, path, check_mode):
    module, client, api = acl_environment(path=path, check_mode=check_mode)
    before = copy.deepcopy(api.entries)
    handler = ProxmoxAclModule(module, client)
    with pytest.raises(module.Fail, match="supported Proxmox VE ACL path") as failure:
        handler.execute()
    assert failure.value.args[0].get("changed", False) is False
    assert api.entries == before
    api.get.assert_not_called()
    api.put.assert_not_called()
    client.validate_pve_permissions.assert_not_called()


@pytest.mark.parametrize("path", VALID_ACL_PATHS)
@pytest.mark.parametrize("check_mode", [False, True])
def test_native_acl_path_families_do_not_require_objects_to_exist(acl_environment, path, check_mode):
    module, client, api = acl_environment(path=path, check_mode=check_mode)
    result = run(module, ProxmoxAclModule(module, client))
    assert result["changed"] is True
    assert result["acl"]["path"] == path
    if check_mode:
        api.put.assert_not_called()
    else:
        assert api.put.call_args.kwargs["path"] == path
        assert run(module, ProxmoxAclInfoModule(module, client))["acls"] == [result["acl"]]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_obsolete_path_bindings_can_be_read_and_removed(acl_environment, check_mode, existing):
    module, client, api = acl_environment(path="/obsolete-family/target", state="absent", check_mode=check_mode)
    binding = dict(path=module.params["path"], type="user", ugid="application@pve", roleid="PVEAuditor", propagate=1)
    if existing:
        api.entries.append(binding)
    assert len(run(module, ProxmoxAclInfoModule(module, client))["acls"]) == int(existing)
    result = run(module, ProxmoxAclModule(module, client))
    assert result == {"changed": existing, "acl": None}
    if check_mode or not existing:
        api.put.assert_not_called()
    else:
        assert api.put.call_args.kwargs["delete"] == 1
        assert api.put.call_args.kwargs["path"] == binding["path"]
        assert run(module, ProxmoxAclInfoModule(module, client))["acls"] == []
