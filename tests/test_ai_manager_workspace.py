"""Offline acceptance of human approval and fact-vs-AI boundary."""
from copy import deepcopy
from datetime import date

import pytest

from leadpilot.ai_manager_workspace import UnsafeBrief, build_manager_workspace

TODAY=date(2026,11,1)


def fixture():
    return {
        "company":"DEMO COMPANY-001","inn":"7700000009","region":"Москва",
        "priority":"high","lead_score":86,"score_version":"demo-v1",
        "rule_based_service":"1С + ККТ", "crm_writes":"NO","crm_ready":False,
        "outreach":"NOT_AUTHORIZED_BY_THIS_BRIEF","outbound_authorized":False,
        "purchasing_intent":"UNVERIFIED","business_size_verification":"NOT_VERIFIED_BY_LEGACY_SCORE",
        "suppressed_channels":[],"holds":["MANAGER_APPROVAL_PENDING","CRM_DUPLICATE_PENDING"],
        "source":{"name":"synthetic-fixture","url":"https://example.invalid/serviceit-demo/request/001",
                  "signal_date":"2026-10-31", "evidence":"Синтетический тестовый запрос 1С", "signal_type":"procurement"},
        "ai_hypothesis":None,
    }


def test_no_ai_fallback_is_human_only():
    out=build_manager_workspace(fixture(),checked_on=TODAY)
    assert out["score"]=={"value":86,"version":"demo-v1", "origin":"DETERMINISTIC_RULES_NOT_AI", "conversion_probability":"NOT_ESTIMATED"}
    assert out["ai"] is None and out["manager_approval"]=="NOT_GRANTED"
    assert out["crm_writes"]=="NO" and out["customer_contact"]=="NO"
    assert out["after_call_template"]["actual_interest"]=="NOT_RECORDED"


def test_ai_hypothesis_quoted_not_approved():
    f=fixture()
    f["ai_hypothesis"]={"origin":"AI_hypothesis_not_verified_need","possible_need":"Может быть поддержка 1С",
                        "evidence_quote":"тестовый запрос 1С", "service":"1С", "confidence":.71}
    out=build_manager_workspace(f,checked_on=TODAY)
    assert out["ai"]["origin"]=="AI_HYPOTHESIS_NOT_FACT"
    assert out["ai"]["confirmed_purchase_intent"] is False
    assert out["score"]["value"]==86


@pytest.mark.parametrize("change",[
    {"crm_ready":True},{"crm_writes":"YES"},{"outbound_authorized":True},
    {"holds":[]},{"suppressed_channels":["all"]},
    {"purchasing_intent":"CONFIRMED"},{"business_size_verification":"VERIFIED"},
    {"priority":"low"},{"lead_score":86.5},{"inn":"not-an-inn"},
])
def test_policy_conflicts_block(change):
    f=fixture();f.update(change)
    with pytest.raises(UnsafeBrief):build_manager_workspace(f,checked_on=TODAY)


def test_wrong_ai_quote_dropped_not_used():
    f=fixture();f["ai_hypothesis"]={"origin":"AI_hypothesis_not_verified_need","possible_need":"Предполагается 1С",
        "evidence_quote":"нет такой цитаты","service":"1С","confidence":.98}
    o=build_manager_workspace(f,checked_on=TODAY)
    assert o["ai"] is None and o["score"]["value"]==86


def test_future_or_old_source_blocked():
    for dt in ("2027-01-01","2026-01-01"):
        f=fixture();f["source"]["signal_date"]=dt
        with pytest.raises(UnsafeBrief):build_manager_workspace(f,checked_on=TODAY)


def test_no_mutation_of_input():
    f=fixture();original=deepcopy(f)
    build_manager_workspace(f,checked_on=TODAY)
    assert f==original


def test_https_only_source():
    f=fixture();f["source"]["url"]="http://example.invalid/demo"
    with pytest.raises(UnsafeBrief):build_manager_workspace(f,checked_on=TODAY)
