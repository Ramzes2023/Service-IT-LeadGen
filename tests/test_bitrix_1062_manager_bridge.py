"""The manager bridge is synthetic-only and never connects to Bitrix24."""
from datetime import date, timedelta
import json
import socket

import pytest

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import SyntheticPlanRejected, build_synthetic_1062_plan
from leadpilot.bitrix_1062_manager_bridge import build_synthetic_1062_manager_plan

NOW = date(2026, 11, 1)


def _data():
    return build_offline_handoff(synthetic_fixture(NOW), today=NOW)


def _review(index=0, decision="relevant"):
    base = build_synthetic_1062_plan(_data(), today=NOW)
    key = base["requests_not_sent"][index]["idempotency_key"]
    return {
        "idempotency_key": key,
        "event_id": "DEMO-REVIEW-00001",
        "decision": decision,
        "reviewer_role": "manager",
        "reviewer_label": "DEMO-MANAGER-1",
        "reason": "Синтетическая проверка сигнала для демонстрации",
        "reviewed_on": NOW.isoformat(),
    }


def test_unreviewed_is_not_treated_as_approved():
    outcome = build_synthetic_1062_manager_plan(_data(), [], today=NOW)
    assert outcome["status"] == "BITRIX_1062_MANAGER_PREVIEW_ONLY"
    assert outcome["signal_count"] == 2
    assert outcome["reviewed_count"] == 0
    assert outcome["unreviewed_count"] == 2
    assert outcome["actions_not_sent"] == []
    assert outcome["crm_writes"] == "NO"
    assert outcome["customer_contact_authorized"] is False
    assert outcome["live_adapter_activated"] is False


@pytest.mark.parametrize("decision,expected_stage,expected_label", [
    ("relevant", "DT1062_21:SUCCESS", "DEMO_RELEVANT_NOT_A_SALE"),
    ("irrelevant", "DT1062_21:FAIL", "DEMO_REJECTED"),
    ("needs_research", "DT1062_21:PREPARATION", "DEMO_RESEARCH_REQUIRED"),
])
def test_all_existing_workflow_decisions_map_to_1062(decision, expected_stage, expected_label):
    outcome = build_synthetic_1062_manager_plan(_data(), [_review(decision=decision)], today=NOW)
    assert outcome["reviewed_count"] == 1
    assert outcome["unreviewed_count"] == 1
    action = outcome["actions_not_sent"][0]
    assert action["operation"] == "PREVIEW_ONLY_NOT_EXECUTABLE"
    assert action["expected_current_stage"] == "DT1062_21:CLIENT"
    assert action["expected_category_id"] == 21
    assert action["params_template_without_item_id"]["entityTypeId"] == 1062
    assert "id" not in action["params_template_without_item_id"]
    fields = action["params_template_without_item_id"]["fields"]
    assert fields["stageId"] == expected_stage
    assert fields["UF_CRM_13_SVCIT_REVIEW_DECISION"] == expected_label
    assert action["real_manager_authorization_verified"] is False
    assert outcome["crm_writes"] == "NO"


def test_two_independent_signals_two_manager_decisions():
    second = dict(_review(index=1, decision="irrelevant"), event_id="DEMO-REVIEW-00002")
    outcome = build_synthetic_1062_manager_plan(_data(), [_review(), second], today=NOW)
    assert outcome["signal_count"] == 2
    assert outcome["reviewed_count"] == 2
    assert outcome["unreviewed_count"] == 0
    assert len({x["idempotency_key"] for x in outcome["actions_not_sent"]}) == 2


def test_duplicate_event_and_same_signal_replay_blocked():
    r = _review()
    with pytest.raises(SyntheticPlanRejected, match="REPLAY_OR_CONFLICT"):
        build_synthetic_1062_manager_plan(_data(), [r, dict(r)], today=NOW)
    other = dict(_review(index=1), event_id=r["event_id"])
    with pytest.raises(SyntheticPlanRejected, match="REPLAY_OR_CONFLICT"):
        build_synthetic_1062_manager_plan(_data(), [r, other], today=NOW)


def test_unknown_manager_key_blocked():
    r = dict(_review(), idempotency_key="DEMO-IDEMP-" + "a"*32)
    with pytest.raises(SyntheticPlanRejected, match="NOT_LINKED"):
        build_synthetic_1062_manager_plan(_data(), [r], today=NOW)


@pytest.mark.parametrize("field,value", [
    ("decision", "approve_real_sale"),
    ("reviewer_role", "admin"),
    ("reviewer_label", "REAL-EMPLOYEE-7"),
    ("event_id", "LIVE-REVIEW-1"),
    ("reason", "ok"),
    ("reason", "Send to me@example.org now"),
    ("reason", "https://crm.example.com/contact"),
    ("reviewed_on", "2026-01-01"),
    ("reviewed_on", "2026-11-02"),
    ("reviewed_on", "not-a-date"),
    ("idempotency_key", "not-a-hash"),
])
def test_invalid_manager_reviews_fail_closed(field, value):
    r = dict(_review())
    r[field] = value
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_manager_plan(_data(), [r], today=NOW)


def test_unknown_review_fields_rejected():
    r = dict(_review(), contact_phone="+7 900 000 00 00")
    with pytest.raises(SyntheticPlanRejected, match="SHAPE"):
        build_synthetic_1062_manager_plan(_data(), [r], today=NOW)


def test_review_list_bounded_and_strict():
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_manager_plan(_data(), "wrong", today=NOW)
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_manager_plan(_data(), [{}]*251, today=NOW)


def test_no_network_or_secrets(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    plan = build_synthetic_1062_manager_plan(_data(), [_review()], today=NOW)
    serialized = json.dumps(plan)
    assert "/rest/" not in serialized
    assert "access_token" not in serialized
    assert "webhook" not in serialized
    assert plan["source_connections"] == 0
    assert plan["crm_writes"] == "NO"


def test_offline_report_must_pass_existing_mapping_gates():
    report = _data()
    report["production_activated"] = True
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_manager_plan(report, [], today=NOW)


def test_reason_with_newline_is_rejected():
    r = dict(_review(), reason="Обоснование\nне должно иметь переносы")
    with pytest.raises(SyntheticPlanRejected, match="REASON_UNSAFE"):
        build_synthetic_1062_manager_plan(_data(), [r], today=NOW)
