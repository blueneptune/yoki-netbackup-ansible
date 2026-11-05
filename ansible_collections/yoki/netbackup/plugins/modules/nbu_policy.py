#!/usr/bin/python
"""yoki.netbackup.nbu_policy: a NetBackup policy, present or absent."""

from __future__ import annotations

DOCUMENTATION = r"""
module: nbu_policy
short_description: A NetBackup policy, present with these settings, or absent
description:
  - Reads the policy (C(GET /config/policies/{name}), generic schema), lays the given
    I(attributes), I(schedules), I(clients) and I(backup_selections) over it, and sends it back
    whole (C(PUT)) only if that changes anything.
  - A missing policy is created (C(POST /config/policies)), copying its settings and schedules
    from I(template) when given, the way the SDK's live tests do.
  - Replaces the command-line chain C(bppolicynew), C(bpplinfo), C(bpplsched),
    C(bpplschedrep), C(bpplclients) and C(bpplinclude).
  - Never starts a backup.
version_added: 0.1.0
author: Michael Billings
extends_documentation_fragment: [yoki.netbackup.connection]
options:
  name:
    description: The policy name.
    type: str
    required: true
  state:
    description: C(present) to create or update it, C(absent) to delete it.
    type: str
    choices: [present, absent]
    default: present
  policy_type:
    description:
      - The policy type, e.g. C(Standard), C(MS-Windows), C(VMware). A new policy without it, and
        without I(template), is C(Standard).
      - When given, an existing policy of another type is an error (a type is not changed in
        place).
    type: str
  template:
    description:
      - An existing policy to copy settings (C(policyAttributes)) and schedules from when this
        one is created. Its clients and backup selections are not copied.
      - Without it, the policy is created from the given options alone, and NetBackup fills in
        its defaults.
    type: str
  attributes:
    description:
      - Policy attributes (C(policyAttributes)) to set, by NetBackup's names, e.g. C(active),
        C(storage), C(useAccelerator), C(priority), C(keyword). Anything not given is left as it
        is.
    type: dict
  schedules:
    description:
      - Schedules by C(scheduleName). Each is laid over the schedule of that name; a new one is
        added as given (laid over I(schedule_base) when that is set).
    type: list
    elements: dict
  schedule_base:
    description:
      - The name of a schedule (in the policy, or in I(template) on creation) that new schedules
        start from, so a task only gives what differs.
    type: str
  purge_schedules:
    description: Remove schedules that are not in I(schedules).
    type: bool
    default: false
  clients:
    description:
      - Clients by C(hostName), each with C(hardware) and C(OS) as NetBackup knows them
        (e.g. C(Linux) / C(RedHat)).
    type: list
    elements: dict
  purge_clients:
    description: Remove clients that are not in I(clients).
    type: bool
    default: false
  backup_selections:
    description: What to back up, e.g. C([ALL_LOCAL_DRIVES]). Replaces the current list.
    type: list
    elements: str
"""

EXAMPLES = r"""
- name: File system and dedup policies for a media server
  yoki.netbackup.nbu_policy:
    url: "https://{{ nbu_primary }}:1556/netbackup"
    api_key: "{{ vault_nbu_api_key }}"
    ca_bundle: files/nbu-ca.pem
    name: "ens{{ region }}e.fs.u.{{ inventory_hostname_short }}"
    template: "ens{{ region }}e.fs.u.template"
    attributes:
      storage: "{{ inventory_hostname_short }}_DeDup"
      useAccelerator: true
    clients:
      - hostName: "{{ inventory_hostname_short }}"
        hardware: Linux
        OS: RedHat
    backup_selections: [ALL_LOCAL_DRIVES]
  delegate_to: localhost

- name: Switch a policy off
  yoki.netbackup.nbu_policy:
    name: old.policy
    attributes: {active: false}
  delegate_to: localhost

- name: Remove a policy
  yoki.netbackup.nbu_policy:
    name: old.policy
    state: absent
  delegate_to: localhost
"""

RETURN = r"""
policy:
  description: The policy as NetBackup has it after the run (generic schema); null when absent.
  returned: always
  type: dict
action:
  description: What was done, C(created), C(updated), C(deleted) or C(none).
  returned: always
  type: str
"""

import copy

from ansible_collections.yoki.netbackup.plugins.module_utils.nbu import (
    NetBackupModule,
    SDKError,
    attributes,
    get_or_none,
    merge,
    merge_by_key,
    resource,
)


