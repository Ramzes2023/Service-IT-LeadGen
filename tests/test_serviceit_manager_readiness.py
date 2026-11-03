"""Manager handoff from spec: real score after nine independent checks only."""
from copy import deepcopy
from datetime import date

import pytest

from leadpilot.serviceit_ai_lead_engine import assess_lead
from leadpilot.serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, score_label,
)
from leadpilot.serviceit_manager_readiness import (
    MANAGER_NEXT, REVIEW_DECISION, STOP_REASON, REGION, COMPANY_INN,
    COMPANY_NAME, SIGNAL_URL, SIGNAL_ID, AI_STATUS, REQUIRED_GATES,
    MANAGER_STAGE, MANAGER_NEXT, manager_readiness,
    preliminary_manager_packet, ManagerReadinessBlocked,
)
from test_b2b_github_to_bitrix import fixture_event
from leadpilot.b2b_github_to_bitrix import notice_from_event, hold_payload


def verified():
    company = {
        "inn": "7700000009", "name": "Тестовая компания",
        "region": "Москва", "industry": "торговля",
        "phone": "", "email": "",
    }
    signal = {
        "signal_type": "new_store", "signal_date": date.today().isoformat(),
        "source_url": "https://example.invalid/audited-source/demo001",
        "evidence": "Независимо проверено открытие новой торговой точки и закупка 1С",
    }
    checks = {k: "VERIFIED" for k in REQUIRED_GATES}
    assessment = assess_lead(company, [signal], checks=checks, today=date.today())
    assert assessment["crm_handoff_eligible"] and assessment["lead_score"] >= 60
    card = {
        "id": 101, "entityTypeId": 1062, "categoryId": 21,
        "stageId": "DT1062_21:NEW",
        COMPANY_INN: company["inn"],
        COMPANY_NAME: company["name"], REGION: company["region"],
        SIGNAL_URL: signal["source_url"],
        SIGNAL_ID: "AUDITED-SOURCE-101",
        AI_STATUS: "HYPOTHESIS_UNVERIFIED",
        SCORE_FIELD: assessment["lead_score"],
        VERSION_FIELD: assessment["score_version"],
        BADGE_FIELD: score_label(assessment["lead_score"]),
        REVIEW_DECISION: "Ожидает проверки менеджером",
        STOP_REASON: "",
    }
    approval = {
        "checks": {
            k: {"state": "VERIFIED", "evidence_ref": "audit/trace-" + k}
            for k in REQUIRED_GATES
        },
        "company": company, "signals": [signal],
        "score_calculated_on": date.today().isoformat(),
        "manager_route_authorized": True,
        "automated_outreach_allowed": False,
        "source_review_authorized": True,
        "global_dedup_verified": True,
        "stop_list_verified": True,
        "target_role": "Руководитель",
    }
    return card, assessment, approval


def test_new_vendor_hold_manager_packet_no_contact_or_score():
    notice, services = notice_from_event(fixture_event())
    packet = preliminary_manager_packet(notice, services)
    assert packet["status"] == "HOLD_SOURCE_RESEARCH"
    assert packet["manager_section"]["lead_score"] is None
    assert packet["manager_section"]["contact_allowed"] is False
    assert "не контактировать" in packet["fields_on_create"][MANAGER_NEXT]
    fields = hold_payload(notice, services)["fields"]
    assert fields[MANAGER_NEXT] == packet["fields_on_create"][MANAGER_NEXT]
    assert SCORE_FIELD not in fields
    assert BADGE_FIELD not in fields
    assert fields["stageId"] == "DT1062_21:NEW"


def test_verified_source_deterministic_score_to_manager_review_plan():
    card, assess, approved = verified()
    result = manager_readiness(card, 101, assess, approved)
    assert result["status"] == "READY_FOR_MANAGER_REVIEW"
    assert result["target_stage"] == MANAGER_STAGE
    assert result["lead_score"] == assess["lead_score"]
    assert result["score_version"] == assess["score_version"]
    assert "следующее" not in result["manager_card"]["why_now"].lower()
    assert "Руководитель" == result["manager_card"]["target_role"]
    assert result["priority"] in ("HIGH", "MEDIUM")
    assert result["manager_card"]["can_contact_without_human_approval"] is False
    assert result["proposed_fields"]["stageId"] == MANAGER_STAGE
    assert set(result["proposed_fields"]) == {
        "stageId", MANAGER_NEXT, REVIEW_DECISION,
    }
    assert result["crm_writes"] == result["outbound"] == 0
    assert result["creates_task"] is False


