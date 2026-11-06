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
    monkeypatch.setattr(deep, "_GDELT_MIN_INTERVAL", 0)
    calls = []
    def respond(request):
        u = str(request.url)
        calls.append(u)
        if request.url.host == "api.gdeltproject.org":
            assert request.url.path == "/api/v2/doc/doc"
            assert NAME.split()[1] in request.url.params["query"]
            return httpx.Response(200, json={"articles": []})
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
    assert r["independent_company_research"]["status"] == "FREE_NEWS_NO_MATCH_FULL_WEB_KEY_MISSING"
    assert not any("api.search.brave.com" in c for c in calls)
    assert any("api.gdeltproject.org" in c for c in calls)
    fields = public.enrich_manager_fields(
        {name: "HOLD" for name in AI_FIELDS}, r, signal_kind="it_vacancy")
    assert "FREE_NEWS_NO_MATCH_FULL_WEB_KEY_MISSING" in fields[PREFIX + "AI_FULL_REPORT"]
    assert "GDELT Project" in fields[PREFIX + "AI_FULL_REPORT"]


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


def test_complete_source_hold_can_receive_first_real_ai_hypothesis(tmp_path, monkeypatch):
    """A source-created 9-field HOLD is not permanently frozen as NOT_RUN."""
    from copy import deepcopy
    from leadpilot.serviceit_enrichment_store import Store
    from leadpilot.serviceit_windows_autofill import (
        preliminary_fields, preliminary_once, FIELDS,
    )
    import leadpilot.serviceit_windows_autofill as autofill
    monkeypatch.setattr(autofill, "schema_limits",
                        lambda rest: {k: 12000 for k in FIELDS})
    source_item = {code: None for code in FIELDS}
    source_item.update({
        "id": 1001, "entityTypeId": 1062, "categoryId": 21,
        "title": "Поставка серверов и IT-аутсорсинг",
        PREFIX + "SIGNAL_KIND": "tender-opportunity",
        PREFIX + "SIGNAL_URL": "https://www.b2b-center.ru/tender-1234567/",
        PREFIX + "SIGNAL_SOURCE": "public-procurement",
    })
    source_item.update(preliminary_fields(source_item))
    assert source_item[PREFIX + "AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"

    class CRM:
        def __init__(self):
            self.item = source_item
            self.writes = 0

        def __call__(self, method, params):
            if method == "crm.item.get":
                return {"item": deepcopy(self.item)}
            if method == "crm.item.update":
                assert set(params["fields"]) == AI_FIELDS
                self.item.update(params["fields"])
                self.writes += 1
                return {"item": deepcopy(self.item)}
            pytest.fail("OUTSIDE_TARGET_CRM_SCOPE")

    crm = CRM()
    store = Store(tmp_path / "new-card-events.sqlite3")
    store.enqueue(1001)
    seen = []
    def model(text):
        seen.append(text)
        return {"origin": "AI_hypothesis", "provider": "gigachat",
                "model": "GigaChat-2", "quality_status": "complete",
                "service": "IT-аутсорсинг",
                "possible_need": "Возможно требуется техническое сопровождение.",
                "evidence_quote": "Поставка серверов",
                "confidence": 0.8}

    result = preliminary_once(
        store, crm, 1001, model=model,
        public_ai_hosts=frozenset({"b2b-center.ru"}))
    assert result == "HOLD_9_WRITTEN"
    assert seen == ["Поставка серверов и IT-аутсорсинг"]
    assert crm.writes == 1
    assert crm.item[PREFIX+"AI_STATUS"] == "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert crm.item[PREFIX+"LEAD_SCORE"] is None
    assert preliminary_once(
        store, crm, 1001, model=model,
        public_ai_hosts=frozenset({"b2b-center.ru"})) == "IDLE"


def test_multi_source_model_context_uses_only_safe_visible_excerpts():
    pages = {"pages": [
        {"excerpt": "Открытие филиала. Поставки новых касс и оборудования."},
        {"excerpt": "Автоматизация процессов учёта и интеграция 1С."},
        {"excerpt": "Контакт info@example.ru"},
        {"excerpt": "Расширение торговой сети: кассовые системы."},
    ]}
    sample = deep.model_safe_context(pages)
    assert "Открытие филиала" in sample
    assert "Автоматизация" in sample
    assert "info@example.ru" not in sample
    assert sample.count(" | ") == 1


def test_free_news_official_api_discovers_company_expansion_without_search_key(monkeypatch):
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    monkeypatch.setattr(deep, "_GDELT_MIN_INTERVAL", 0)
    reqs = []
    def respond(request):
        reqs.append(str(request.url))
        if request.url.host == "api.gdeltproject.org":
            assert request.url.params["mode"] == "ArtList"
            return httpx.Response(200, json={"articles": [{
                "url": "https://news-example.ru/company-update",
                "title": "Планы нового магазина и оборудование",
                "seendate": "20261105T170000Z",
            }]})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n",
                                  headers={"content-type": "text/plain"})
        if str(request.url) == "https://news-example.ru/company-update":
            return httpx.Response(200, text=(
                "<html><body>ООО Тестовые Системы открыло новый магазин, "
                "для него приобретено серверное оборудование.</body></html>"),
                headers={"content-type": "text/html"})
        pytest.fail("Unexpected URL " + str(request.url))
    with httpx.Client(transport=httpx.MockTransport(respond),
                      trust_env=False) as client:
        result = deep.independent_company_research(
            client, company_name=NAME, signal_kind="it_vacancy",
            source_host="hh.ru", search_token="")
    assert result["status"] == "FREE_NEWS_SEARCH_DONE_FULL_WEB_KEY_MISSING"
    assert result["search_backend"] == "FREE_GDELT_NEWS_ONLY"
    assert len(result["pages"]) == 1
    assert "news-example.ru" in result["pages"][0]["url"]
    assert any(s["kind"] == "OPENING_OR_EXPANSION" for s in result["hypotheses"])
    assert not any("api.search.brave.com" in x for x in reqs)
