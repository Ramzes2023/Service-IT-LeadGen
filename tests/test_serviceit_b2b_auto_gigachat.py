"""Opted-in genuine GigaChat hypothesis for B2B-Center is still HOLD/no Score."""
import json

import pytest

from leadpilot import b2b_github_to_bitrix as bridge
from leadpilot.serviceit_b2b_hold_ai_fields import hold_analysis_fields,HoldAnalysisRejected
from test_b2b_github_to_bitrix import fixture_event, Endpoints, simulate


def fake_hypothesis():
    return {
        "provider":"gigachat",
        "model":"GigaChat-2",
        "origin":"AI_hypothesis",
        "quality_status":"complete",
        "confidence":0.82,
        "evidence_quote":"Поддержка 1С",
        "possible_need":"Возможно требуется поддержка 1С",
        "service":"1С",
    }


def test_opted_in_model_is_used_once_before_single_create(monkeypatch):
    calls=[]
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED","YES")
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED","YES")
    monkeypatch.setenv("AI_ENABLE","1")
    monkeypatch.setenv("AI_PROVIDER","gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS","SYNTHETIC_CREDENTIAL_NO_NETWORK")
    from leadpilot import ai
    def fake_classifier(text):
        calls.append(text)
        return fake_hypothesis()
    monkeypatch.setattr(ai,"classify_cockpit_signal",fake_classifier)
    target=Endpoints()
    result=simulate(monkeypatch,target)
    assert result["status"]=="ONE_TENDER_HOLD_VERIFIED"
    assert calls==["Поддержка 1С:ЗУП"]
    assert target.calls.count("crm.item.add")==1
    assert target.calls.count("crm.item.update")==0
    assert target.add_fields["UF_CRM_13_SVCIT_AI_STATUS"]=="HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert "AI-провайдер: внешняя модель / GigaChat-2" in target.add_fields["UF_CRM_13_SVCIT_AI_FULL_REPORT"]
    assert "Lead Score: НЕ РАССЧИТАН" in target.add_fields["UF_CRM_13_SVCIT_AI_FULL_REPORT"]
    assert "UF_CRM_13_SVCIT_LEAD_SCORE" not in target.add_fields
    assert "UF_CRM_13_SVCIT_AI_SCORE_BADGE" not in target.add_fields
    assert "Поддержка 1С" in target.add_fields["UF_CRM_13_SVCIT_AI_HYPOTHESIS"]


def test_without_opt_in_provider_not_called(monkeypatch):
    monkeypatch.delenv("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED",raising=False)
    monkeypatch.delenv("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED",raising=False)
    from leadpilot import ai
    monkeypatch.setattr(ai,"classify_cockpit_signal",lambda x: pytest.fail("GigaChat must not run"))
    target=Endpoints()
    result=simulate(monkeypatch,target)
    assert result["status"]=="ONE_TENDER_HOLD_VERIFIED"
    assert target.add_fields["UF_CRM_13_SVCIT_AI_STATUS"]=="NOT_RUN_SOURCE_UNVERIFIED"


def test_privacy_approval_missing_blocks_both_gigachat_and_crm_write(monkeypatch):
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED","YES")
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED","NO")
    monkeypatch.setenv("AI_ENABLE","1")
    monkeypatch.setenv("AI_PROVIDER","gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS","SYNTHETIC_CREDENTIAL_NO_NETWORK")
    target=Endpoints()
    with pytest.raises(bridge.BridgeBlocked,match="B2B_GIGACHAT_PRIVACY_OR_CREDENTIALS_NOT_APPROVED"):
        simulate(monkeypatch,target)
    assert "crm.item.add" not in target.calls
    assert "GH.POST" not in target.calls


def test_unusable_model_output_safe_fallback_creates_hold(monkeypatch):
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED","YES")
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED","YES")
    monkeypatch.setenv("AI_ENABLE","1")
    monkeypatch.setenv("AI_PROVIDER","gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS","SYNTHETIC_CREDENTIAL_NO_NETWORK")
    from leadpilot import ai
    monkeypatch.setattr(ai,"classify_cockpit_signal",lambda x:{
        **fake_hypothesis(),"quality_status":"needs_review"
    })
    target=Endpoints()
    result=simulate(monkeypatch,target)
    assert result["status"]=="ONE_TENDER_HOLD_VERIFIED"
    assert target.add_fields["UF_CRM_13_SVCIT_AI_STATUS"]=="NOT_RUN_SOURCE_UNVERIFIED"


def test_hallucinated_model_quote_never_accepted():
    notice,services=bridge.notice_from_event(fixture_event())
    data={**fake_hypothesis(),"evidence_quote":"ЭТОГО НЕТ В ИСХОДНОМ ТЕКСТЕ"}
    with pytest.raises(HoldAnalysisRejected,match="UNVERIFIED_MODEL_OUTPUT"):
        hold_analysis_fields(notice,services,ai=data)


def test_repeated_card_dedup_does_not_invoke_paid_model(monkeypatch):
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED","YES")
    monkeypatch.setenv("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED","YES")
    monkeypatch.setenv("AI_ENABLE","1")
    monkeypatch.setenv("AI_PROVIDER","gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS","SYNTHETIC_CREDENTIAL_NO_NETWORK")
    from leadpilot import ai
    monkeypatch.setattr(ai,"classify_cockpit_signal",lambda text:pytest.fail("dup must skip AI"))
    target=Endpoints(existing=True)
    result=simulate(monkeypatch,target)
    assert result["status"]=="ALREADY_EXISTS_IN_CRM"
