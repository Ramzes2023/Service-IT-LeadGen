"""One-call RouterAI V3 robust/diagnostic contract, no real API or CRM."""
import json
from types import SimpleNamespace

import httpx
import pytest

from leadpilot import serviceit_b2b_routerai_deep as deep
from leadpilot import serviceit_vps_b2b_deep_research as runner
from leadpilot.serviceit_vps_native_fence import NATIVE_NAMESPACES


FINDINGS = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{"url": "https://b2b-center.ru/market/sample/",
               "excerpt": "Учебная закупка ИТ-услуг. "
                          "Требуются специалисты 1С, DevOps и системные администраторы."}],
}


def config():
    return SimpleNamespace(
        provider="routerai", key="SYNTHETIC-KEY-DO-NOT-USE",
        model="deepseek/deepseek-v4.1-flash",
        url="https://routerai.ru/api/v1/chat/completions",
    )


def success_object():
    return {
        "overview": "Объявление о сопровождении ИТ",
        "service_fit": "ИТ-аутсорсинг",
        "likely_need": "Возможны услуги ИТ-поддержки, без подтверждения покупки",
        "facts": [{"source_id": 1, "category": "TECHNOLOGY",
                   "quote": "специалисты 1С, DevOps",
                   "finding": "Сведения страницы"}],
        "missing": ["бюджет и условия оплаты"],
        "manager_questions": ["Уточнить объём поддержки?"],
    }


def test_json_object_accepted_one_call_with_safe_structured_evidence():
    called=[]
    def transport(request):
        called.append(request)
        body=json.loads(request.content)
        assert body["max_tokens"]==6200
        assert "JSON" in body["messages"][0]["content"]
        assert "example_shape" in body["messages"][1]["content"]
        return httpx.Response(200,json={"choices":[{
            "finish_reason":"stop",
            "message":{"content":json.dumps(success_object(),ensure_ascii=False)},
        }]})
    with httpx.Client(trust_env=False,transport=httpx.MockTransport(transport)) as client:
        result=deep.investigate_routerai(client,config(),FINDINGS)
    assert len(called)==1
    assert result["provider"]=="RouterAI"
    assert result["facts"][0]["url"] == FINDINGS["pages"][0]["url"]


@pytest.mark.parametrize("status,body,expected", [
    (502,None,"ROUTERAI_HTTP_NOT_OK"),
    (200,{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]},
     "ROUTERAI_OUTPUT_TRUNCATED"),
    (200,{"choices":[{"finish_reason":"stop","message":{"content":""}}]},
     "ROUTERAI_CONTENT_EMPTY_OR_OVERSIZED"),
    (200,{"choices":[{"finish_reason":"stop","message":{"content":"not-json"}}]},
     "ROUTERAI_MODEL_JSON_INVALID"),
])
def test_diagnostic_returns_fixed_no_data_codes(status,body,expected):
    def transport(_):
        return httpx.Response(status,json=body or {"error":"synthetic"})
    with httpx.Client(trust_env=False,transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(deep.DeepEvidenceError,match="^"+expected+"$"):
            deep.investigate_routerai(client,config(),FINDINGS)


def test_v3_only_upgrades_not_yet_ai_verified_previous_versions():
    assert runner.RECEIPT=="B2B_RESEARCH_V4"
    assert {"B2B_RESEARCH_V1","B2B_RESEARCH_V2","B2B_RESEARCH_V3","B2B_RESEARCH_V4"} <= NATIVE_NAMESPACES
    assert "PUBLIC_RESEARCH_SOURCE_CITED_AI_UNVERIFIED" in runner.RESEARCHABLE_STATES
    assert "PUBLIC_RESEARCH_NO_VERIFIED_PUBLIC_TEXT" in runner.RESEARCHABLE_STATES
    assert "PUBLIC_RESEARCH_ROUTERAI_CITED" not in runner.RESEARCHABLE_STATES


def test_fenced_json_markdown_code_block_is_accepted():
    fence=chr(96)*3
    raw=fence+"json\n"+json.dumps(success_object(),ensure_ascii=False)+"\n"+fence
    def transport(req):
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop",
                                                   "message":{"content":raw}}]})
    with httpx.Client(trust_env=False,transport=httpx.MockTransport(transport)) as client:
        result=deep.investigate_routerai(client,config(),FINDINGS)
    assert result["facts"]
