"""nbu_slp, nbu_info and nbu_request against the fake NetBackup."""

from __future__ import annotations

import pytest

OPS = [
    {
        "operationType": "BACKUP",
        "storage": {"name": "tpl_ADV_DISK"},
        "retentionPeriod": {"value": 35},
    },
    {
        "operationType": "DUPLICATION",
        "storage": {"name": "tpl_DeDup"},
        "retentionPeriod": {"value": 35},
    },
]


@pytest.fixture
def with_slp(nbu):
    nbu.slps["RRD_35D_TEMPLATE_AD"] = {
        "slpName": "RRD_35D_TEMPLATE_AD",
        "duplicationPriority": 0,
        "operationList": OPS,
        "versionNumber": 3,
        "state": "ACTIVE",
    }
    return nbu


NEW = {
    "name": "RRD_35D_MEDIA01_AD",
    "template": "RRD_35D_TEMPLATE_AD",
    "attributes": {
        "operationList": [
            {"storage": {"name": "media01_ADV_DISK"}},
            {"storage": {"name": "media01_DeDup"}},
        ]
    },
}


def test_slp_from_a_template_with_its_storage_changed(with_slp, module):
    result = module("nbu_slp", **NEW)
    assert result["action"] == "created"
    ops = with_slp.slps["RRD_35D_MEDIA01_AD"]["operationList"]
    assert [o["storage"]["name"] for o in ops] == ["media01_ADV_DISK", "media01_DeDup"]
    assert [o["operationType"] for o in ops] == ["BACKUP", "DUPLICATION"]  # kept, item by item
    sent = with_slp.changes[-1][2]["data"]["attributes"]
    assert "versionNumber" not in sent and "state" not in sent  # NetBackup's own


def test_slp_second_run_and_versions_are_not_changes(with_slp, module):
    module("nbu_slp", **NEW)
    assert module("nbu_slp", **NEW)["changed"] is False
    result = module("nbu_slp", name="RRD_35D_MEDIA01_AD", attributes={"duplicationPriority": 5})
    assert result["action"] == "updated" and with_slp.changes[-1][0] == "PUT"
    assert with_slp.slps["RRD_35D_MEDIA01_AD"]["duplicationPriority"] == 5


def test_slp_check_mode_and_absent(with_slp, module):
    assert module("nbu_slp", check=True, **NEW)["changed"] and with_slp.changes == []
    module("nbu_slp", **NEW)
    assert module("nbu_slp", name="RRD_35D_MEDIA01_AD", state="absent")["action"] == "deleted"
    assert module("nbu_slp", name="RRD_35D_MEDIA01_AD", state="absent")["changed"] is False


def test_a_new_slp_needs_operations(nbu, module):
    result = module("nbu_slp", name="empty")
    assert result["failed"] and "operationList" in result["msg"]


def test_info_reads_and_changes_nothing(with_slp, module):
    with_slp.policies["p1"] = {
        "policyName": "p1",
        "policyType": "Standard",
        "policyAttributes": {"active": False},
    }
    result = module("nbu_info", gather_subset=["server", "policies", "slps"], check=True)
    info = result["netbackup"]
    assert result["changed"] is False and with_slp.changes == []
    assert info["server"]["nbuVersion"] == "NetBackup_10.2" and info["server"]["api_version"]
    assert info["policies"] == [{"name": "p1", "type": "Standard", "active": False}]
    assert [s["slpName"] for s in info["slps"]] == ["RRD_35D_TEMPLATE_AD"]


def test_request_get_is_not_a_change_and_missing_ok(with_slp, module):
    result = module("nbu_request", path="/config/slps/RRD_35D_TEMPLATE_AD")
    assert result["changed"] is False
    assert result["response"]["data"]["attributes"]["versionNumber"] == 3
    assert module("nbu_request", path="/config/slps/nope", missing_ok=True)["response"] is None
    assert module("nbu_request", path="/config/slps/nope")["failed"]


def test_request_change_is_sent_once_with_an_audit_reason_never_in_check_mode(with_slp, module):
    assert module(
        "nbu_request", path="/config/slps/RRD_35D_TEMPLATE_AD", method="DELETE", check=True
    )["changed"]
    assert with_slp.changes == []
    result = module("nbu_request", path="config/slps/RRD_35D_TEMPLATE_AD", method="DELETE")
    assert result["changed"] and with_slp.slps == {}
    assert len(with_slp.changes) == 1


def test_without_the_sdk_it_says_where_to_run(module, monkeypatch):
    from ansible_collections.yoki.netbackup.plugins.module_utils import nbu

    monkeypatch.setattr(nbu, "HAS_SDK", False)
    result = module("nbu_info")
    assert result["failed"] and "delegate_to: localhost" in result["msg"]
