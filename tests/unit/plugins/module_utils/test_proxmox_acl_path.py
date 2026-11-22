# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from ansible_collections.ansible.proxmox.plugins.module_utils.proxmox import is_pve_acl_path


@pytest.mark.parametrize("path", [
    '/',
    '/access',
    '/access/groups',
    '/access/groups/ops-team',
    '/access/realm',
    '/access/realm/example.com',
    '/nodes',
    '/nodes/pve01',
    '/nodes/pve01/certificates',
    '/pool',
    '/pool/a',
    '/pool/a/b',
    '/pool/a/b/c',
    '/sdn',
    '/sdn/controllers',
    '/sdn/controllers/controller_1',
    '/sdn/dns',
    '/sdn/dns/dns1',
    '/sdn/fabrics',
    '/sdn/fabrics/fabric1',
    '/sdn/ipams',
    '/sdn/ipams/ipam1',
    '/sdn/prefix-lists',
    '/sdn/prefix-lists/prefix1',
    '/sdn/route-maps',
    '/sdn/route-maps/route1',
    '/sdn/zones',
    '/sdn/zones/zone-1',
    '/sdn/zones/zone-1/vnet_1',
    '/sdn/zones/zone-1/vnet_1/1',
    '/sdn/zones/zone-1/vnet_1/9999',
    '/storage',
    '/storage/local-lvm',
    '/vms',
    '/vms/100',
    '/vms/9999999999',
    '/mapping',
    '/mapping/pci',
    '/mapping/pci/gpu-1',
    'vms/100',
    '/access//groups/',
    '///',
])
def test_supported_pve_acl_namespaces(path):
    assert is_pve_acl_path(path) is True


@pytest.mark.parametrize("path", [
    '/no_path',
    '//no_path//',
    '/no_path/child',
    '/access/users',
    '/access/groups/team/child',
    '/nodes/pve01/unknown',
    '/nodes/pve01/certificates/child',
    '/pool/a/b/c/d',
    '/sdn/unknown',
    '/sdn/controllers/controller.1',
    '/sdn/dns/dns-1',
    '/sdn/fabrics/fabric_1',
    '/sdn/ipams/ipam.1',
    '/sdn/prefix-lists/prefix-1',
    '/sdn/route-maps/route_1',
    '/sdn/zones/z/v/0',
    '/sdn/zones/z/v/10000',
    '/sdn/zones/z/v/1/extra',
    '/storage/local/child',
    '/vms/99',
    '/vms/0100',
    '/vms/abc',
    '/mapping/pci/gpu/extra',
    '/wrong_path',
    None,
    42,
    '',
    '/vms/100\\n',
    '/pool/a b',
    '/vms/100%2F200#?literal',
])
def test_invalid_pve_acl_namespaces(path):
    assert is_pve_acl_path(path) is False
