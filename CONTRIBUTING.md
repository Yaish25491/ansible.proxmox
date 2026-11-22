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
`proxmox_realm_info`. Run `ansible-test integration proxmox_realm` using the
shared private integration configuration, only with authorization to create
and remove temporary realm configurations. It preflights three unique realm
identifiers, then exercises LDAP, AD and OpenID creation, reconciliation,
omission, explicit removal, idempotency, digest updates, check mode and deletion.
Cleanup attempts every owned realm and independently verifies removal.

The fixtures use reserved `.invalid` provider addresses and never request a
connection check, start synchronization, create identities or change the login
default. These tests validate stored configuration, not external provider
connectivity or successful user authentication. Actual LDAP/AD/OpenID behavior
requires separate provider fixtures. Built-in and controller authentication
realms are protected; default-realm side effects are covered by unit tests.

Provider tests verify LDAP/AD transport, fallback servers, boolean defaults,
and OpenID claim/group/userinfo settings with independent reads. Bind passwords
and client keys are dummy values for disconnected owned fixtures, and secret
fields are excluded from results. Additional OpenID audience tests require
`libpve-access-control >= 9.1.1`; enable them explicitly with
`realm_test_audiences_supported: true` only on a compatible server.

## Two-factor authentication integration tests

The `proxmox_tfa` target exercises `proxmox_tfa` and `proxmox_tfa_info`
together, including `state: configured` and `state: unlocked` operations. It
creates one uniquely named disabled, passwordless PVE user and a disconnected
LDAP realm definition.
Both identifiers are preflighted absent before claiming ownership. Authentication
is shared through group module defaults; the role-local realm helper reuses the
resource framework. It never changes the controller's identity or an existing
realm. The target uses root@pam password authentication for factor deletion;
non-root DELETE confirmation is intentionally not sent in a URL.

Set `tfa_test_allow_enrollment: true` only when these owned fixtures are
authorized, then run `ansible-test integration --allow-disabled proxmox_tfa`. Missing password
authentication or explicit authorization fails with an explanation rather than
claiming mutation coverage. Existing token-only CI credentials are insufficient.
For a generated local configuration, set `PROXMOX_TFA_ALLOW_ENROLLMENT=true`
together with `PROXMOX_USER=root@pam` and `PROXMOX_PASSWORD`, leaving token
variables unset. The enrollment opt-in defaults to false.

The target is marked `disabled` to keep it out of the global token-authenticated
pipeline. `.github/workflows/proxmox_tfa.yml` runs this paired target explicitly
with `--allow-disabled`, an isolated Python environment and protected environment
approval. It requires repository secret `PROXMOX_TFA_ROOT_PASSWORD` and repository
variable `PROXMOX_TFA_ALLOW_ENROLLMENT=true` for the designated endpoint. Missing
prerequisites fail before execution. Configuration is regenerated with `make -B`
under a restrictive umask, then removed in an `always` cleanup step.

Automatic live runs use `pull_request` events from the same repository, checked
by immutable repository IDs. Fork PRs require an explicitly authorized manual
run with the full reviewed commit SHA in the workflow's `ref` input. The
protected environment approval applies to every run; no unsafe privileged PR
checkout or bot-name trust check is used.

CI installs published Core 2.16, 2.18, 2.20 and 2.21 wheels from the hash-locked
files under `tests/integration/requirements/tfa/`, then calls `ansible-test`
directly. Refresh the four Core pins and common dependency hashes together and
validate every lock on Linux x86_64 with Python 3.12. Only binary distributions
are accepted; `make integration` is avoided because it reinstalls unlocked
requirements. No secret, opt-in or workflow approval is created by this PR.

Default scenarios cover TOTP lifecycle, recovery-code creation and one-time
returns, native user/realm type reads, conditional unlock on an unlocked owned
user, and realm TOTP/Yubico property reconciliation. The Yubico configuration
uses a reserved `.invalid` validation URL and dummy API key on the unused owned
realm; it does not call a Yubico provider or enroll a device. Cluster WebAuthn/U2F
configuration is read and predicted in check mode only. Existing cluster settings
are independently verified unchanged. Actual lockout reset and cluster-setting
writes have unit coverage; these scenarios do not force account lockouts or
change the existing cluster's authentication settings.

Sensitive tasks use `no_log: true`. Recovery codes are never printed or
persisted by the target. Cleanup removes owned factors before users, attempts
realm cleanup even after individual errors, and independently verifies final
absence. Failed cleanup is exposed. The test realm has no users and provider
connection checks are disabled.

Hardware and real Yubico enrollment require external fixtures. To exercise them,
set `tfa_test_allow_provider_enrollment: true` and provide `tfa_test_provider_cases`
as a list of mappings containing `type` (`webauthn`, `u2f` or `yubico`), `userid`
and `response_command` (an argument list, not a shell command). Owners must be
existing disabled `ansible-` test accounts marked with the exact comment
`Owned Ansible TFA provider fixture`, with the required realm/cluster provider
settings already configured. They must not be root or the operator, and must
already have an enabled factor and the modern TFA marker. A read-only role-local
helper verifies that marker without returning legacy keys. This prevents failed
hardware registration from enabling TFA on an account that previously had none.
The command receives the native serialized registration request on standard
input for hardware types and returns the serialized authenticator response on
standard output. For Yubico it receives empty input and returns a fresh device
OTP. It must implement the authenticator's physical interaction externally.

Each provider case preflights its unique type/description selector, claims only
that absent factor, exercises enrollment and a repeated no-op, then removes only
that factor and independently verifies the owner's original factors are intact.
The original user attributes and modern TFA marker are also independently
verified after cleanup. The existing owner is never created, changed or removed.
Hardware challenges can expire server-side and there is no cancel/list-pending API; starting a challenge
is reported as pending, not as a completed factor. With the provider gate disabled,
the target explicitly reports hardware/Yubico live coverage as unexecuted.
