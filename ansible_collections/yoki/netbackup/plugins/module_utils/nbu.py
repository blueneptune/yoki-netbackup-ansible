"""What every yoki.netbackup module shares: the connection options, the SDK
client built from them, NetBackup's errors as Ansible failures, and the
merging that makes "this is what should exist" into "change only what
differs".

The SDK (netbackup_sdk) does the talking: login, API version, TLS, retries
on reads, changes sent once. In check mode the client is built read-only
(allow_changes=False), so a module cannot change anything even by mistake.
"""

from __future__ import annotations

import copy
import traceback
from typing import Any

from ansible.module_utils.basic import AnsibleModule, env_fallback, missing_required_lib

try:
    from netbackup_sdk import NetBackupClient
    from sdk_primer import NotFoundError, SDKError

    HAS_SDK = True
    SDK_IMPORT_ERROR = None
except ImportError:  # reported by NetBackupModule, with how to fix it
    HAS_SDK = False
    SDK_IMPORT_ERROR = traceback.format_exc()

    class NotFoundError(Exception):  # type: ignore[no-redef]
        pass

    class SDKError(Exception):  # type: ignore[no-redef]
        pass


CONNECTION_ARGS: dict[str, dict[str, Any]] = {
    "url": {"type": "str", "required": True, "fallback": (env_fallback, ["NBU_URL"])},
    "api_key": {"type": "str", "no_log": True, "fallback": (env_fallback, ["NBU_API_KEY"])},
    "username": {"type": "str", "fallback": (env_fallback, ["NBU_USERNAME"])},
    "password": {"type": "str", "no_log": True, "fallback": (env_fallback, ["NBU_PASSWORD"])},
    "domain_type": {
        "type": "str",
        "default": "vx",
        "fallback": (env_fallback, ["NBU_DOMAIN_TYPE"]),
    },
    "domain_name": {"type": "str", "fallback": (env_fallback, ["NBU_DOMAIN_NAME"])},
    "api_version": {
        "type": "str",
        "default": "auto",
        "fallback": (env_fallback, ["NBU_API_VERSION"]),
    },
    "ca_bundle": {"type": "path", "fallback": (env_fallback, ["NBU_CA_BUNDLE"])},
    "validate_certs": {
        "type": "bool",
        "default": True,
        "fallback": (env_fallback, ["NBU_VERIFY_TLS"]),
    },
    "timeout": {"type": "float", "default": 120.0},
    "audit_reason": {"type": "str", "default": "yoki.netbackup Ansible"},
}

ON_THE_CONTROLLER = (
    " The yoki.netbackup modules run on the controller, where the SDK is installed: "
    "use 'delegate_to: localhost' on the task, or a 'connection: local' play."
)


class NetBackupModule:
    """An AnsibleModule with the connection options, and the SDK client
    built from them on first use."""

    def __init__(self, argument_spec: dict[str, Any], **kwargs: Any) -> None:
        kwargs.setdefault("supports_check_mode", True)
        self.module = AnsibleModule(
            argument_spec={**CONNECTION_ARGS, **argument_spec},
            required_one_of=[("api_key", "username")],
            required_together=[("username", "password")],
            mutually_exclusive=[("api_key", "username")],
            **kwargs,
        )
        if not HAS_SDK:
            self.module.fail_json(
                msg=missing_required_lib("netbackup-sdk (yoki-py-netbackup-sdk)")
                + ON_THE_CONTROLLER,
                exception=SDK_IMPORT_ERROR,
            )
        self.params = self.module.params
        self.check_mode = self.module.check_mode
        self._client: Any = None

    @property
    def nbu(self) -> Any:
        if self._client is None:
            p = self.params
            self._client = NetBackupClient(
                p["url"],
                api_key=p["api_key"],
                username=p["username"],
                password=p["password"],
                domain_type=p["domain_type"],
                domain_name=p["domain_name"],
                api_version=p["api_version"],
                verify_tls=p["validate_certs"],
                ca_bundle=p["ca_bundle"],
                timeout=p["timeout"],
                allow_changes=not self.check_mode,
            )
        return self._client

    @property
    def audit(self) -> str:
        return self.params["audit_reason"]

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # closing is best effort; never hide the real result
                pass
            self._client = None

    def exit(self, **result: Any) -> None:
        self.close()
        self.module.exit_json(**result)

    def fail(self, msg: str, error: BaseException | None = None, **result: Any) -> None:
        """Fail with NetBackup's own reason and status, when it gave them."""
        self.close()
        if error is not None:
            msg = f"{msg}: {error}"
            result.setdefault("status_code", getattr(error, "status_code", None))
            detail = getattr(error, "detail", None)
            if detail is not None:
                result.setdefault("detail", detail)
        self.module.fail_json(msg=msg, **result)

    def diff(self, before: Any, after: Any) -> dict[str, Any]:
        """{'diff': ...} for exit(), when the run asked for --diff."""
        return {"diff": {"before": before, "after": after}} if self.module._diff else {}


def get_or_none(call: Any, *args: Any, **kwargs: Any) -> Any:
    """A read that answers None for 404 instead of raising."""
    try:
        return call(*args, **kwargs)
    except NotFoundError:
        return None


def attributes(body: Any) -> Any:
    """The attributes of a JSON:API answer: {"data": {"attributes": {...}}}."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"].get("attributes")
    return body


def resource(kind: str, rid: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """A request body as NetBackup takes it: a JSON:API resource."""
    return {"data": {"type": kind, "id": rid, "attributes": attrs}}


def all_pages(call: Any, page_limit: int = 100, **kwargs: Any) -> list[dict[str, Any]]:
    """Every item of a paged `nbu.raw` listing (page[limit] / page[offset]),
    stopping on a short page."""
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        body = call(page_limit=page_limit, page_offset=offset, **kwargs) or {}
        page = (body.get("data") or []) if isinstance(body, dict) else []
        items.extend(page)
        if len(page) < page_limit:
            return items
        offset += page_limit


def merge(current: Any, desired: Any) -> Any:
    """`desired` laid over `current`, leaving everything it does not mention.

    - dicts are merged key by key, recursively
    - a list of dicts as long as the current one is merged item by item
      (NetBackup returns extra fields in each item that a task does not
      repeat)
    - anything else in `desired` replaces what is there
    """
    if isinstance(current, dict) and isinstance(desired, dict):
        out = copy.deepcopy(current)
        for key, value in desired.items():
            out[key] = merge(current[key], value) if key in current else copy.deepcopy(value)
        return out
    if (
        isinstance(current, list)
        and isinstance(desired, list)
        and len(current) == len(desired)
        and all(isinstance(i, dict) for i in current + desired)
    ):
        return [merge(c, d) for c, d in zip(current, desired, strict=True)]
    return copy.deepcopy(desired)


def merge_by_key(
    current: list[dict[str, Any]] | None,
    desired: list[dict[str, Any]] | None,
    key: str,
    purge: bool = False,
    base: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Items matched on `key` (a schedule's name, a client's host name):
    each desired item merged over its current one, new ones added at the
    end (laid over `base` when given), and the rest kept unless `purge`."""
    current = current or []
    if desired is None:
        return copy.deepcopy(current)
    wanted = {item[key]: item for item in desired}
    out = []
    for item in current:
        if item.get(key) in wanted:
            out.append(merge(item, wanted.pop(item[key])))
        elif not purge:
            out.append(copy.deepcopy(item))
    for item in desired:
        if item[key] in wanted:
            out.append(merge(base, item) if base else copy.deepcopy(item))
    return out