@pytest.mark.parametrize("gate", REQUIRED_GATES)
def test_each_single_missing_independent_verification_blocks_manager_stage(gate):
    card, assess, approved = verified()
    approved["checks"][gate] = {"state": "UNKNOWN", "evidence_ref": ""}
    with pytest.raises(ManagerReadinessBlocked, match="UNVERIFIED"):
        manager_readiness(card, 101, assess, approved)


@pytest.mark.parametrize("override", [
    ("manager_route_authorized", False),
    ("automated_outreach_allowed", True),
    ("source_review_authorized", False),
    ("global_dedup_verified", False),
    ("stop_list_verified", False),
])
def test_manager_and_global_corporate_controls_all_mandatory(override):
    card, assess, approved = verified()
    approved[override[0]] = override[1]
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_APPROVAL_MISSING"):
        manager_readiness(card, 101, assess, approved)


@pytest.mark.parametrize("name,value", [
    ("stageId", "DT1062_21:CLIENT"),
    (STOP_REASON, "Отказ клиента"),
    (REVIEW_DECISION, "Нерелевантен — не контактировать"),
])
def test_manager_edits_stage_or_stop_are_not_overwritten(name, value):
    card, assess, approved = verified()
    card[name] = value
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_EDIT_OR_STOP_CONFLICT"):
        manager_readiness(card, 101, assess, approved)


@pytest.mark.parametrize("name,value", [
    (COMPANY_INN, "7711111111"),
    (COMPANY_NAME, "Другая компания"),
    (REGION, "Ингушетия"),
    (SIGNAL_URL, "https://example.invalid/some-other-source"),
])
def test_source_company_fingerprint_conflicts_never_route_manager(name, value):
    card, assess, approved = verified()
    card[name] = value
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_SOURCE_OR_COMPANY_MISMATCH"):
        manager_readiness(card, 101, assess, approved)


@pytest.mark.parametrize("name,value", [
    (SCORE_FIELD, 1),
    (VERSION_FIELD, "model-confidence-0.9"),
    (BADGE_FIELD, "🟢 90/100 — подделка"),
    (AI_STATUS, "NOT_RUN_SOURCE_UNVERIFIED"),
])
def test_no_fake_score_version_or_unverified_model_status(name, value):
    card, assess, approved = verified()
    card[name] = value
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_VERIFIED_SCORE_READBACK_REQUIRED"):
        manager_readiness(card, 101, assess, approved)


def test_ai_cannot_supply_score_though_assessment_parameter():
    card, assess, approved = verified()
    assess["lead_score"] = 100 if assess["lead_score"] != 100 else 60
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_DETERMINISTIC_SCORE_MISMATCH"):
        manager_readiness(card, 101, assess, approved)


def test_historical_3_7_and_demo_not_manager_eligible():
    card, assess, approved = verified()
    for item_id in (3, 7):
        card["id"] = item_id
        with pytest.raises(ManagerReadinessBlocked, match="HISTORICAL_OR_INVALID_ID"):
            manager_readiness(card, item_id, assess, approved)
    card["id"] = 101
    card[SIGNAL_ID] = "DEMO-ITREQ-001"
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_SIGNAL_NOT_VERIFIED"):
        manager_readiness(card, 101, assess, approved)


def test_approval_with_stale_scoring_date_rejected():
    from datetime import timedelta
    card, assess, approved = verified()
    approved["score_calculated_on"] = (date.today() - timedelta(days=6)).isoformat()
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_SCORING_TOO_OLD"):
        manager_readiness(card, 101, assess, approved)


def test_missing_source_proof_not_self_healed_by_ai():
    card, assess, approved = verified()
    approved["signals"][0]["evidence"] = "Содержимое не совпадает с оценкой"
    with pytest.raises(ManagerReadinessBlocked, match="MANAGER_DETERMINISTIC_SCORE_MISMATCH"):
        manager_readiness(card, 101, assess, approved)


def test_existing_hold_status_refuses_false_promotion_even_with_high_score():
    card, assess, approved = verified()
    card[AI_STATUS] = "NOT_RUN_SOURCE_UNVERIFIED"
    card[STOP_REASON] = "Не квалифицированный B2B-лид"
    with pytest.raises(ManagerReadinessBlocked):
        manager_readiness(card, 101, assess, approved)
