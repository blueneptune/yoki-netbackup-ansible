#!/usr/bin/python
"""yoki.netbackup.nbu_slp: a storage lifecycle policy, present or absent."""

from __future__ import annotations

DOCUMENTATION = r"""
module: nbu_slp
short_description: A NetBackup storage lifecycle policy (SLP), present or absent
description:
  - Reads the SLP (C(GET /config/slps/{name})), lays the given I(attributes) over it, and sends
    it back (C(PUT)) only if that changes anything; a missing SLP is created (C(POST /config/slps)).
  - Replaces C(nbstl ... -add) and the C(nbstl -json | grep) check before it.
  - NetBackup keeps every version of an SLP; a change makes a new version (C(versionNumber)).
version_added: 0.1.0
author: Michael Billings
extends_documentation_fragment: [yoki.netbackup.connection]
options:
  name:
    description: The SLP name.
    type: str
    required: true
  state:
    description: C(present) to create or update it, C(absent) to delete it.
    type: str
    choices: [present, absent]
    default: present
  template:
    description:
      - An existing SLP to copy C(operationList), C(duplicationPriority) and
        C(dataClassification) from when this one is created.
    type: str
  attributes:
    description:
      - SLP attributes by NetBackup's names, e.g. C(duplicationPriority), C(dataClassification),
        C(operationList).
      - C(operationList) of the same length as the current one is merged operation by operation,
        so a task can change one field of one operation; a list of another length replaces it.
    type: dict
    default: {}
"""

EXAMPLES = r"""
- name: 35-day SLP for a media server, from the standard one
  yoki.netbackup.nbu_slp:
    name: "RRD_35D_{{ inventory_hostname_short | upper }}_AD"
    template: RRD_35D_TEMPLATE_AD
    attributes:
      operationList:
        - storage: {name: "{{ inventory_hostname_short }}_ADV_DISK"}
        - storage: {name: "{{ inventory_hostname_short }}_DeDup"}
  delegate_to: localhost
"""

RETURN = r"""
slp:
  description: The SLP's attributes as NetBackup has them after the run; null when absent.
  returned: always
  type: dict
action:
  description: What was done, C(created), C(updated), C(deleted) or C(none).
  returned: always
  type: str
"""

from ansible_collections.yoki.netbackup.plugins.module_utils.nbu import (
    NetBackupModule,
    SDKError,
    attributes,
    get_or_none,
    merge,
    resource,
)

# What an SLP is written with (as the SDK's live tests do); the rest of a
# GET's answer (versionNumber, state, ...) is NetBackup's own.
WRITABLE = ("slpName", "duplicationPriority", "operationList", "dataClassification")


def read(m: NetBackupModule, name: str) -> dict | None:
    body = get_or_none(m.nbu.raw.config.get_slp, name)
    return None if body is None else attributes(body) or {}


def writable(attrs: dict, extra: dict) -> dict:
    return {k: v for k, v in attrs.items() if k in WRITABLE or k in extra}


def main() -> None:
    m = NetBackupModule(
        {
            "name": {"type": "str", "required": True},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
            "template": {"type": "str"},
            "attributes": {"type": "dict", "default": {}},
        }
    )
    name, given = m.params["name"], m.params["attributes"]
    try:
        current = read(m, name)

        if m.params["state"] == "absent":
            if current is None:
                m.exit(changed=False, action="none", slp=None)
            if not m.check_mode:
                m.nbu.raw.config.delete_slp(name)
            m.exit(changed=True, action="deleted", slp=None, **m.diff(current, None))

        if current is None:
            base: dict = {}
            if m.params["template"]:
                template = read(m, m.params["template"])
                if template is None:
                    m.fail(f"template SLP {m.params['template']} does not exist")
                base = writable(template, {})
            wanted = merge(base, given)
            wanted["slpName"] = name
            if not wanted.get("operationList"):
                m.fail(f"SLP {name} does not exist: give attributes.operationList, or a template")
            if not m.check_mode:
                m.nbu.raw.config.post_slps(
                    x_net_backup_audit_reason=m.audit, body=resource("slp", name, wanted)
                )
                wanted = read(m, name) or wanted
            m.exit(changed=True, action="created", slp=wanted, **m.diff(None, wanted))

        before = writable(current, given)
        after = merge(before, given)
        after["slpName"] = name
        if after == before:
            m.exit(changed=False, action="none", slp=current)
        result = after
        if not m.check_mode:
            m.nbu.raw.config.put_slp(
                name, x_net_backup_audit_reason=m.audit, body=resource("slp", name, after)
            )
            result = read(m, name) or after
        m.exit(changed=True, action="updated", slp=result, **m.diff(before, after))
    except SDKError as error:
        m.fail(f"SLP {name}: the NetBackup call failed", error)


if __name__ == "__main__":
    main()
