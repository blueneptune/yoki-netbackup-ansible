# TODO

Numbered so items can be referred to; struck through when done, with the commit.

## First run

1. **Run the modules against a lab primary.**
   - Done 2026-11-05 on eth00bck01 (NetBackup 10.2), `playbooks/info.yml`:
     - `nbu_ca_cert`: first contact stopped and listed the CA (`CN=nbatd`, valid to 2042); its
       fingerprints matched `nbcertcmd -displayCACertDetail` on the primary; pinned on the rerun
     - `nbu_info` (server, policies, SLPs) read through it, with the CA verified
   - Next: `nbu_policy` and `nbu_slp` on their own `ansible-test-*` objects only, check mode
     first. Record what differs from the SDK's recorded answers, and fix.
2. ~~**This GitHub repo**: `git@github.com:blueneptune/yoki-netbackup-ansible.git`, created by hand
   (no `gh` here), then `git push -u origin main`.~~ Done 2026-11-05.
3. **CI**: ruff, pytest and `ansible-lint` on every push, as in the SDK.

## Modules still to write (each needs its calls proven live in the SDK first)

4. `nbu_storage_server`, `nbu_disk_pool`, `nbu_storage_unit`: AdvancedDisk and MSDP, with
   credentials in the request body instead of on a command line (`tpconfig`, `PDDE_initConfig`).
   SDK: `/storage/*` changes are read-only proven so far.
5. `nbu_install_token`: one per build, a small use count and a short life, deleted after.
   `nbu_ca_fingerprint` (or part of `nbu_info`). SDK: `/security/securitytokens` reads proven.
6. `nbu_api_key`: present for a user, kept unless `rotate: true`, the key returned `no_log`.
   SDK: `/security/api-keys` reads proven.
7. `nbu_retention_level`, `nbu_resource_limits`, `nbu_security_properties` (DTE),
   `nbu_dr_passphrase` (`/config/credentials` / `/security/credentials`).
8. `nbu_manual_backup`: only when asked for, never as a side effect of configuring.
9. `nbu_host_config`: host properties such as the bp.conf timeouts
   (`/config/hosts/{id}/configuration`).

## Roles (the split of gdpr_mgmt_tools' Install-NetBackup)

10. `nbu_prereqs`, `nbu_trust`, `nbu_install`, `nbu_tuning`, `nbu_storage_advdisk`,
    `nbu_storage_msdp`, `nbu_primary_config`, `nbu_policies`, `nbu_slps`,
    `nbu_service_accounts`, and playbooks `build_primary.yml`, `build_media.yml`,
    `build_client.yml`. Moved one task file at a time, fixing the review's bugs as each moves
    (gdpr_mgmt_tools `docs/ROADMAP.md`, N1-N6). Secrets go into a vault first.
11. Borrow the pre-checks from Veritas' own roles (NBCheck before install, partial-install
    detection): [VeritasOS/netbackup-automation-platform](https://github.com/VeritasOS/netbackup-automation-platform).
