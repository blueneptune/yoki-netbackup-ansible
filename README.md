# yoki-netbackup-ansible

Ansible for NetBackup: the `yoki.netbackup` collection. Its modules are built on
[yoki-py-netbackup-sdk](../yoki-py-netbackup-sdk), and its roles build and configure NetBackup
primary and media servers.

It replaces the single `Install-NetBackup` role in `gdpr_mgmt_tools/ansible`, which:
- drives NetBackup through its command line (`bppolicynew`, `bpplsched`, `nbstl`, ...)
- uses a copy of Ansible's `uri` module for the REST API

## Why modules on the SDK, not `uri`

- **One proven way to call NetBackup.** Every call goes through the SDK, so it gets:
  - login (API key, or user and password with token renewal)
  - API version negotiation (10.0 / 10.5 / 11.0)
  - TLS against NetBackup's own CA
  - retries on reads, and changes sent exactly once
  - errors that keep NetBackup's reason
- **Declarative and idempotent.** A task says what should exist. The module reads what is there,
  changes only what differs, and reports `changed` and a `--diff` honestly.
- **Check mode can't change anything.** In check mode the SDK client is built read-only, so even
  a bug can't send a change.
- **Secrets stay out of output.** Keys and passwords are `no_log`, and no module returns them
  unless it exists to (and then marks them so).

## Where it sits

| Repo | Does |
|---|---|
| **yoki-netbackup-ansible** (this) | NetBackup builds and configuration, as Ansible |
| [yoki-py-netbackup-sdk](../yoki-py-netbackup-sdk) | The calls, proven against a real primary (`docs/live/`) |
| gdpr_mgmt_tools | The old `Install-NetBackup` role, until its parts move here |

## Modules

| Module | Does | NetBackup calls (proven live?) |
|---|---|---|
| `yoki.netbackup.nbu_ca_cert` | The primary's CA certificate: downloaded, checked by fingerprint, then pinned like an SSH host key | `/security/cacert` (yes, read) |
| `yoki.netbackup.nbu_info` | What the primary has: server, policies, SLPs, storage, hosts, retention levels | reads (yes) |
| `yoki.netbackup.nbu_policy` | A policy: present with these attributes, schedules, clients and selections, or absent | `/config/policies` (yes, on 10.2) |
| `yoki.netbackup.nbu_slp` | A storage lifecycle policy: present or absent | `/config/slps` (yes, on 10.2) |
| `yoki.netbackup.nbu_request` | Any other API call, through the SDK (the escape hatch; replaces `nbu_safe_uri`) | any |

More follow as the SDK proves their calls ([TODO.md](TODO.md)).

### They run on the controller

The modules talk to the primary server's API, so they run where the SDK is installed: on the
machine running Ansible (the tools server), not on the NetBackup host. Use one of:
- `delegate_to: localhost` on the task
- `connection: local` / `hosts: localhost` on the play

On any other host they fail with a message saying so.

### The CA certificate

The modules verify the primary against NetBackup's own CA (`ca_bundle`). `nbu_ca_cert`
downloads it (`GET /security/cacert` needs no login), and `playbooks/info.yml` does this for you
when no `-e nbu_ca_bundle=` is given:

1. **First run:** it stops and lists the CA's SHA-256/SHA-1 fingerprints. The download itself
   can't be verified (the CA is what's being fetched).
2. **Compare them on the primary:** `/usr/openv/netbackup/bin/nbcertcmd -displayCACertDetail`.
3. **Run again** with `-e nbu_ca_fingerprint=<SHA-256>`, or `-e nbu_ca_accept_new=true` only where
   the path to the primary is trusted. The CA is written to `out/ca/<host>.pem` (git-ignored),
   but only after it has been shown to verify the server's TLS.
4. **From then on that file is the pin.** If the server's CA changes, the module stops; a renewal
   is accepted by giving the new fingerprint.

If it says the certificate does not verify the server because of the host name, use the name on
the server's certificate (often the FQDN) as `nbu_primary`.

### Connection options (every module)

| Option | Env | Default |
|---|---|---|
| `url` | `NBU_URL` | required: `https://<primary>:1556/netbackup` |
| `api_key` | `NBU_API_KEY` | or `username` / `password` |
| `username`, `password` | `NBU_USERNAME`, `NBU_PASSWORD` | |
| `domain_type`, `domain_name` | `NBU_DOMAIN_TYPE`, `NBU_DOMAIN_NAME` | `vx` |
| `api_version` | `NBU_API_VERSION` | `auto` (13.0, then 12.0, then 7.0) |
| `ca_bundle` | `NBU_CA_BUNDLE` | NetBackup's CA certificate (PEM) to verify against |
| `validate_certs` | `NBU_VERIFY_TLS` | `true` |
| `timeout` | | `120` |
| `audit_reason` | | `yoki.netbackup Ansible`: sent as `X-NetBackup-Audit-Reason` on changes |

Set them once per play with `module_defaults: {group/yoki.netbackup.nbu: {...}}`.

```yaml
- hosts: localhost
  connection: local
  gather_facts: false
  module_defaults:
    group/yoki.netbackup.nbu:
      url: "https://{{ nbu_primary }}:1556/netbackup"
      api_key: "{{ vault_nbu_api_key }}"
      ca_bundle: files/nbu-ca.pem
  tasks:
    - name: File system policy for a media server
      yoki.netbackup.nbu_policy:
        name: ens25e.fs.u.media01
        template: ens25e.fs.u.template    # settings copied from here when the policy is created
        attributes:
          storage: media01_DeDup
          useAccelerator: true
        clients:
          - {hostName: media01, hardware: Linux, OS: RedHat}
        backup_selections: [ALL_LOCAL_DRIVES]
```

## Setup

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run ansible-playbook playbooks/<playbook>.yml --check --diff
```

The SDK is the sibling checkout `../yoki-py-netbackup-sdk` (editable) while it grows the calls
this repo needs. It becomes a released tag once they are in one.

## How work is done

As in the SDK (`docs/development.md` there):
- `F/<What_it_does>` and `B/<What_it_fixes>` branches, merged into `main` with `--no-ff`
- small commits, each with its tests and the checks passing
- no weekly release train while it is paused

## Status

Written against the SDK and its recorded answers from a 10.2 primary. **No module has been run
against a NetBackup server yet.** The first run is TODO 1.
