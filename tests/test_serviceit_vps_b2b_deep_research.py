"""Offline proof for the real B2B post-HOLD evidence-first research path.

No real Bitrix, Brave, RouterAI, browser, contacts or Postgres network calls.
"""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS, hold_analysis_fields
from leadpilot.serviceit_vps_native_fence import NATIVE_NAMESPACES


NOTICE = {
    "source": "B2B-Center", "tender_id": "4673595",
    "source_url": "https://www.b2b-center.ru/market/test/tender-4673595/",
    "subject": "Предоставление IT-специалистов, включая 1С, DevOps и ИБ",
    "organizer": "ООО ПРИМЕР",
    "original_publication_verified": False, "contact_approved": False,
    "qualified_lead": False, "buyer_inn": None, "fns_msp": None,
}


def item():
    data = {
        "id": 105, "entityTypeId": 1062, "categoryId": 21,
        "title": "ТЕНДЕР / проверка · B2B-Center №4673595",
        PREFIX + "SIGNAL_ID": "B2B-CENTER-4673595",
        PREFIX + "SIGNAL_URL": NOTICE["source_url"],
        PREFIX + "SIGNAL_KIND": "tender-opportunity",
        PREFIX + "STOP_REASON": "Не квалифицированный B2B-лид",
        "stageId": "DT1062_21:NEW",
    }
    data.update(hold_analysis_fields(NOTICE, ["1C"]))
    return data


def findings():
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": NOTICE["source_url"],
                   "excerpt": "Закупка 4673595. Требуются IT-специалисты 1С и DevOps."}],
        "checked_at": "2026-11-10T12:00:00+00:00",
        "contacts": [], "model_excerpt": "Закупка 4673595. Предоставление IT-специалистов, включая 1С, DevOps и ИБ.",
        "independent_company_research": None,
    }


def test_public_subject_in_signed_receipt_is_used_not_generic_kanban_title():
    data = item()
    _, title = research.safe_notice(
        NOTICE, 105, "B2B-CENTER-4673595", data)
    assert title == NOTICE["subject"]
    assert title != data["title"]
    assert "B2B_RESEARCH_V3" in NATIVE_NAMESPACES


@pytest.mark.parametrize("field, replacement", [
    ("tender_id", "9999999"),
    ("source_url", "https://malicious.example.invalid/market"),
    ("contact_approved", True),
])
def test_mismatched_or_reclassified_notice_not_used(field, replacement):
    notice = dict(NOTICE, **{field: replacement})
    with pytest.raises(research.B2BResearchBlocked):
        research.safe_notice(notice, 105, "B2B-CENTER-4673595", item())


def test_model_only_when_source_has_at_least_one_read_public_page():
    assert research.model_safe_source_title(NOTICE["subject"], {}) == ""
    assert research.model_safe_source_title(
        NOTICE["subject"], {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []}) == ""
    assert research.model_safe_source_title(NOTICE["subject"], findings()) == NOTICE["subject"]
    mismatch = dict(findings(), model_excerpt="На сайте опубликована закупка 4673595 на сопровождение серверов и 1С")
    assert research.model_safe_source_title(NOTICE["subject"], mismatch) == mismatch["model_excerpt"]
    assert research.model_safe_source_title(NOTICE["subject"], dict(findings(), model_excerpt="Вход в личный кабинет")) == ""


def test_cited_research_not_hypothetical_text_overwrites_only_nine_fields():
    data = item()
    original = deepcopy(data)
    fields = research.draft_fields(data, findings())
    assert set(fields) == AI_FIELDS
    assert fields[PREFIX + "AI_STATUS"] == "PUBLIC_RESEARCH_AI_NOT_RUN"
    assert NOTICE["source_url"] in fields[PREFIX + "AI_FULL_REPORT"]
    assert "4673595" in fields[PREFIX + "AI_FULL_REPORT"]
    assert "GigaChat" not in "\n".join(fields.values())
    assert all(0 < len(v) <= 12000 and
               all(ord(c) <= 0xFFFF for c in v)
               for v in fields.values())
    assert data == original
    assert data[PREFIX + "STOP_REASON"] == "Не квалифицированный B2B-лид"


def test_routerai_report_and_no_obsolete_model_label():
    ai = {
        "origin": "AI_hypothesis", "provider": "routerai",
        "model": "deepseek/chat", "quality_status": "complete",
        "service": "IT-аутсорсинг",
        "possible_need": "Возможен аутсорсинг сотрудников IT-подразделения.",
        "evidence_quote": "IT-специалистов",
        "confidence": 0.75,
    }
    result = research.draft_fields(
        item(), findings(), ai=ai, safe_title=NOTICE["subject"])
    assert result[PREFIX + "AI_STATUS"] == "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert "RouterAI" in result[PREFIX + "AI_FULL_REPORT"]
    assert "GigaChat" not in "\n".join(result.values())
    assert NOTICE["source_url"] in result[PREFIX + "AI_FULL_REPORT"]


