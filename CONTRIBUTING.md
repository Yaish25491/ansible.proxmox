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
