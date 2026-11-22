# Contributing

Refer to the [Ansible community guide](https://docs.ansible.com/projects/ansible/devel/community/index.html).

## Node DNS integration tests

The `proxmox_node_dns` target tests DNS configuration together with
`proxmox_node_info`. DNS mutation scenarios run by default and temporarily replace
and clear DNS settings for the entire node. Use a disposable node with recovery
access independent of the Proxmox API.

Generate `tests/integration/integration_config.yml` as described in the
[integration test instructions](README.md#integration-tests), set `proxmox_node`
in that file to the designated node, then run from the collection root:

```bash
ansible-test integration proxmox_node_dns
```

For read-only execution, add this setting to
`tests/integration/integration_config.yml` after generating it:

```yaml
dns_test_allow_reconfiguration: false
```

Information retrieval and check-mode scenarios always run. Setting the flag to
`false` skips DNS mutations and restoration.

The target captures the original API settings before making changes, restores
them in `always`, and independently verifies restoration. Recovery depends on
continued API access; resolver comments and formatting are not backed up by the
API. The DNS target's defaults enable mutation scenarios in both local and CI
runs; the shared configuration generator has no DNS-specific behavior.

## Node time integration tests

The `proxmox_node_time` target uses `proxmox_node_info` to verify timezone
updates. Use a designated test node: mutations run by default and temporarily
change the timezone for the entire node, affecting local scheduling and time
presentation. The target does not set the system clock or configure NTP.
Coordinate with other users and avoid concurrent timezone changes.

Generate the integration configuration using the
[integration test instructions](README.md#integration-tests), set `proxmox_node`
to the test node, then run from the collection root:

```bash
ansible-test integration proxmox_node_time
```

For read-only execution, add this setting to
`tests/integration/integration_config.yml` after generating it:

```yaml
time_test_allow_reconfiguration: false
```

Information retrieval, check-mode predictions, and rejected-input checks still
run. Full execution tests two different timezones, repeated updates, check-mode
no-ops and server rejection of an unavailable timezone. The node must support
the names in the target's `time_test_timezones` defaults; override that list with
at least two alternatives to the original timezone if needed.

Cleanup regression scenarios reuse the target's own timezone setting to verify
that ownership refusal and a restore request rejected before writing both allow
an independent final read and report unresolved restoration. The outer `always`
then restores with the normal ownership list and connection settings.

The target captures the original timezone, restores it in `always` only when the
current value belongs to the run's known settings, and independently verifies
restoration. UTC and local timestamps are observations that advance naturally;
they are never restored or compared for exact equality. Recovery requires API
access, and the API has no concurrency digest. The role-local defaults apply to
both local and CI runs; the shared generator and workflow require no changes.

## Node hosts integration tests

The `proxmox_node_hosts` target uses `proxmox_node_info` to verify complete hosts
contents and digests. Set `proxmox_node` in the private integration configuration
to a disposable node with independent recovery access, then run:

```bash
ansible-test integration proxmox_node_hosts
```

Mutation scenarios run by default. They preserve existing entries and append a
unique test mapping, change that mapping, exercise digest conflicts and restore
the original contents in `always`. Cleanup checks ownership and supplies the
current digest; it fails verification rather than overwriting unrecognized
concurrent edits. API access is still required for restoration.

The target also verifies the missing-node error path, cleanup refusal for an
unrecognized version of its own fixture, and continued verification after a
restore-module failure caused by an invalid request timeout. The outer cleanup
then restores and verifies the original file with normal connection options.

For information, validation and check-mode scenarios without writes, add this
setting to `tests/integration/integration_config.yml` after generating it:

```yaml
hosts_test_allow_reconfiguration: false
```

The live target never empties the hosts file. Empty-file replacement is covered
by unit tests and check-mode predictions. Target defaults apply equally to local
and CI runs; the shared configuration generator and workflow need no host-specific
configuration.

## Shared LXC integration test prerequisites

The `proxmox_lxc_template`, `proxmox_lxc_clone`, `proxmox_lxc_snapshot`, and
`proxmox_lxc_migrate` integration targets create LXC containers from
`fedora-44-default_20260713_amd64.tar.xz` by default. The `proxmox_lxc` module
looks up an unqualified template in `local:vztmpl` on the selected
`proxmox_node`; it does not download the archive. Stage the default template on
that node before running these targets, or container creation fails because the
OS template does not exist in storage.

To use another template, set `proxmox_lxc_template` in
`tests/integration/integration_config.yml` to its filename or volume ID. The
override must already be available as `vztmpl` content on the selected node.
The same override is also used by the `proxmox_lxc` and
`proxmox_lxc_powerstate` integration targets.

These targets hardcode `rootfs: local-lvm:8`. Each test node must have an
enabled `local-lvm` storage that supports container root filesystems and enough
free space for the 8 GiB fixture volume.

## LXC template integration tests

The `proxmox_lxc_template` target creates, starts, converts, updates, deletes and
recreates an LXC container on `proxmox_node`, then cleans it up. Use a disposable
node and generate the private configuration using the
[integration test instructions](README.md#integration-tests). See the shared
[LXC prerequisites](#shared-lxc-integration-test-prerequisites) before running
the target. Set `proxmox_node` to the test node, then run from the collection
root:

```bash
ansible-test integration proxmox_lxc_template
```

## LXC clone integration tests

The `proxmox_lxc_clone` target creates an LXC source container before exercising
the clone operation. See the shared
[LXC prerequisites](#shared-lxc-integration-test-prerequisites) before running
the target. Generate the private integration configuration using the
[integration test instructions](README.md#integration-tests), set
`proxmox_node` to the test node, then run from the collection root:

```bash
ansible-test integration proxmox_lxc_clone
```

## LXC migration integration tests

The `proxmox_lxc_migrate` target checks migration preconditions, verifies that a
running container requires `restart` in both normal and check mode,
then predicts and performs a restart migration between two cluster nodes. Use
the shared [LXC prerequisites](#shared-lxc-integration-test-prerequisites),
generate the private integration configuration, and set
`lxc_migrate_source_node` and `lxc_migrate_target_node` if the defaults do not
match the test cluster. Run from the collection root:

```bash
ansible-test integration proxmox_lxc_migrate
```

## Node syslog information integration tests

The `proxmox_node_syslog_info` target makes read-only queries against the
configured `proxmox_node`. Generate the private configuration using the
[integration test instructions](README.md#integration-tests), then run:

```bash
ansible-test integration proxmox_node_syslog_info
```

The target covers default and explicit pagination, service and date-time
filters, repeated reads, check mode, missing nodes and rejected inputs. Raw
journal contents are hidden from test output. It creates no resources, writes
no configuration and requires no cleanup. Logs may rotate between calls, so
pagination checks do not assume a stable journal snapshot. Override the target's
`syslog_test_service` default to query a different service if needed.

## User identity integration tests

The `proxmox_user` target tests the lifecycle module together with
`proxmox_user_info`, using the shared private integration configuration.
Run `ansible-test integration proxmox_user` from the collection root.
The target creates uniquely named, ownership-checked identity fixtures and
removes them in `always`, continuing cleanup after individual errors and
independently verifying removal. It does not assign ACLs or alter the
controller's authentication identity. Fixtures are passwordless PVE-realm users;
the primary user's lifecycle briefly exercises enable and disable operations.
The target checks literal percent signs, question marks and number signs in user
identifiers, including isolation from a separate account whose name resembles a
decoded percent escape. It also checks list-all and selected information reads
in normal and check mode. Full user listings are hidden from test output.
Run only with explicit authorization to create and remove these test identities.

## Group identity integration tests

The `proxmox_group` target tests the lifecycle module together with
`proxmox_group_info`, using the shared private integration configuration.
Run `ansible-test integration proxmox_group` from the collection root.
The target creates uniquely named, ownership-checked identity fixtures and
removes them in `always`, continuing cleanup after individual errors and
independently verifying removal. It does not assign ACLs or alter the
controller's authentication identity. Fixture users, where needed, are disabled
PVE-realm accounts without passwords. Coverage includes literal special user
identifiers, sibling-account isolation, membership reconciliation and omission,
list-all and selected information reads in check mode, creation/deletion
predictions, removal of a user's last group, deletion of a populated group while
preserving member accounts, and rejection of reserved group path segments.
Run only with explicit authorization to create and remove these test identities.

## Role identity integration tests

The `proxmox_role` target tests the lifecycle module together with
`proxmox_role_info`, using the shared private integration configuration.
Run `ansible-test integration proxmox_role` from the collection root.
The target creates uniquely named, ownership-checked identity fixtures and
removes them in `always`, continuing cleanup after individual errors and
independently verifying removal. It does not assign ACLs or alter the
controller's authentication identity. Coverage includes omitted-privilege creation, privilege replacement,
populated omission, explicit empty sets, list-all and selected information reads
in check mode, reserved role identifiers, and built-in update/deletion protection
with independent verification that built-in privileges remain unchanged.
Run only with explicit authorization to create and remove these test identities.

## Authentication realm integration tests

The `proxmox_realm` target tests configuration lifecycle together with
`proxmox_realm_info`, then independently verifies synchronized identities with
`proxmox_user_info` and `proxmox_group_info`. Run
`ansible-test integration proxmox_realm` using the shared private integration
configuration, only with authorization to create and remove temporary realms,
disabled users, groups and nonpropagating NoAccess ACL fixtures. It refuses
pre-existing realms, imported identities, suffixed groups and related orphaned
ACLs before claiming ownership.

Configuration scenarios use reserved `.invalid` provider addresses and cover
LDAP, AD and OpenID creation, reconciliation, omission, explicit removal,
idempotency, digest updates, check mode and deletion. Synchronization scenarios
start an owned, read-only LDAP fixture on an ephemeral controller port. The
Proxmox node must be able to reach that address and port. The target discovers
the controller's address from its route to `proxmox_api_host`; override
`realm_test_ldap_advertise_host` for runners behind NAT or with multiple network
interfaces. The target does not change firewall rules or network configuration.

Both LDAP and AD scenarios exercise bind connectivity, native dry runs,
users/groups/both scopes, disabled-user imports, repeated synchronization,
property removal, ACL cleanup, vanished-entry removal and stored bind-password
removal. The fixture contains only synthetic records and a dummy bind password;
it does not authenticate real users or validate OpenID login flows. Built-in
realm configuration and reset are predicted only in check mode. No test changes
the controller's realm or the cluster login default.

Cleanup attempts every owned binding, group and user before deleting the owned
realms, continues after individual errors and independently verifies removal.
An outer `always` also stops the owned LDAP service and verifies removal of its
allocated directory. A failed verification remains a test failure.

Provider tests verify LDAP/AD transport, fallback servers, boolean defaults,
and OpenID claim/group/userinfo settings with independent reads. Bind passwords
and client keys are dummy values for disconnected owned fixtures, and secret
fields are excluded from results. Additional OpenID audience tests require
`libpve-access-control >= 9.1.1`; enable them explicitly with
`realm_test_audiences_supported: true` only on a compatible server.

## proxmox_permission_info integration tests

ACA-5631 explicitly defines this read-only reader separately from ACL lifecycle
(ACA-5629). ACL assignments are managed by `proxmox_acl`; this target verifies
their calculated effect without adding another writer.

Run `ansible-test integration proxmox_permission_info` with the shared private
integration configuration. This target makes read-only requests, verifies normal
and check-mode result shapes, and exercises rejected inputs. It creates no
resources and requires no cleanup. API results are hidden from test output.

The target independently discovers the configured administrative user, then
queries the authenticated identity, that user and the configured full API token
in normal and check mode. At the default `/access` path it compares concrete
privileges with each subject's full tree and verifies known propagated privileges.
For a custom path, comparison applies when the API enumerates that path in the
full tree; the native tree does not enumerate every possible descendant path.
A zero propagation
flag still grants the privilege at that path; it does not mean denied.

The shared `prepare_tests` dependency generates unique identifiers only. A unique
missing user ID is independently verified absent, then queried in both modes to
verify an empty tree and a selected path with an empty privilege mapping. No
user, ACL or token is created or changed, so cleanup is inapplicable. Invalid
user/path requests are also exercised in both modes.

Password-authenticated runs must provide `permission_test_tokenid` for an existing
test token with matching administrative access; a missing token fixture fails
clearly. Password authentication can use the shared `proxmox_api_otp` setting.
All authentication and TLS options come from the shared private configuration.

Selected and returned paths must belong to supported PVE 9 ACL namespaces.
Unsupported paths, malformed privilege maps and invalid propagation flags fail
explicitly in both modes. Path validity does not establish object existence;
valid paths for not-yet-created objects retain their native inheritance semantics.
