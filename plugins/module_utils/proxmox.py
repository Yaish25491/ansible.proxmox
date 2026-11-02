# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Proxmox connection options and lazy PVE/PBS API client construction."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import traceback

from ansible.module_utils.basic import env_fallback, missing_required_lib

PROXMOXER_IMPORT_ERROR = None
try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    ProxmoxAPI = None
    PROXMOXER_IMPORT_ERROR = traceback.format_exc()

REQUESTS_IMPORT_ERROR = None
try:
    __import__("requests")
except ImportError:
    REQUESTS_IMPORT_ERROR = traceback.format_exc()


def proxmox_argument_spec(argument_spec=None):
    """Build the shared HTTPS connection argument specification.

    Args:
        argument_spec: Optional module arguments, overriding shared definitions.

    Returns:
        A new mapping of shared and module-specific argument definitions.
    """
    spec = {
        "api_host": {
            "type": "str",
            "required": True,
            "fallback": (env_fallback, ["PROXMOX_HOST"]),
        },
        "api_user": {
            "type": "str",
            "fallback": (env_fallback, ["PROXMOX_USER"]),
        },
        "api_password": {
            "type": "str",
            "no_log": True,
            "fallback": (env_fallback, ["PROXMOX_PASSWORD"]),
        },
        "api_token_id": {
            "type": "str",
            "fallback": (env_fallback, ["PROXMOX_TOKEN_ID"]),
        },
        "api_token_secret": {
            "type": "str",
            "no_log": True,
            "fallback": (env_fallback, ["PROXMOX_TOKEN_SECRET"]),
        },
        "api_port": {
            "type": "int",
            "fallback": (env_fallback, ["PROXMOX_PORT"]),
        },
        "validate_certs": {
            "type": "bool",
            "default": True,
            "fallback": (env_fallback, ["PROXMOX_VALIDATE_CERTS"]),
        },
        "ca_path": {
            "type": "path",
            "fallback": (env_fallback, ["PROXMOX_CA_PATH"]),
        },
        "api_timeout": {
            "type": "int",
            "default": 5,
            "fallback": (env_fallback, ["PROXMOX_API_TIMEOUT"]),
        },
        "api_otp": {
            "type": "str",
            "no_log": True,
            "fallback": (env_fallback, ["PROXMOX_OTP"]),
        },
    }
    spec.update(argument_spec or {})
    return spec


def proxmox_resource_argument_spec(argument_spec=None):
    """Build connection arguments with a standard present/absent state option.

    Args:
        argument_spec: Optional module arguments, overriding shared definitions.

    Returns:
        A new argument specification with state defaulting to 'present'.
    """
    resource_spec = {
        "state": {
            "type": "str",
            "choices": ["present", "absent"],
            "default": "present",
        },
    }
    resource_spec.update(argument_spec or {})
    return proxmox_argument_spec(resource_spec)


def proxmox_required_together(required_together=None):
    """Combine token-pair requirements with module-specific parameter groups.

    Args:
        required_together: Optional groups whose members must be supplied together.

    Returns:
        Independent group lists containing shared and additional requirements.
    """
    return [["api_token_id", "api_token_secret"]] + [list(group) for group in required_together or []]


def proxmox_mutually_exclusive(mutually_exclusive=None):
    """Combine authentication exclusions with module-specific parameter groups.

    Args:
        mutually_exclusive: Optional groups whose members cannot be used together.

    Returns:
        Independent group lists containing shared and additional exclusions.
    """
    return [["api_password", "api_token_id"]] + [list(group) for group in mutually_exclusive or []]


