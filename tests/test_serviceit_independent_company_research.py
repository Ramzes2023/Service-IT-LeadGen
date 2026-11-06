"""No-network proof of independent company research and exact evidence origins."""
from datetime import datetime, timezone
import httpx
import pytest

from leadpilot import serviceit_independent_company_research as deep
from leadpilot import serviceit_public_evidence_research as public
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

KEY = "synthetic-brave-private-token-not-a-real-secret"
NAME = "ООО Тестовые Системы"
URL = "https://hh.ru/vacancy/123456"


def item(company=None):
    value = {
        PREFIX + "SIGNAL_KIND": "it_vacancy",
        PREFIX + "SIGNAL_URL": URL,
    }
    if company:
        value[PREFIX + "COMPANY_NAME"] = company
    return value


def source_html(company=NAME):
    return ("""<html><script type="application/ld+json">"""
            + '{"@type":"JobPosting","hiringOrganization":{"name":"'
            + company + '"}}'
            + """</script><body>Вакансия системного администратора, требуется в штат.
            <a href="mailto:employer@hh.ru">Написать</a></body></html>""")


def index_response(urls):
    return {"web": {"type": "search", "results": [
        {"url": url, "title": NAME + " открытие магазина",
         "description": "Опубликована информация о " + NAME}
        for url in urls
    ]}}


@pytest.fixture
def mocked_web(monkeypatch):
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    calls = []
    def respond(request):
        u = str(request.url)
        calls.append(u)
        if request.url.host == "api.search.brave.com":
            assert request.url.path == "/res/v1/web/search"
            assert request.headers["X-Subscription-Token"] == KEY
            assert NAME.split()[1] in request.url.params["q"]
            assert "CRM" not in request.url.params["q"]
            return httpx.Response(200, json=index_response([
                "https://news-example.ru/company-update",
                "https://test-systems.ru/contacts",
                "https://unrelated.ru/contacts",
                "http://127.0.0.1/private",
            ]))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n",
                                  headers={"content-type": "text/plain"})
        if u == URL:
            return httpx.Response(200, text=source_html(),
                                  headers={"content-type": "text/html"})
        if u == "https://news-example.ru/company-update":
            return httpx.Response(200, text="""<html><body>
            ООО Тестовые Системы открыло новый магазин и расширение сети
            идёт в Москве. Компания заявила о серверном оборудовании.
            </body></html>""", headers={"content-type": "text/html"})
        if u == "https://test-systems.ru/contacts":
            return httpx.Response(200, text="""<html><body>
            Контакты ООО Тестовые Системы, отдел продаж.
            <a href="mailto:info@test-systems.ru">Написать</a>
            <a href="mailto:director@test-systems.ru">Личный</a>
            <a href="tel:+74951234567">Телефон</a>
            </body></html>""", headers={"content-type": "text/html"})
        if u == "https://unrelated.ru/contacts":
            return httpx.Response(200, text="""<html><body>
            Другой юридический субъект. info@unrelated.ru
            </body></html>""", headers={"content-type": "text/html"})
        pytest.fail("UNEXPECTED_EXTERIOR_ACCESS " + u)

    with httpx.Client(transport=httpx.MockTransport(respond),
                      trust_env=False) as client:
        yield client, calls


def test_external_web_search_evidence_contact_and_role_attribution(mocked_web):
    client, calls = mocked_web
    result = public.research_public(
        item(), client, frozenset({"hh.ru"}),
        now=datetime(2026, 11, 6, tzinfo=timezone.utc),
        deep_search_enabled=True, deep_search_key=KEY)
    external = result["independent_company_research"]
    assert external["status"] == "INDEX_SEARCH_DONE"
    assert external["company_name_provenance"] == "PUBLIC_JOBPOSTING_EMPLOYER_CLAIM"
    assert len([c for c in calls if "api.search.brave.com" in c]) == 3
    assert len(external["pages"]) == 2
    assert any("news-example.ru" in p["url"] for p in external["pages"])
    assert any(s["kind"] == "OPENING_OR_EXPANSION"
               and s["url"] == "https://news-example.ru/company-update"
               for s in external["hypotheses"])
    assert [c["value"] for c in external["contacts"]
            if c["type"] == "email"] == ["info@test-systems.ru"]
    assert any(c["value"] == "+74951234567" for c in external["contacts"])
    assert not any("employer@hh.ru" in str(c) for c in external["contacts"])
    assert not any("unrelated.ru" in c["url"] for c in external["contacts"])
    assert not any("127.0.0.1" in s for s in calls)
    fields = {name: "Предварительный HOLD" for name in AI_FIELDS}
    enhanced = public.enrich_manager_fields(fields, result, signal_kind="it_vacancy")
    report = enhanced[PREFIX + "AI_FULL_REPORT"]
    assert "НЕЗАВИСИМОЕ ИССЛЕДОВАНИЕ" in report
    assert "https://news-example.ru/company-update" in report
    assert "info@test-systems.ru" in report
    assert "Возможен запуск/расширение площадок" in report
    assert "НЕ доказывают юрлицо" in report


def test_missing_api_key_explains_no_independent_search(mocked_web):
    client, calls = mocked_web
    r = public.research_public(item(), client, frozenset({"hh.ru"}),
                               deep_search_enabled=True, deep_search_key="")
    assert r["independent_company_research"]["status"] == "INDEX_API_KEY_NOT_CONFIGURED"
    assert not any("api.search.brave.com" in c for c in calls)
    fields = public.enrich_manager_fields(
        {name: "HOLD" for name in AI_FIELDS}, r, signal_kind="it_vacancy")
    assert "INDEX_API_KEY_NOT_CONFIGURED" in fields[PREFIX + "AI_FULL_REPORT"]


def test_unpublished_crm_company_name_is_never_sent_to_index(mocked_web):
    client, calls = mocked_web
    page = {"text": "Вакансия тест инженера", "employer": {}}
    name, provenance = deep.published_company_name(
        item(company="СЕКРЕТНАЯ КОМПАНИЯ"), page)
    assert name == "" and provenance == "NO_COMPANY_PUBLISHED_IN_SOURCE"
    result = deep.independent_company_research(
        client, company_name=name, signal_kind="it_vacancy",
        source_host="hh.ru", search_token=KEY)
    assert result["status"] == "NO_PUBLIC_COMPANY_IDENTITY"
    assert calls == []


def test_untrusted_search_result_never_becomes_fact(mocked_web):
    client, _ = mocked_web
    assert deep._same_published_name(NAME, "совсем другая компания") is False
    assert deep._site_allowed("http://127.0.0.1/private",
                              source_host="hh.ru", accepted_hosts=set()) is None
    info = {"status": "INDEX_NO_RELEVANT_RESULTS", "checked_at": "2026-11-06",
            "pages": [], "contacts": [], "hypotheses": []}
    base = {name: "HOLD" for name in AI_FIELDS}
    result = deep.append_independent_intelligence(base, info)
    assert result[PREFIX + "AI_SUMMARY"] == "HOLD"
    assert "не найдено" in result[PREFIX + "AI_FULL_REPORT"]


def test_no_published_company_even_with_no_key_stays_unverified():
    r = deep.independent_company_research(
        httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("NO NETWORK"))),
        company_name="", signal_kind="direct-service",
        source_host="example.ru", search_token="")
    assert r["status"] == "NO_PUBLIC_COMPANY_IDENTITY"
