# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Base lifecycle helpers shared by Proxmox modules."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from abc import ABC

from .proxmox import ProxmoxClient


class ProxmoxBase(ABC):
    """Common client, API error handling, and result shaping."""

    result_key = "result"
    service = None

    def __init__(self, module, client=None):
        """Attach the Ansible module and a shared, lazy API client.

        Args:
            module: Ansible module providing parameters and result handling.
            client: Optional existing ProxmoxClient; otherwise one is created.
        """
        self.module = module
        self.client = client or ProxmoxClient(module, service=self.service)

    @property
    def api(self):
        """Return the underlying proxmoxer client through the shared client helper."""
        return self.client.api

    def validate_proxmox_resource_exist(self, resource_type, resource_name, getter, resource_key=None, fail_on_missing=True):
        """Check resource existence, optionally failing when it is missing.

        Args:
            resource_type: Resource label used in operation and failure messages.
            resource_name: Resource name or identifier to locate.
            getter: No-argument API callable returning a resource or entry list.
            resource_key: Optional entry field to match against resource_name.
            fail_on_missing: Whether a missing resource should fail the module.

        Returns:
            Whether a matching resource exists. Without resource_key, any non-None
            response counts as existing; HTTP 404 is treated as missing.
        """
        resource = self.call_api(
            "Retrieving {0} {1}".format(resource_type, resource_name),
            getter,
            return_none_on_404=True,
        )
        if resource_key:
            exists = any(
                str(item.get(resource_key)) == str(resource_name)
                for item in resource or []
                if isinstance(item, dict)
            )
        else:
            exists = resource is not None

        if not exists and fail_on_missing:
            self.module.fail_json(msg="{0} {1} does not exist".format(resource_type.capitalize(), resource_name))
        return exists

    def call_api(self, operation, function, *args, **kwargs):
        """Call an API endpoint and report failures through the shared client.

        Args:
            operation: Human-readable description used in failure messages.
            function: API callable to invoke.
            *args: Positional arguments forwarded to the callable.
            **kwargs: API keywords. The helper consumes return_none_on_404 to
                optionally treat HTTP 404 as an absent resource.

        Returns:
            The API response, or None for an explicitly tolerated HTTP 404.
        """
        return_none_on_404 = kwargs.pop("return_none_on_404", False)
        try:
            return function(*args, **kwargs)
        except Exception as exception:
            if return_none_on_404 and self.client.exception_status(exception) == 404:
                return None
            self.client.fail_request(operation, exception, parameters=kwargs)

    def prepare_result(self, changed, resource=None, **values):
        """Assemble result data using this handler's resource key.

        Args:
            changed: Whether the operation changed or would change the resource.
            resource: Data stored under result_key.
            **values: Additional result fields, overriding existing keys.

        Returns:
            A new mapping suitable for Ansible's exit_json.
        """
        result = {"changed": changed, self.result_key: resource}
        result.update(values)
        return result

    def exit(self, changed, resource=None, **values):
        """Finish the module with a prepared resource result.

        Args:
            changed: Whether the operation changed or would change the resource.
            resource: Data stored under result_key.
            **values: Additional result fields passed to prepare_result.
        """
        self.module.exit_json(**self.prepare_result(changed, resource, **values))
