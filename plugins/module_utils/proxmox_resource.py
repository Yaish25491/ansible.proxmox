# Copyright: (c) 2026, Ansible Proxmox Collection contributors
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from abc import abstractmethod

from .proxmox_base import ProxmoxBase


class ProxmoxResourceModule(ProxmoxBase):
    """Abstract create/update/delete lifecycle for Proxmox resources."""

    @abstractmethod
    def get_resource(self):
        """Retrieve the resource, returning None when it does not exist."""
        pass

    @abstractmethod
    def create_resource(self):
        """Create the resource and return its result data."""
        pass

    def get_update_parameters(self, _resource):
        """Return API changes needed for an existing resource.

        Args:
            _resource: Current resource data from the API.

        Returns:
            A parameter mapping; empty by default to leave the resource unchanged.
        """
        return {}

    @abstractmethod
    def update_resource(self, _resource, _updates):
        """Apply changes and return the updated resource data.

        Args:
            _resource: Current resource data from the API.
            _updates: API parameters to update, including any delete directive.
        """
        pass

    @abstractmethod
    def delete_resource(self, _resource):
        """Delete the resource and return any result data.

        Args:
            _resource: Current resource data from the API.
        """
        pass

    @staticmethod
    def omit_default_parameters(parameters, parameter_defaults):
        """Remove creation parameters that equal a configured API default.

        Args:
            parameters: Desired API parameter mapping.
            parameter_defaults: Defaults by name; missing or empty means unknown.

        Returns:
            A new mapping retaining non-default values and unknown defaults.
        """
        filtered = {}
        for key, value in parameters.items():
            default_value = parameter_defaults.get(key, "")
            if default_value == "" or value != default_value:
                filtered[key] = value
        return filtered

    @staticmethod
    def _serialize_formatted_options(options, primary_field=None):
        """Encode options as a comma-separated Proxmox property string.

        Args:
            options: Option mapping; None values are omitted and bools use 1/0.
            primary_field: Optional field emitted first without its key.

        Returns:
            An API string such as 'virtio,bridge=vmbr0,firewall=1'.
        """
        fields = []
        if primary_field:
            primary_value = options.get(primary_field)
            if primary_value is not None:
                fields.append(str(primary_value))

        for option, value in options.items():
            if option == primary_field or value is None:
                continue
            if isinstance(value, bool):
                value = str(int(value))
            fields.append("{0}={1}".format(option, value))
        return ",".join(fields)

    def _formatted_entry_name(self, parent, entry, prefix):
        """Validate an entry's API name, failing the module for invalid names.

        Args:
            parent: Structured module parameter name, such as 'net'.
            entry: Option mapping containing a 'name' field.
            prefix: Indexed name prefix, or None for an exact parent name.

        Returns:
            The validated API parameter name, such as 'net0'.
        """
        if not isinstance(entry, dict) or "name" not in entry:
            self.module.fail_json(
                msg="Each {0} entry must be a mapping with a name such as {1}0.".format(parent, prefix)
            )

        api_parameter = entry["name"]
        if prefix is None:
            if api_parameter != parent:
                self.module.fail_json(msg="{0} entry name must be {0}.".format(parent))
        else:
            suffix = ""
            if isinstance(api_parameter, str):
                suffix = api_parameter[len(prefix) :]
            if not isinstance(api_parameter, str) or not api_parameter.startswith(prefix) or not suffix.isdigit():
                self.module.fail_json(
                    msg="{0} entry name must use the {1}<index> format.".format(parent, prefix)
                )
        return api_parameter

    def _formatted_entries(self, parent, entries, prefix):
        """Yield validated API names and options, rejecting duplicate names.

        Args:
            parent: Structured module parameter name.
            entries: List of option mappings with 'name' fields.
            prefix: Indexed name prefix, or None for an exact parent name.

        Yields:
            Pairs of API parameter names and new option mappings without 'name'.
        """
        seen = set()
        for entry in entries:
            api_parameter = self._formatted_entry_name(parent, entry, prefix)
            if api_parameter in seen:
                self.module.fail_json(msg="Duplicate formatted API parameter {0}.".format(api_parameter))
            seen.add(api_parameter)
            options = dict(entry)
            options.pop("name")
            yield api_parameter, options

    def _serialize_formatted_entries(self, parent, entries, prefix, primary_field, fixed_options):
        """Serialize named entries into indexed Proxmox property strings.

        Args:
            parent: Structured parameter name.
            entries: Named option mappings to serialize.
            prefix: Expected API parameter prefix, or None for an exact name.
            primary_field: Optional unkeyed option field.
            fixed_options: Options applied to every entry.

        Returns:
            API parameter names mapped to serialized property strings.
        """
        serialized = {}
        for api_parameter, options in self._formatted_entries(parent, entries, prefix):
            options.update(fixed_options)
            serialized[api_parameter] = self._serialize_formatted_options(options, primary_field)
        return serialized

    def serialize_formatted_parameters(
        self,
        parameters,
        formatted_parameters,
        prefixes,
        primary_fields=None,
        fixed_options=None,
    ):
        """Expand structured module values into Proxmox API parameters.

        Args:
            parameters: Desired values; formatted values are strings or lists.
            formatted_parameters: Names requiring property-string serialization.
            prefixes: Indexed API prefixes by parent; None means an exact name.
            primary_fields: Optional unkeyed field names by parent.
            fixed_options: Required option mappings by parent, overriding input.

        Returns:
            A new mapping with formatted parents replaced by API property strings.
            Unformatted parameters pass through and None parents are omitted.
        """
        primary_fields = primary_fields or {}
        fixed_options = fixed_options or {}
        serialized = {}
        for key, value in parameters.items():
            if key not in formatted_parameters:
                serialized[key] = value

        for parent in formatted_parameters:
            entries = parameters.get(parent)
            if entries is None:
                continue
            if isinstance(entries, str):
                primary_field = primary_fields.get(parent)
                options = {"value": entries}
                if primary_field:
                    options = {primary_field: entries}
                options.update(fixed_options.get(parent, {}))
                serialized[parent] = self._serialize_formatted_options(options, primary_field)
                continue
            if not isinstance(entries, list):
                self.module.fail_json(msg="{0} must be a list or string.".format(parent))
            serialized.update(
                self._serialize_formatted_entries(
                    parent,
                    entries,
                    prefixes.get(parent, parent),
                    primary_fields.get(parent),
                    fixed_options.get(parent, {}),
                )
            )
        return serialized

    @staticmethod
    def _parse_formatted_options(value):
        """Parse a Proxmox property string without interpreting field values.

        Args:
            value: Comma-separated API string, such as 'virtio,bridge=vmbr0'.

        Returns:
            String fields by key; unkeyed fields map to an empty string.
            Empty or non-string input produces an empty mapping.
        """
        if not isinstance(value, str) or not value:
            return {}

        options = {}
        for item in value.split(","):
            key, separator, field_value = item.partition("=")
            if key.strip():
                parsed_value = ""
                if separator:
                    parsed_value = field_value.strip()
                options[key.strip()] = parsed_value
        return options

    @staticmethod
    def _formatted_values_equal(current, desired):
        """Compare a property value using the API's string representation.

        Args:
            current: Existing API value; bools use 1/0 and None never matches.
            desired: Requested value, with bools represented as 1/0.

        Returns:
            Whether both values have the same serialized representation.
        """
        if isinstance(current, bool):
            current = str(int(current))
        if isinstance(desired, bool):
            desired = str(int(desired))
        return current is not None and str(current) == str(desired)

    def _formatted_options_equal(self, current_options, desired_options):
        """Check requested fields without comparing unspecified API options.

        Args:
            current_options: Parsed option mapping from the API.
            desired_options: Only the fields that should be compared.

        Returns:
            Whether every requested field matches its current value.
        """
        for key, value in desired_options.items():
            if not self._formatted_values_equal(current_options.get(key), value):
                return False
        return True

    def _current_formatted_options(self, current_value, primary_field, fixed_options):
        """Normalize current API fields for comparison with structured input.

        Args:
            current_value: Existing comma-separated API property string.
            primary_field: Optional name assigned to the unkeyed primary value.
            fixed_options: Fallback options for fields missing from the API.

        Returns:
            Parsed options with the primary value named and fallbacks filled in.
        """
        current_options = self._parse_formatted_options(current_value)
        if primary_field and primary_field not in current_options and current_options:
            primary_key = next(iter(current_options))
            for key, value in current_options.items():
                if value == "":
                    primary_key = key
                    break
            current_options.pop(primary_key)
            current_options[primary_field] = primary_key
        for option, option_value in fixed_options.items():
            current_options.setdefault(option, option_value)
        return current_options

    def _scalar_formatted_update(self, parent, entries, resource, primary_field, fixed_options):
        """Serialize a scalar formatted value only when its fields differ.

        Args:
            parent: API parameter name for the scalar value.
            entries: Requested string value.
            resource: Current API configuration mapping.
            primary_field: Optional unkeyed field name for the requested value.
            fixed_options: Required options that override requested fields.

        Returns:
            The replacement API string, or None when no update is needed.
        """
        desired_options = {"value": entries}
        if primary_field:
            desired_options = {primary_field: entries}
        desired_options.update(fixed_options)
        current_options = self._current_formatted_options(
            resource.get(parent),
            primary_field,
            fixed_options,
        )
        if self._formatted_options_equal(current_options, desired_options):
            return None
        return self._serialize_formatted_options(desired_options, primary_field)

    def _formatted_entry_update(self, api_parameter, desired_options, resource, primary_field, fixed_options):
        """Merge changed entry options while preserving unrequested API fields.

        Args:
            api_parameter: Existing API parameter name, such as 'net0'.
            desired_options: Requested entry fields; None values are ignored.
            resource: Current API configuration mapping.
            primary_field: Optional unkeyed field name.
            fixed_options: Required fields, overridden by non-None desired fields.

        Returns:
            The merged API string, or None when requested fields already match.
        """
        current_options = self._current_formatted_options(
            resource.get(api_parameter),
            primary_field,
            fixed_options,
        )
        requested_options = {}
        for option_group in (fixed_options, desired_options):
            for key, value in option_group.items():
                if value is not None:
                    requested_options[key] = value
        if self._formatted_options_equal(current_options, requested_options):
            return None

        updated_options = dict(current_options)
        updated_options.update(requested_options)
        return self._serialize_formatted_options(updated_options, primary_field)

    def _list_formatted_updates(self, parent, entries, resource, prefix, primary_field, fixed_options):
        """Compare a complete entry list and identify stale indexed devices.

        Args:
            parent: Structured module parameter name.
            entries: Desired list of named option mappings.
            resource: Current API configuration mapping.
            prefix: Indexed prefix, or None to disable stale-device removal.
            primary_field: Optional unkeyed field name.
            fixed_options: Required options for each entry.

        Returns:
            A pair containing changed API parameters and sorted stale names.
            An empty list requests removal of all entries matching the prefix.
        """
        updates = {}
        desired_names = set()
        for api_parameter, desired_options in self._formatted_entries(parent, entries, prefix):
            desired_names.add(api_parameter)
            update = self._formatted_entry_update(
                api_parameter,
                desired_options,
                resource,
                primary_field,
                fixed_options,
            )
            if update is not None:
                updates[api_parameter] = update

        if prefix is None:
            return updates, []
        current_names = set()
        for key in resource:
            if key.startswith(prefix) and key[len(prefix) :].isdigit():
                current_names.add(key)
        return updates, sorted(current_names - desired_names)

    @staticmethod
    def _merge_formatted_deletions(updates, deletions):
        """Add stale parameter names to an update's delete directive in order.

        Args:
            updates: API updates to modify in place.
            deletions: Stale parameter names to add without duplicates.

        Returns:
            The updated mapping, unchanged when deletions is empty.
        """
        if not deletions:
            return updates

        existing_deletions = []
        for key in updates.get("delete", "").split(","):
            if key:
                existing_deletions.append(key)
        for key in deletions:
            if key not in existing_deletions:
                existing_deletions.append(key)
        updates["delete"] = ",".join(existing_deletions)
        return updates

    def get_formatted_parameter_updates(
        self,
        resource,
        parameters,
        formatted_parameters,
        prefixes,
        primary_fields=None,
        fixed_options=None,
    ):
        """Build formatted API updates, including removal of stale entries.

        Args:
            resource: Current API configuration mapping.
            parameters: Desired values; None parents leave API entries untouched.
            formatted_parameters: Parent names requiring formatted comparison.
            prefixes: Indexed API prefixes by parent; None disables removal.
            primary_fields: Optional unkeyed field names by parent.
            fixed_options: Required option mappings by parent.

        Returns:
            Changed API strings with stale names in a comma-separated 'delete'
            parameter. Unspecified fields in retained entries are preserved.
        """
        primary_fields = primary_fields or {}
        fixed_options = fixed_options or {}
        updates = {}
        deletions = []

        for parent in formatted_parameters:
            entries = parameters.get(parent)
            if entries is None:
                continue
            if isinstance(entries, str):
                primary_field = primary_fields.get(parent)
                update = self._scalar_formatted_update(
                    parent,
                    entries,
                    resource,
                    primary_field,
                    fixed_options.get(parent, {}),
                )
                if update is not None:
                    updates[parent] = update
                continue
            if not isinstance(entries, list):
                self.module.fail_json(msg="{0} must be a list or string.".format(parent))

            prefix = prefixes.get(parent, parent)
            primary_field = primary_fields.get(parent)
            parent_updates, parent_deletions = self._list_formatted_updates(
                parent,
                entries,
                resource,
                prefix,
                primary_field,
                fixed_options.get(parent, {}),
            )
            updates.update(parent_updates)
            deletions.extend(parent_deletions)

        return self._merge_formatted_deletions(updates, deletions)

    @staticmethod
    def parameter_values_equal(current, desired):
        """Compare plain values, accepting numeric API strings as numbers.

        Args:
            current: Existing API value, possibly a numeric string.
            desired: Requested value; bools are not converted to integers.

        Returns:
            Whether values are equal directly or after numeric conversion.
        """
        if current == desired:
            return True
        if isinstance(current, str):
            if isinstance(desired, int) and not isinstance(desired, bool):
                try:
                    return int(current) == desired
                except ValueError:
                    return False
            if isinstance(desired, float):
                try:
                    return float(current) == desired
                except ValueError:
                    return False
        return False

    @staticmethod
    def get_parameter_updates(resource, desired_parameters, parameter_defaults):
        """Compare plain parameters and reset configured defaults by deletion.

        Args:
            resource: Current API configuration mapping.
            desired_parameters: Requested plain parameter values.
            parameter_defaults: Defaults by name; missing or empty means unknown.

        Returns:
            Changed values and a comma-separated 'delete' directive for defaults.
            Absent or empty current values already satisfy a configured default.
        """
        updates = {}
        deleted_parameters = []

        for key, value in desired_parameters.items():
            current = resource.get(key)
            default_value = parameter_defaults.get(key, "")
            if default_value != "" and ProxmoxResourceModule.parameter_values_equal(value, default_value):
                if current is None or current == "":
                    continue
                if ProxmoxResourceModule.parameter_values_equal(current, default_value):
                    continue
                deleted_parameters.append(key)
            elif not ProxmoxResourceModule.parameter_values_equal(current, value):
                updates[key] = value

        if deleted_parameters:
            updates["delete"] = ",".join(deleted_parameters)
        return updates

    @staticmethod
    def apply_parameter_updates(resource, updates):
        """Apply predicted API changes without modifying the original resource.

        Args:
            resource: Current API configuration mapping.
            updates: Changed fields and an optional comma-separated 'delete'.

        Returns:
            A new mapping with deletions applied before updated values.
        """
        updated = dict(resource)
        for key in updates.get("delete", "").split(","):
            if key:
                updated.pop(key, None)
        for key, value in updates.items():
            if key != "delete":
                updated[key] = value
        return updated

    def get_check_mode_resource(self, operation, resource=None, updates=None):
        """Predict result data without creating, updating, or deleting a resource.

        Args:
            operation: Lifecycle operation: 'create', 'update', or 'delete'.
            resource: Current resource data, if available.
            updates: Optional API parameter mapping for an update.

        Returns:
            None for deletion, a patched mapping for supported updates, or the
            supplied resource otherwise. Subclasses may predict creation data.
        """
        if operation == "delete":
            return None
        if operation == "update" and isinstance(resource, dict) and isinstance(updates, dict):
            return self.apply_parameter_updates(resource, updates)
        return resource

    def execute_resource_module(self):
        """Reconcile the requested state and exit with the module result.

        Check mode predicts changes without invoking mutation hooks. Existing
        resources are updated only when get_update_parameters returns changes.
        """
        state = self.module.params.get("state", "present")
        resource = self.get_resource()

        if state == "absent":
            if resource is None:
                self.exit(changed=False, resource=None)
            if self.module.check_mode:
                self.exit(changed=True, resource=self.get_check_mode_resource("delete", resource=resource))
            result = self.delete_resource(resource)
            self.exit(changed=True, resource=result)

        if resource is None:
            if self.module.check_mode:
                self.exit(changed=True, resource=self.get_check_mode_resource("create"))
            self.exit(changed=True, resource=self.create_resource())

        updates = self.get_update_parameters(resource)
        if not updates:
            self.exit(changed=False, resource=resource)
        if self.module.check_mode:
            self.exit(changed=True, resource=self.get_check_mode_resource("update", resource=resource, updates=updates))
        self.exit(changed=True, resource=self.update_resource(resource, updates))

    def execute(self):
        """Run the resource lifecycle through the common handler entry point."""
        self.execute_resource_module()