def test_brave_exact_id_only_trusts_separately_fetched_official_page(monkeypatch):
    import leadpilot.serviceit_public_evidence_research as evidence
    import leadpilot.serviceit_vps_b2b_deep_research as module
    monkeypatch.setattr(evidence, "_public_dns", lambda host: True)
    # The imported _page uses its own module's _public_dns.
    called = []
    def transport(req):
        called.append((req.method, req.url.host, req.url.path))
        if req.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web": {"results": [
                {"url": "https://www.b2b-center.ru/market/candidate/"}
            ]}})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /",
                                  headers={"content-type": "text/plain"})
        if req.url.path == "/market/candidate/":
            return httpx.Response(200,
                text="<html><body>Закупка 4673595: услуги 1С и DevOps</body></html>",
                headers={"content-type": "text/html"})
        raise AssertionError("Unexpected external endpoint")
    with httpx.Client(transport=httpx.MockTransport(transport),
                      follow_redirects=False) as client:
        result = module.brave_notice_page(
            client, "4673595", frozenset({"b2b-center.ru"}),
            "SYNTHETIC-INDEX-KEY-NONSECRET")
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"
    assert result["pages"][0]["url"].startswith("https://www.b2b-center.ru/")
    assert "4673595" in result["pages"][0]["excerpt"]
    assert not result["contacts"]
    assert len([x for x in called if x[1] == "api.search.brave.com"]) == 1


def test_brave_search_snippet_never_becomes_proof(monkeypatch):
    import leadpilot.serviceit_public_evidence_research as evidence
    monkeypatch.setattr(evidence, "_public_dns", lambda host: True)
    def transport(req):
        if req.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web": {"results": [
                {"url": "https://www.b2b-center.ru/market/old/",
                 "description": "4673595 BIG BUDGET BUYER CONTACT APPROVED"},
                {"url": "https://evil.example.invalid/private/4673595",
                 "title": "Информация о заказчике 4673595"},
            ]}})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /",
                                  headers={"content-type": "text/plain"})
        return httpx.Response(200,
            text="<html><body>Старая закупка. Идентификатор другой.</body></html>",
            headers={"content-type": "text/html"})
    with httpx.Client(transport=httpx.MockTransport(transport),
                      follow_redirects=False) as client:
        assert research.brave_notice_page(
            client, "4673595", frozenset({"b2b-center.ru"}),
            "SYNTHETIC-INDEX-KEY-NONSECRET") is None


def test_research_is_off_by_default_without_any_external_requests():
    assert research.run_once({}, None, None) == {
        "status": "B2B_RESEARCH_DISABLED", "research": 0,
    }


def test_live_one_card_uses_separate_fence_and_exact_nine_readback(monkeypatch):
    from leadpilot import serviceit_vps_worker
    monkeypatch.setattr(serviceit_vps_worker, "live_permitted", lambda env: True)
    monkeypatch.setattr(research, "selected_one", lambda conn:
        (105, "B2B-CENTER-4673595", dict(NOTICE)))
    monkeypatch.setattr(research, "secret_file",
                        lambda env, key, optional=False: "SYNTHETIC-SECRET")
    monkeypatch.setattr(research, "validate_webhook", lambda key: True)
    monkeypatch.setattr(research, "source_findings",
                        lambda item, notice, env, client: findings())
    monkeypatch.setattr(research, "config_from_env",
                        lambda env: SimpleNamespace(provider="routerai"))
    calls = []
    live = item()
    class Rest:
        def __init__(self, *_):
            pass
        def __call__(self, method, args):
            calls.append(method)
            if method == "crm.item.get":
                return {"item": deepcopy(live)}
            if method == "crm.item.update":
                assert set(args["fields"]) == AI_FIELDS
                live.update(args["fields"])
                return {"item": deepcopy(live)}
            raise AssertionError(method)
    monkeypatch.setattr(research, "Rest", Rest)
    def deep_model(client, cfg, findings):
        assert findings["pages"][0]["url"] == NOTICE["source_url"]
        calls.append("routerai.deep_inference")
        return {
            "provider": "RouterAI",
            "overview": "На публичной странице упомянуты услуги ИТ-специалистов",
            "service_fit": "IT-аутсорсинг",
            "likely_need": "Возможно внешнее сопровождение по ИТ-направлению",
            "facts": [{
                "category": "SCOPE", "source_id": 1,
                "finding": "В объявлении перечислены 1С и DevOps",
                "quote": "Закупка 4673595.",
                "url": NOTICE["source_url"],
            }],
            "missing": ["дата и срок подачи"],
            "manager_questions": ["Уточнить действующий срок подачи заявки"],
        }
    monkeypatch.setattr(research, "investigate_routerai", deep_model)
    class Fence:
        def __init__(self, _, namespace):
            assert namespace == "B2B_RESEARCH_V3"
        def reserve(self, card):
            calls.append("pg.reserve")
            return "RESERVED"
        def checkpoint_write(self, card, fields):
            assert set(fields) == AI_FIELDS
            calls.append("pg.write_intent")
        def finalize(self, card, status):
            calls.append("pg." + status.lower())
            return True
        @staticmethod
        def validate_card(card):
            assert card == 105
    monkeypatch.setattr(research, "PostgresNativeFence", Fence)
    data = research.run_once({
        "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
        "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT": "NO",
    }, None, None)
    assert data["status"] == "B2B_RESEARCH_NINE_FIELDS_VERIFIED"
    assert data["routerai_invoked"] is True
    assert calls.index("pg.reserve") < calls.index("routerai.deep_inference")
    assert calls.index("pg.write_intent") < calls.index("crm.item.update")
    assert calls[-1] == "pg.verified"
    assert "crm.item.add" not in calls
    assert "tasks.task.add" not in calls
    assert live[PREFIX + "STOP_REASON"] == "Не квалифицированный B2B-лид"
    assert live["stageId"] == "DT1062_21:NEW"
