#!/usr/bin/python
"""yoki.netbackup.nbu_info: what a NetBackup primary server has. Reads only."""

from __future__ import annotations

DOCUMENTATION = r"""
module: nbu_info
short_description: What a NetBackup primary server has (reads only)
description:
  - The server and the API version in use, and lists of policies, SLPs, storage units, disk
    pools, storage servers, hosts and retention levels, as NetBackup returns them.
  - Changes nothing, so it runs the same in check mode.
version_added: 0.1.0
author: Michael Billings
extends_documentation_fragment: [yoki.netbackup.connection]
options:
  gather_subset:
    description: What to read. C(all) reads everything.
    type: list
    elements: str
    choices: [all, server, policies, slps, storage_units, disk_pools, storage_servers, hosts, retention_levels]
    default: [server, policies, slps, storage_units, disk_pools]
"""

EXAMPLES = r"""
- name: What the primary has
  yoki.netbackup.nbu_info:
    url: "https://{{ nbu_primary }}:1556/netbackup"
    api_key: "{{ vault_nbu_api_key }}"
    gather_subset: [server, policies]
  delegate_to: localhost
  register: nbu

- name: Policy names
  ansible.builtin.debug:
    msg: "{{ nbu.netbackup.policies | map(attribute='name') | list }}"
"""

RETURN = r"""
netbackup:
  description: One key per subset read.
  returned: always
  type: dict
  contains:
    server:
      description: C(GET /security/serverinfo) (serverName, nbuVersion, ...) and C(api_version).
      type: dict
    policies:
      description: Each policy's C(name), C(type) and C(active).
      type: list
    slps:
      description: Each SLP's attributes.
      type: list
    storage_units:
      description: Each storage unit's attributes, with C(id).
      type: list
    disk_pools:
      description: Each disk pool's attributes, with C(id).
      type: list
    storage_servers:
      description: Each storage server's attributes, with C(id).
      type: list
    hosts:
      description: The hosts the primary knows.
      type: list
    retention_levels:
      description: C(GET /config/retentionlevels) as returned.
      type: raw
"""

from ansible_collections.yoki.netbackup.plugins.module_utils.nbu import (
    NetBackupModule,
    SDKError,
    all_pages,
)

SUBSETS = (
    "server",
    "policies",
    "slps",
    "storage_units",
    "disk_pools",
    "storage_servers",
    "hosts",
    "retention_levels",
)


def flat(item: dict) -> dict:
    out = dict(item.get("attributes") or {})
    out.setdefault("id", item.get("id"))
    return out


def gather(m: NetBackupModule, subset: str):
    nbu = m.nbu
    if subset == "server":
        info = nbu.server.info()
        return {**info, "api_version": nbu.api_version}
    if subset == "policies":
        out = []
        for item in nbu.policies.list():
            policy = item.get("policy") or {}
            attrs = policy.get("policyAttributes") or {}
            out.append(
                {
                    "name": policy.get("policyName", item.get("id")),
                    "type": policy.get("policyType"),
                    "active": attrs.get("active"),
                }
            )
        return out
    if subset == "slps":
        return [flat(i) for i in all_pages(nbu.raw.config.list_slps)]
    if subset == "storage_units":
        return nbu.storage.units()
    if subset == "disk_pools":
        return nbu.storage.disk_pools()
    if subset == "storage_servers":
        return [flat(i) for i in all_pages(nbu.raw.storage.list_storage_servers)]
    if subset == "hosts":
        return nbu.hosts.list()
    return nbu.raw.hostproperties.get_retentionlevels()


def main() -> None:
    m = NetBackupModule(
        {
            "gather_subset": {
                "type": "list",
                "elements": "str",
                "choices": ["all", *SUBSETS],
                "default": ["server", "policies", "slps", "storage_units", "disk_pools"],
            }
        }
    )
    wanted = m.params["gather_subset"]
    subsets = SUBSETS if "all" in wanted else [s for s in SUBSETS if s in wanted]
    result = {}
    for subset in subsets:
        try:
            result[subset] = gather(m, subset)
        except SDKError as error:
            m.fail(f"Reading {subset} from NetBackup failed", error)
    m.exit(changed=False, netbackup=result)


if __name__ == "__main__":
    main()
