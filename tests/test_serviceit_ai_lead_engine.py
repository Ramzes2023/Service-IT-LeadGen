"""General qualification and CRM handoff gates for ServiceIT AI Lead."""
from datetime import date
import pytest

from leadpilot.serviceit_ai_lead_engine import (
    assess_lead, plan_serviceit_ai_signals, QualificationBlocked,
)

TODAY=date(2026,11,1)
COMPANY={"inn":"7700000009","name":"DEMO COMPANY-001","region":"Москва",
         "industry":"торговля","phone":"","email":""}
SIGNAL={"signal_type":"new_store", "signal_date":"2026-10-31",
        "source_url":"https://example.invalid/serviceit-demo/request/001",
        "evidence":"УЧЕБНЫЙ СИГНАЛ: компания открывает новую торговую точку и изучает 1С"}
KEYS=("source","existing_customer","crm_dedup","stop_list","legal_basis","contact")


def report(*,checks=None,signals=None, ai=None,demo=False):
    return assess_lead(COMPANY,signals or [SIGNAL],checks=checks or {},
                       today=TODAY,ai_hypothesis=ai,demo=demo)


def test_unknown_company_wide_gates_never_become_verified_even_high_score():
    out=report()
    assert out["lead_score"]==90
    assert out["status"]=="HOLD_UNVERIFIED"
    assert out["crm_handoff_eligible"] is False
    assert out["outreach_authorized_by_this_module"] is False
    assert len(out["unverified_checks"])==6
    assert out["score_version"]=="mvp-2026-10-v1"


def test_fully_independently_verified_prospect_still_requires_human_manager():
    out=report(checks={name:"VERIFIED" for name in KEYS})
    assert out["status"]=="MANAGER_REVIEW_REQUIRED"
    assert out["crm_handoff_eligible"] is True
    assert out["human_decision_required"] is True
    assert out["outreach_authorized_by_this_module"] is False
    assert out["bitrix_write_performed"] is False


@pytest.mark.parametrize("one",KEYS)
def test_every_single_blocked_legal_customer_or_dedup_gate_stops(one):
    c={name:"VERIFIED" for name in KEYS}
    c[one]="BLOCKED"
    out=report(checks=c)
    assert out["status"]=="HOLD_BLOCKED"
    assert one in out["blocking_checks"]
    assert not out["crm_handoff_eligible"]


def test_low_confidence_ai_does_not_change_score():
    basic=report()
    bad={"origin":"AI_hypothesis","quality_status":"needs_review",
         "possible_need":"Нужна 1С"}
    ai=report(ai=bad)
    assert ai["lead_score"]==basic["lead_score"]
    assert ai["ai_status"]=="REQUIRES_HUMAN_REVIEW"
    assert ai["ai_possible_need"]==""


def test_ai_is_explictly_unverified_hypothesis_even_after_model_quality_pass():
    ai={"origin":"AI_hypothesis","quality_status":"complete",
        "evidence_quote":"УЧЕБНЫЙ СИГНАЛ",
        "possible_need":"Возможно потребуется обновление 1С"}
    out=report(ai=ai)
    assert out["ai_status"]=="HYPOTHESIS_NOT_FACT"
    assert out["ai_possible_need"].startswith("Возможно")
    assert not out["crm_handoff_eligible"]


def test_multiple_signals_for_same_company_produce_one_company_and_two_sources():
    s2={"signal_type":"1c_vacancy","signal_date":"2026-10-30",
        "source_url":"https://example.invalid/serviceit-demo/request/002",
        "evidence":"Вакансия 1С-консультанта зафиксирована в демонстрационных данных"}
    out=report(signals=[SIGNAL,s2])
    assert out["source_count"]==2
    planned=plan_serviceit_ai_signals([out])
    assert planned["candidates"]==1
    assert planned["items"][0]["crm_entity_type_id"]==1062
    assert planned["items"][0]["target_process"]=="Сервис ИТ — AI-сигналы"
    assert planned["crm_writes"]=="NO"


def test_duplicate_signals_rejected_even_with_two_different_indices():
    with pytest.raises(QualificationBlocked,match="SIGNAL_DUPLICATE_IN_BATCH"):
        report(signals=[SIGNAL,SIGNAL])


def test_stale_source_holds_even_high_historical_score():
    stale=dict(SIGNAL,signal_date="2026-09-30")
    out=report(signals=[stale],checks={name:"VERIFIED" for name in KEYS})
    assert "stale_signal" in out["blocking_checks"]
    assert out["status"]=="HOLD_BLOCKED"


def test_demo_does_not_become_live_lead_even_with_all_gates_pass():
    out=report(checks={name:"VERIFIED" for name in KEYS},demo=True)
    assert out["status"]=="DEMO_REVIEW_ONLY"
    assert out["crm_handoff_eligible"] is False
    planned=plan_serviceit_ai_signals([out])
    assert planned["items"][0]["demo_only"] is True
    assert planned["automatic_production_worker"] is False


def test_batch_repeated_item_does_not_create_second_crm_intent():
    out=report(demo=True)
    with pytest.raises(QualificationBlocked,match="REPEATED_SIGNAL_IN_BATCH"):
        plan_serviceit_ai_signals([out,out])


@pytest.mark.parametrize("value",["YES",True,None,{"oops":"value"}])
def test_untrusted_check_states_rejected(value):
    checks={"crm_dedup":value}
    with pytest.raises(QualificationBlocked,match="GATE_STATE_INVALID"):
        report(checks=checks)


def test_optional_registry_does_not_block_manager_review():
    checks={name:"VERIFIED" for name in KEYS}
    checks["company_registry"]="UNKNOWN"
    assert report(checks=checks)["crm_handoff_eligible"] is True
