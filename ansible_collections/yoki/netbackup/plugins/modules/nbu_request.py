#!/usr/bin/python
"""yoki.netbackup.nbu_request: any NetBackup API call, through the SDK."""

from __future__ import annotations

DOCUMENTATION = r"""
module: nbu_request
short_description: Any NetBackup API call, through the SDK
description:
  - For a call no other module covers yet. The SDK still does the login, the API version, TLS,
    retries on reads and NetBackup's error reasons; it replaces the copied C(uri) module
    (C(nbu_safe_uri)) and hand-built C(Authorization) headers.
  - A C(GET) never changes anything and reports C(changed=false). Any other method reports
    C(changed=true), is sent exactly once (never retried), and is not sent in check mode.
  - Prefer a declarative module where there is one; this one cannot tell whether a change was
    needed.
version_added: 0.1.0
author: Michael Billings
extends_documentation_fragment: [yoki.netbackup.connection]
options:
  path:
    description: The path under I(url), e.g. C(/security/properties).
    type: str
    required: true
  method:
    description: The HTTP method.
    type: str
    choices: [GET, POST, PUT, PATCH, DELETE]
    default: GET
  query:
    description: "Query parameters, e.g. C({filter: \"sType eq 'PureDisk'\", page[limit]: 100})."
    type: dict
  headers:
    description: Extra headers. C(Authorization) and the API version are set by the SDK.
    type: dict
  body:
    description: A JSON body.
    type: raw
  missing_ok:
    description: Answer C(response=null) instead of failing when NetBackup says 404.
    type: bool
    default: false
notes:
  - Set C(no_log) on the task when the body or the answer holds a secret.
"""

EXAMPLES = r"""
- name: Data transport encryption setting
  yoki.netbackup.nbu_request:
    path: /security/properties
  delegate_to: localhost
  register: props

- name: DTE to preferred-on (only when it is not already)
  yoki.netbackup.nbu_request:
    path: /security/properties
    method: POST
    body: "{{ props.response | combine({'dteGlobalMode': 'PREFERRED_ON'}) }}"
  when: props.response.dteGlobalMode == 'PREFERRED_OFF'
  delegate_to: localhost
"""

RETURN = r"""
response:
  description: NetBackup's answer, decoded (JSON, text) or null when empty.
  returned: success
  type: raw
"""

from ansible_collections.yoki.netbackup.plugins.module_utils.nbu import (
    NetBackupModule,
    NotFoundError,
    SDKError,
)


def main() -> None:
    m = NetBackupModule(
        {
            "path": {"type": "str", "required": True},
            "method": {
                "type": "str",
                "choices": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "default": "GET",
            },
            "query": {"type": "dict"},
            "headers": {"type": "dict"},
            "body": {"type": "raw"},
            "missing_ok": {"type": "bool", "default": False},
        }
    )
    p = m.params
    method, path = p["method"], "/" + p["path"].lstrip("/")
    change = method != "GET"
    if change and m.check_mode:
        m.exit(changed=True, msg=f"check mode: {method} {path} not sent")
    headers = dict(p["headers"] or {})
    if change:
        headers.setdefault("X-NetBackup-Audit-Reason", m.audit)
    try:
        # The generic call behind every nbu.raw method: reads retried,
        # changes refused on a read-only client and sent once.
        answer = m.nbu.raw.config._call(
            method, path, params=p["query"], headers=headers, body=p["body"], change=change
        )
    except NotFoundError as error:
        if p["missing_ok"]:
            m.exit(changed=False, response=None)
        m.fail(f"{method} {path}", error)
    except SDKError as error:
        m.fail(f"{method} {path}", error)
    m.exit(changed=change, response=answer)


if __name__ == "__main__":
    main()