def read(m: NetBackupModule, name: str) -> dict | None:
    body = get_or_none(m.nbu.raw.config.get_policy, name)
    return None if body is None else (attributes(body) or {}).get("policy")


def schedule_named(policy: dict | None, name: str | None) -> dict | None:
    for schedule in (policy or {}).get("schedules") or []:
        if schedule.get("scheduleName") == name:
            return schedule
    return None


def desired_policy(m: NetBackupModule, current: dict | None, template: dict | None) -> dict:
    p = m.params
    if current is not None:
        policy = copy.deepcopy(current)
    elif template is not None:
        policy = copy.deepcopy(template)
        policy["clients"] = []
        policy["backupSelections"] = {"selections": []}
    else:
        policy = {
            "policyType": p["policy_type"] or "Standard",
            "policyAttributes": {},
            "clients": [],
            "schedules": [],
            "backupSelections": {"selections": []},
        }
    policy["policyName"] = p["name"]
    if p["policy_type"]:
        policy["policyType"] = p["policy_type"]

    if p["attributes"]:
        policy["policyAttributes"] = merge(policy.get("policyAttributes") or {}, p["attributes"])
    base = schedule_named(policy, p["schedule_base"]) or schedule_named(
        template, p["schedule_base"]
    )
    policy["schedules"] = merge_by_key(
        policy.get("schedules"), p["schedules"], "scheduleName", p["purge_schedules"], base
    )
    policy["clients"] = merge_by_key(
        policy.get("clients"), p["clients"], "hostName", p["purge_clients"]
    )
    if p["backup_selections"] is not None:
        policy["backupSelections"] = merge(
            policy.get("backupSelections") or {}, {"selections": p["backup_selections"]}
        )
    return policy


def validate(m: NetBackupModule) -> None:
    p = m.params
    for kind, key in (("schedules", "scheduleName"), ("clients", "hostName")):
        for item in p[kind] or []:
            if not item.get(key):
                m.fail(f"every item of {kind} needs {key}")
    if p["schedule_base"] and not p["schedules"]:
        m.fail("schedule_base is only used with schedules")


def main() -> None:
    m = NetBackupModule(
        {
            "name": {"type": "str", "required": True},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
            "policy_type": {"type": "str"},
            "template": {"type": "str"},
            "attributes": {"type": "dict"},
            "schedules": {"type": "list", "elements": "dict"},
            "schedule_base": {"type": "str"},
            "purge_schedules": {"type": "bool", "default": False},
            "clients": {"type": "list", "elements": "dict"},
            "purge_clients": {"type": "bool", "default": False},
            "backup_selections": {"type": "list", "elements": "str"},
        }
    )
    validate(m)
    name = m.params["name"]
    try:
        current = read(m, name)

        if m.params["state"] == "absent":
            if current is None:
                m.exit(changed=False, action="none", policy=None)
            if not m.check_mode:
                m.nbu.raw.config.delete_policy(name, x_net_backup_audit_reason=m.audit)
            m.exit(changed=True, action="deleted", policy=None, **m.diff(current, None))

        wanted_type = m.params["policy_type"]
        if current is not None and wanted_type and current.get("policyType") != wanted_type:
            m.fail(
                f"policy {name} is of type {current.get('policyType')}, not "
                f"{m.params['policy_type']}; a type is not changed in place"
            )
        template = None
        if current is None and m.params["template"]:
            template = read(m, m.params["template"])
            if template is None:
                m.fail(f"template policy {m.params['template']} does not exist")

        wanted = desired_policy(m, current, template)
        if wanted == current:
            m.exit(changed=False, action="none", policy=current)

        action = "created" if current is None else "updated"
        if not m.check_mode:
            body = resource("policy", name, {"policy": wanted})
            if current is None:
                m.nbu.raw.config.post_policies(x_net_backup_audit_reason=m.audit, body=body)
            else:
                m.nbu.raw.config.put_policy(name, x_net_backup_audit_reason=m.audit, body=body)
            wanted = read(m, name) or wanted
        m.exit(changed=True, action=action, policy=wanted, **m.diff(current, wanted))
    except SDKError as error:
        m.fail(f"Policy {name}: the NetBackup call failed", error)


if __name__ == "__main__":
    main()
