"""nbu_policy: create from a template, change only what differs, never in check mode."""

from __future__ import annotations

import copy

import pytest

TEMPLATE = {
    "policyName": "fs.template",
    "policyType": "Standard",
    "policyAttributes": {
        "active": True,
        "storage": "any_stu",
        "useAccelerator": False,
        "priority": 0,
        "keyword": "",
    },
    "clients": [{"hostName": "template-client", "hardware": "Linux", "OS": "RedHat"}],
    "schedules": [
        {
            "scheduleName": "weekly-full",
            "scheduleType": "FULL",
            "frequencySeconds": 604800,
            "backupCopies": {"copies": [{"retentionPeriod": {"value": 35, "unit": "DAYS"}}]},
        },
        {
            "scheduleName": "daily-diff",
            "scheduleType": "DIFFERENTIAL_INCREMENTAL",
            "frequencySeconds": 86400,
        },
    ],
    "backupSelections": {"selections": ["/template"]},
}

ARGS = {
    "name": "fs.media01",
    "template": "fs.template",
    "attributes": {"storage": "media01_DeDup", "useAccelerator": True},
    "clients": [{"hostName": "media01", "hardware": "Linux", "OS": "RedHat"}],
    "backup_selections": ["ALL_LOCAL_DRIVES"],
}


@pytest.fixture
def with_template(nbu):
    nbu.policies["fs.template"] = copy.deepcopy(TEMPLATE)
    return nbu


def test_creates_from_the_template_without_its_clients_or_selections(with_template, module):
    result = module("nbu_policy", **ARGS)
    assert result["changed"] and result["action"] == "created"
    made = with_template.policies["fs.media01"]
    assert made["policyAttributes"]["storage"] == "media01_DeDup"
    assert made["policyAttributes"]["useAccelerator"] is True
    assert made["policyAttributes"]["active"] is True  # copied
    assert [s["scheduleName"] for s in made["schedules"]] == ["weekly-full", "daily-diff"]
    assert [c["hostName"] for c in made["clients"]] == ["media01"]  # not template-client
    assert made["backupSelections"]["selections"] == ["ALL_LOCAL_DRIVES"]
    post = [c for c in with_template.changes if c[0] == "POST"]
    assert post and post[0][2]["data"]["type"] == "policy"


def test_a_second_run_changes_nothing(with_template, module):
    module("nbu_policy", **ARGS)
    before = len(with_template.changes)
    result = module("nbu_policy", **ARGS)
    assert result["changed"] is False and result["action"] == "none"
    assert len(with_template.changes) == before  # nothing sent


def test_changes_only_what_differs_and_sends_the_whole_policy(with_template, module):
    module("nbu_policy", **ARGS)
    result = module("nbu_policy", name="fs.media01", attributes={"priority": 7}, diff=True)
    assert result["action"] == "updated"
    put = with_template.changes[-1]
    assert put[0] == "PUT" and put[1] == "/config/policies/fs.media01"
    sent = put[2]["data"]["attributes"]["policy"]
    assert sent["policyAttributes"]["priority"] == 7
    assert sent["policyAttributes"]["storage"] == "media01_DeDup"  # kept
    assert result["diff"]["before"]["policyAttributes"]["priority"] == 0


def test_check_mode_sends_nothing(with_template, module):
    result = module("nbu_policy", check=True, **ARGS)
    assert result["changed"] and result["action"] == "created"
    assert with_template.changes == [] and "fs.media01" not in with_template.policies


def test_schedules_merge_by_name_and_new_ones_start_from_a_base(with_template, module):
    module("nbu_policy", **ARGS)
    module(
        "nbu_policy",
        name="fs.media01",
        schedule_base="weekly-full",
        schedules=[
            {"scheduleName": "weekly-full", "frequencySeconds": 1209600},
            {"scheduleName": "monthly-full"},
        ],
    )
    schedules = {s["scheduleName"]: s for s in with_template.policies["fs.media01"]["schedules"]}
    assert schedules["weekly-full"]["frequencySeconds"] == 1209600
    assert schedules["weekly-full"]["scheduleType"] == "FULL"  # kept
    assert schedules["monthly-full"]["scheduleType"] == "FULL"  # from the base
    assert "daily-diff" in schedules  # not purged


def test_purge_removes_what_is_not_listed(with_template, module):
    module("nbu_policy", **ARGS)
    module(
        "nbu_policy",
        name="fs.media01",
        purge_schedules=True,
        schedules=[{"scheduleName": "daily-diff"}],
        purge_clients=True,
        clients=[{"hostName": "media02", "hardware": "Linux", "OS": "RedHat"}],
    )
    policy = with_template.policies["fs.media01"]
    assert [s["scheduleName"] for s in policy["schedules"]] == ["daily-diff"]
    assert [c["hostName"] for c in policy["clients"]] == ["media02"]


def test_extra_fields_netbackup_adds_do_not_count_as_a_change(with_template, module):
    module("nbu_policy", **ARGS)
    assert with_template.policies["fs.media01"]["clients"][0]["databaseName"] == ""  # server-side
    assert module("nbu_policy", **ARGS)["changed"] is False


def test_absent_deletes_once(with_template, module):
    module("nbu_policy", **ARGS)
    assert module("nbu_policy", name="fs.media01", state="absent")["action"] == "deleted"
    assert "fs.media01" not in with_template.policies
    assert module("nbu_policy", name="fs.media01", state="absent")["changed"] is False


def test_a_missing_template_or_another_type_fails(with_template, module):
    result = module("nbu_policy", name="x", template="nope")
    assert result["failed"] and "template policy nope does not exist" in result["msg"]
    module("nbu_policy", **ARGS)
    result = module("nbu_policy", name="fs.media01", policy_type="MS-Windows")
    assert result["failed"] and "is of type Standard" in result["msg"]


def test_netbackups_reason_is_kept_and_the_key_never_shown(nbu, module):
    def refuse(request):
        import httpx

        return httpx.Response(
            400, json={"errorCode": 8000, "errorMessage": "storage unit not found"}
        )

    nbu.router.routes.clear()
    nbu.router.route().mock(side_effect=refuse)
    result = module("nbu_policy", name="fs.x", attributes={"storage": "nope"})
    assert result["failed"] and "storage unit not found" in result["msg"]
    assert "test-api-key" not in str(result)
