"""Run a module in-process against a fake NetBackup.

FakeNetBackup answers the policy and SLP endpoints from what it holds, the
way a primary server would (404 for a missing one, the generic policy
schema, JSON:API resources), and records every request. Any request it does
not know fails the test instead of reaching the network.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from ansible.module_utils.testing import patch_module_args

URL = "https://nbu.test:1556/netbackup"
CONNECTION = {"url": URL, "api_key": "test-api-key", "validate_certs": False}


def not_found(what: str) -> httpx.Response:
    return httpx.Response(404, json={"errorCode": 404, "errorMessage": f"{what} not found"})


class FakeNetBackup:
    def __init__(self, router: respx.MockRouter) -> None:
        self.policies: dict[str, dict[str, Any]] = {}
        self.slps: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.router = router
        router.route(path__regex=r".*").mock(side_effect=self.answer)

    @property
    def changes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/netbackup")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if request.headers.get("Authorization") != "test-api-key":
            return httpx.Response(401, json={"errorMessage": "no key"})
        for pattern, kind in (
            (r"/config/policies/?(?P<name>[^/]*)$", "policy"),
            (r"/config/slps/?(?P<name>[^/]*)$", "slp"),
        ):
            match = re.match(pattern, path)
            if match:
                return self.crud(kind, request.method, match["name"], body)
        if path == "/security/serverinfo":
            return httpx.Response(
                200, json={"serverName": "primary-01", "nbuVersion": "NetBackup_10.2"}
            )
        return httpx.Response(599, json={"errorMessage": f"fake has no {request.method} {path}"})

    def crud(self, kind: str, method: str, name: str, body: Any) -> httpx.Response:
        store = self.policies if kind == "policy" else self.slps
        if method == "GET" and not name:
            return httpx.Response(
                200,
                json={
                    "data": [self.wrap(kind, n) for n in store],
                    "meta": {"pagination": {"count": len(store)}},
                },
            )
        if method == "POST":
            new = body["data"]["attributes"]
            new = new["policy"] if kind == "policy" else new
            store[body["data"]["id"]] = self.server_side(kind, copy.deepcopy(new))
            return httpx.Response(201 if kind == "slp" else 204)
        if name not in store:
            return not_found(f"{kind} {name}")
        if method == "GET":
            return httpx.Response(200, json={"data": self.wrap(kind, name)})
        if method == "PUT":
            new = body["data"]["attributes"]
            store[name] = self.server_side(
                kind, copy.deepcopy(new["policy"] if kind == "policy" else new)
            )
            return httpx.Response(204)
        if method == "DELETE":
            del store[name]
            return httpx.Response(204)
        return httpx.Response(405)

    @staticmethod
    def server_side(kind: str, item: dict[str, Any]) -> dict[str, Any]:
        """Fields NetBackup adds that a task never sends."""
        if kind == "slp":
            item["versionNumber"] = item.get("versionNumber", -1) + 1
            item.setdefault("state", "ACTIVE")
        else:
            for client in item.get("clients") or []:
                client.setdefault("databaseName", "")
        return item

    def wrap(self, kind: str, name: str) -> dict[str, Any]:
        store = self.policies if kind == "policy" else self.slps
        attrs = {"policy": store[name]} if kind == "policy" else store[name]
        return {"type": kind, "id": name, "attributes": copy.deepcopy(attrs)}


@pytest.fixture
def nbu() -> Iterator[FakeNetBackup]:
    with respx.mock(base_url=URL, assert_all_called=False) as router:
        yield FakeNetBackup(router)


def run(
    module: str,
    args: dict[str, Any],
    *,
    check: bool = False,
    diff: bool = False,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    """The module's result, as Ansible would get it."""
    mod = importlib.import_module(f"ansible_collections.yoki.netbackup.plugins.modules.{module}")
    full = {**CONNECTION, **args, "_ansible_check_mode": check, "_ansible_diff": diff}
    with patch_module_args(full), pytest.raises(SystemExit):
        mod.main()
    out = capsys.readouterr().out
    return json.loads(out.strip().splitlines()[-1])


@pytest.fixture
def module(capsys: pytest.CaptureFixture[str]):
    def call(_module: str, check: bool = False, diff: bool = False, **args: Any) -> dict[str, Any]:
        return run(_module, args, check=check, diff=diff, capsys=capsys)

    return call
