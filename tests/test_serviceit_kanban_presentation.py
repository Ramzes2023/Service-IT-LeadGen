"""Generic Kanban badge for every source signal, not only DEMO #3."""
from datetime import date

import pytest

from leadpilot.serviceit_ai_lead_engine import assess_lead, plan_serviceit_ai_signals
from leadpilot.serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD,
    ScorePresentationRejected, score_color, score_label,
    fields_from_assessment, decorate_assessment,
)
from leadpilot.serviceit_kanban_score_badge import score_badge as earlier_demo_badge


COMPANY = {
    "inn": "7700000009", "name": "DEMO COMPANY-001", "region": "Москва",
    "industry": "торговля", "phone": "", "email": "",
}
SIGNAL_A = {
    "signal_type": "new_store", "signal_date": "2026-10-31",
    "source_url": "https://example.invalid/serviceit-demo/request/001",
    "evidence": "Демонстрационная закупка на 1С и оборудование для нового магазина",
}
SIGNAL_B = {
    "signal_type": "crm_need", "signal_date": "2026-10-30",
    "source_url": "https://example.invalid/serviceit-demo/request/002",
    "evidence": "Демонстрационный запрос на внедрение CRM и автоматизацию продаж",
}


@pytest.mark.parametrize("score,color", [
    (0, "🔴"), (35, "🔴"), (59, "🔴"),
    (60, "🟡"), (65, "🟡"), (79, "🟡"),
    (80, "🟢"), (90, "🟢"), (100, "🟢"),
])
def test_color_ranges_and_compatibility_with_prior_demonstration(score,color):
    assert score_color(score) == color
    assert score_label(score).startswith(f"{color} {score}/100")
    assert score_label(score) == earlier_demo_badge(score)


@pytest.mark.parametrize("score",[-1, 101, "90", 90.0, True, None])
def test_invalid_scores_do_not_produce_colored_badges(score):
    with pytest.raises(ScorePresentationRejected):
        score_label(score)


def test_automatically_rendered_for_multiple_source_assessments():
    today=date(2026,11,1)
    r1=assess_lead(COMPANY,[SIGNAL_A],checks={},today=today)
    r2=assess_lead(COMPANY,[SIGNAL_B],checks={},today=today)
    assert r1["kanban_score_badge"] == score_label(r1["lead_score"])
    assert r2["kanban_score_badge"] == score_label(r2["lead_score"])
    plan=plan_serviceit_ai_signals([r1,r2])
    assert plan["candidates"]==2
    for original,item in zip((r1,r2),plan["items"]):
        assert item["kanban_score_badge"] == score_label(original["lead_score"])
        assert item["kanban_score_field_code"] == BADGE_FIELD
        assert item["score_badge_write_performed"] is False
        assert item["crm_operation"]=="NOT_SENT_REQUIRES_SEPARATE_REVIEW"
        assert "🟢" in item["kanban_score_badge"]
    assert plan["automatic_production_worker"] is False
    assert plan["crm_writes"] == "NO"


def test_kanban_badge_high_score_still_unverified_not_outreach_authorized():
    result=assess_lead(COMPANY,[SIGNAL_A],checks={},today=date(2026,11,1))
    assert result["lead_score"]==90
    assert result["status"]=="HOLD_UNVERIFIED"
    assert result["kanban_score_badge"]=="🟢 90/100 — высокий приоритет проверки"
    assert result["crm_handoff_eligible"] is False
    assert result["outreach_authorized_by_this_module"] is False
    assert result["bitrix_write_performed"] is False
    fields=fields_from_assessment(result)
    assert fields=={
        SCORE_FIELD:90,
        VERSION_FIELD:"mvp-2026-10-v1",
        BADGE_FIELD:"🟢 90/100 — высокий приоритет проверки",
    }


def test_gigachat_hypothesis_cannot_change_color_or_score():
    today=date(2026,11,1)
    plain=assess_lead(COMPANY,[SIGNAL_A],checks={},today=today)
    with_ai=assess_lead(
        COMPANY,[SIGNAL_A],checks={},today=today,
        ai_hypothesis={"origin":"AI_hypothesis","quality_status":"complete",
                       "evidence_quote":"Демонстрационная закупка",
                       "possible_need":"Возможна автоматизация торговли"},
    )
    assert with_ai["ai_status"]=="HYPOTHESIS_NOT_FACT"
    assert with_ai["lead_score"]==plain["lead_score"]
    assert with_ai["kanban_score_badge"]==plain["kanban_score_badge"]


@pytest.mark.parametrize("override", [
    {"outreach_authorized_by_this_module":True},
    {"human_decision_required":False},
    {"score_version":""},
    {"lead_score":"90"},
    {"mode":"LIVE_APPROVED"},
])
def test_not_qualified_or_malformed_assessment_cannot_claim_crm_fields(override):
    source=assess_lead(COMPANY,[SIGNAL_A],checks={},today=date(2026,11,1))
    changed={**source,**override}
    with pytest.raises(ScorePresentationRejected):
        fields_from_assessment(changed)


def test_repeated_decoration_is_safe_but_conflict_rejected():
    source=assess_lead(COMPANY,[SIGNAL_A],checks={},today=date(2026,11,1))
    assert decorate_assessment(source)==source
    with pytest.raises(ScorePresentationRejected,match="BADGE_CONFLICT"):
        decorate_assessment({**source,"kanban_score_badge":"🔴 90/100"})


def test_preserves_blocked_state_and_human_review_required():
    checks={"company_registry":"UNKNOWN","source":"UNKNOWN",
            "existing_customer":"BLOCKED","crm_dedup":"UNKNOWN",
            "stop_list":"UNKNOWN","legal_basis":"UNKNOWN",
            "contact":"UNKNOWN"}
    out=assess_lead(COMPANY,[SIGNAL_A],checks=checks,today=date(2026,11,1))
    assert out["status"]=="HOLD_BLOCKED"
    assert out["kanban_score_badge"]==score_label(out["lead_score"])
    assert out["human_decision_required"] is True
    assert out["outreach_authorized_by_this_module"] is False


def test_all_101_integer_scores_have_exactly_one_band_and_match_existing():
    for n in range(101):
        assert score_label(n)==earlier_demo_badge(n)