class ProxmoxClient:
    """Validate connection parameters and lazily construct a proxmoxer client."""

    def __init__(self, module, service=None):
        """Store connection settings without opening an API connection.

        Args:
            module: Ansible module providing connection parameters and failures.
            service: Internal proxmoxer service name; None uses its PVE default.
        """
        self.module = module
        self.service = service
        self._api = None
        self._roles = None

    def fail_request(self, operation, exception, parameters=None):
        """Exit with API error details and a parameter-specific message when possible.

        Args:
            operation: Human-readable description of the failed API operation.
            exception: Original API exception, retained as text in 'api_error'.
            parameters: Submitted parameters used to identify HTTP 400 field errors.
        """
        details = {"error_type": type(exception).__name__}
        status = self.exception_status(exception)
        if status is not None:
            details["status_code"] = status
        api_error = str(exception)
        details["api_error"] = api_error
        message = "{0} failed: {1}".format(operation, api_error)
        errors = getattr(exception, "errors", None)
        if status == 400 and isinstance(errors, dict):
            for parameter, value in (parameters or {}).items():
                if parameter in errors:
                    message = "{0} value for {1} is not available".format(value, parameter)
                    break
        self.module.fail_json(msg=message, **details)

    @staticmethod
    def exception_status(exception):
        """Extract an HTTP status from an exception or its response.

        Args:
            exception: Error exposing status, status_code, or response.status_code.

        Returns:
            The first available status value, or None when none is provided.
        """
        for attribute in ("status", "status_code"):
            value = getattr(exception, attribute, None)
            if value is not None:
                return value

        response = getattr(exception, "response", None)
        if response is not None:
            return getattr(response, "status_code", None)
        return None

    def _token_identity(self, user, token_id):
        """Resolve a token identity, failing for malformed or conflicting users.

        Args:
            user: Configured API user, if supplied.
            token_id: Token name or full 'user@realm!token' identifier.

        Returns:
            A pair containing the API user and the unqualified token name.
        """
        if "!" not in token_id:
            return user, token_id

        token_user, token_name = token_id.split("!", 1)
        if not token_user or not token_name:
            self.module.fail_json(msg="api_token_id must use the format user@realm!token or token")
        if user and user != token_user:
            self.module.fail_json(msg="api_user must match the user embedded in api_token_id")
        return token_user, token_name

    def validate_pve_permissions(self, api, path, role="Administrator", require_propagation=True):
        """Fail unless effective PVE privileges satisfy the requested role.

        Role definitions are cached per client; permissions are fetched each time.
        Non-PVE services skip this check.

        Args:
            api: Connected proxmoxer client used to query roles and permissions.
            path: PVE ACL path whose effective privileges should be checked.
            role: Role defining the complete set of required privileges.
            require_propagation: Whether every required privilege must propagate.
        """
        if self.service not in (None, "pve"):
            return

        try:
            if self._roles is None:
                self._roles = api.access.roles.get()
            roles = self._roles
            permissions = api.access.permissions.get(path=path)
        except Exception as exception:
            self.fail_request(
                "Unable to verify effective PVE permissions for role {0} on {1}".format(role, path),
                exception,
            )

        role_info = next(
            (item for item in roles if isinstance(item, dict) and item.get("roleid") == role),
            None,
        ) if isinstance(roles, list) else None
        if role_info is None:
            self.module.fail_json(
                msg="Unable to verify PVE role {0}; refusing to continue.".format(role)
            )

        role_privileges = role_info.get("privs", "")
        if isinstance(role_privileges, str):
            role_privileges = {
                privilege.strip()
                for privilege in role_privileges.split(",")
                if privilege.strip()
            }
        else:
            role_privileges = set(role_privileges or [])

        effective = permissions.get(path) if isinstance(permissions, dict) else None
        if permissions == {}:
            effective = {}
        if not isinstance(effective, dict):
            self.module.fail_json(
                msg="Unexpected PVE permissions response for role {0} on {1}.".format(role, path)
            )

        missing = sorted(
            privilege
            for privilege in role_privileges
            if privilege not in effective or (require_propagation and not bool(effective[privilege]))
        )
        if missing:
            self.module.fail_json(
                msg="Authenticated identity lacks full {0} permissions propagated on {1}; missing: {2}.".format(
                    role, path, ", ".join(missing)
                )
            )

    def _validate_https_options(self, params):
        """Fail for conflicting authentication options or invalid TLS settings.

        Args:
            params: Module parameters containing password/token and TLS options.
        """
        password = params.get("api_password")
        token_id = params.get("api_token_id")
        token_secret = params.get("api_token_secret")
        otp = params.get("api_otp")

        if bool(password) == bool(token_id):
            self.module.fail_json(msg="Provide exactly one of api_password or api_token_id/api_token_secret for HTTPS")
        if token_id and not token_secret:
            self.module.fail_json(msg="api_token_secret is required when api_token_id is set")
        if token_secret and not token_id:
            self.module.fail_json(msg="api_token_id is required when api_token_secret is set")
        if token_id and otp:
            self.module.fail_json(msg="api_otp can only be used with HTTPS password authentication")
        if params.get("ca_path") and not params["validate_certs"]:
            self.module.fail_json(msg="ca_path requires validate_certs=true")

    def _https_options(self, params, options):
        """Validate HTTPS settings and populate proxmoxer options in place.

        Args:
            params: Module connection parameters, including authentication and TLS.
            options: Mutable keyword mapping receiving credentials and verify_ssl.
        """
        self._validate_https_options(params)
        user = params.get("api_user")
        password = params.get("api_password")
        token_id = params.get("api_token_id")
        token_secret = params.get("api_token_secret")
        otp = params.get("api_otp")

        if token_id:
            user, token_name = self._token_identity(user, token_id)
            options.update({"token_name": token_name, "token_value": token_secret})
        else:
            options["password"] = password
            if otp:
                options["otp"] = otp

        if not user:
            self.module.fail_json(msg="api_user is required for HTTPS password authentication or a token name without a user")

        options["user"] = user
        if params.get("ca_path") and params["validate_certs"]:
            options["verify_ssl"] = params["ca_path"]
        else:
            options["verify_ssl"] = params["validate_certs"]

    def connection_options(self):
        """Validate connection parameters and build proxmoxer HTTPS options.

        Returns:
            Connection keywords including authentication, timeout, and TLS settings.
            Invalid host, port, timeout, or authentication settings fail the module.
        """
        params = self.module.params
        if not params.get("api_host"):
            self.module.fail_json(msg="api_host must not be empty")
        if params["api_timeout"] <= 0:
            self.module.fail_json(msg="api_timeout must be greater than zero")
        port = params.get("api_port")
        if port is not None and not 1 <= port <= 65535:
            self.module.fail_json(msg="api_port must be between 1 and 65535")

        options = {
            "timeout": params["api_timeout"],
        }
        if self.service is not None:
            options["service"] = self.service
        if port is not None:
            options["port"] = port

        self._https_options(params, options)
        return options

    def get_api(self):
        """Construct the API client on first use and reuse it thereafter.

        Returns:
            The cached proxmoxer client. Missing dependencies or connection setup
            errors fail the module instead of returning a client.
        """
        if self._api is not None:
            return self._api
        if ProxmoxAPI is None:
            self.module.fail_json(
                msg=missing_required_lib("proxmoxer", url="https://github.com/proxmoxer/proxmoxer"),
                exception=PROXMOXER_IMPORT_ERROR,
            )

        if REQUESTS_IMPORT_ERROR:
            self.module.fail_json(msg=missing_required_lib("requests"), exception=REQUESTS_IMPORT_ERROR)

        try:
            self._api = ProxmoxAPI(self.module.params["api_host"], **self.connection_options())
        except Exception as exception:
            self.fail_request("Initializing the Proxmox API connection", exception)
        return self._api

    @property
    def api(self):
        """Return the proxmoxer client, initializing it lazily when first accessed."""
        return self.get_api()
