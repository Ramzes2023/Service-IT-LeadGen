"""DeepSeek V4.1 Flash is opt-in and cannot bypass evidence/CRM controls."""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.ai import AIProvider, config_from_env
from leadpilot.serviceit_b2b_hold_ai_fields import hold_analysis_fields, PREFIX
from leadpilot.serviceit_preliminary_gigachat import model_hypothesis_fields


def config(**kwargs):
    return {"AI_ENABLE": "1", "AI_PROVIDER":"routerai",
            "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
            "ROUTERAI_API_KEY":"SYNTHETIC-TEST-KEY-ONLY", **kwargs}


def test_official_deepseek_identity_not_derived_from_user_url():
    cfg=config_from_env(config(AI_MODEL="any-unapproved-model",
          AI_API_URL="https://example.org/redirect"))
    assert cfg.provider == "routerai"
    assert cfg.url == "https://routerai.ru/api/v1/chat/completions"
    assert cfg.model == "deepseek/deepseek-v4.1-flash"
    assert cfg.key == "SYNTHETIC-TEST-KEY-ONLY"


@pytest.mark.parametrize("overrides",[
    {"SERVICEIT_ROUTERAI_PRIVACY_APPROVED":""},
    {"ROUTERAI_API_KEY":""},
    {"AI_ENABLE":"0"},
])
def test_no_approval_no_key_no_model_run(overrides):
    with pytest.raises(RuntimeError):
        config_from_env(config(**overrides))


def test_deepseek_http_request_has_json_and_unchanged_evidence():
    source="Нужен подрядчик для сопровождения 1С"
    inspected=[]
    def transport(req):
        assert req.url.host=="routerai.ru"
        assert req.url.path=="/api/v1/chat/completions"
        assert req.headers["Authorization"]=="Bearer SYNTHETIC-TEST-KEY-ONLY"
        payload=json.loads(req.content)
        inspected.append(payload)
        assert payload["model"] == "deepseek/deepseek-v4.1-flash"
        assert payload["response_format"] == {"type":"json_object"}
        assert payload["max_tokens"] == 800
        assert "thinking" not in payload
        assert payload["messages"][1]["content"] == source
        body={"service":"1С", "possible_need":"Возможна поддержка 1С",
              "confidence":.83, "evidence_quote":"сопровождения 1С"}
        return httpx.Response(200,json={"choices":[{"message":{
            "content":json.dumps(body,ensure_ascii=False)}}]})
    cfg=config_from_env(config())
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with AIProvider(cfg,client) as provider:
            ai=provider.classify(source)
    assert ai["provider"]=="deepseek" and ai["model"]=="deepseek/deepseek-v4.1-flash"
    assert ai["quality_status"]=="complete"
    assert len(inspected)==1


def test_unverified_fake_quote_cannot_qualify():
    source="Закупка сопровождения 1С"
    def transport(req):
        body={"service":"1С", "possible_need":"Контракт точно подписан",
              "confidence":.9, "evidence_quote":"несуществующий договор"}
        return httpx.Response(200,json={"choices":[{"message":{
            "content":json.dumps(body,ensure_ascii=False)}}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        ai=AIProvider(config_from_env(config()),client).classify(source)
    assert ai["quality_status"]=="needs_review"
    assert ai["evidence_quote"]==""


def test_preliminary_report_uses_actual_provider_without_false_sale():
    notice={"source":"B2B-Center","tender_id":"4661617",
            "subject":"Сопровождение 1С по договору",
            "original_publication_verified":False,"qualified_lead":False,
            "contact_approved":False,"buyer_inn":None,"fns_msp":None}
    base=hold_analysis_fields(notice,["1C"])
    ai={"provider":"routerai","model":"deepseek/deepseek-v4.1-flash",
        "origin":"AI_hypothesis","quality_status":"complete",
        "service":"1С","possible_need":"Возможна поддержка 1С",
        "confidence":.8,"evidence_quote":"Сопровождение 1С"}
    fields=model_hypothesis_fields(base,"Сопровождение 1С по договору",ai)
    assert set(fields)==set(base)
    assert "RouterAI (DeepSeek)" in fields[PREFIX+"AI_SUMMARY"]
    assert "НЕ подтверждённый лид" in fields[PREFIX+"AI_SUMMARY"]
    assert "ГИПОТЕЗА RouterAI (DeepSeek)" in fields[PREFIX+"AI_FULL_REPORT"]


def test_workflow_switch_preserves_existing_gigachat_fallback():
    for path in (
        ".github/workflows/serviceit-bitrix-new-card-ai.yml",
        ".github/workflows/serviceit-bitrix-all-new-batch-ai.yml",
    ):
        text=Path(path).read_text("utf-8")
        assert "SERVICEIT_AI_PROVIDER" in text
        assert "SERVICEIT_ROUTERAI_PRIVACY_APPROVED" in text
        assert "secrets.SERVICEIT_ROUTERAI_API_KEY" in text
        assert "|| 'gigachat'" in text
        assert "serviceit_gigachat_ca_setup" in text
