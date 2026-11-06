"""No-network checks: safe public evidence, contacts, AI and Bitrix nine-field upgrade."""
from datetime import datetime, timezone
import httpx
import pytest

from leadpilot import serviceit_public_evidence_research as research
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
from leadpilot.serviceit_preliminary_gigachat import model_hypothesis_fields


def item(kind="it_vacancy", url="https://hh.ru/vacancy/123456"):
    return {PREFIX+"SIGNAL_KIND": kind, PREFIX+"SIGNAL_URL": url}


def test_public_job_page_resolves_only_allowlisted_corporate_contacts(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda host: True)
    visited = []

    def respond(request):
        url = str(request.url)
        visited.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n",
                                  headers={"content-type": "text/plain"})
        if url == "https://hh.ru/vacancy/123456":
            return httpx.Response(200, text="""<html><body>
                <h1>Вакансия системного администратора</h1>
                <p>Работа с серверами и сетями, требуется в штат.</p>
                <a href="mailto:fake-employer@hh.ru">Написать</a>
                <a href="https://example-company.ru/">Сайт компании</a>
                </body></html>""", headers={"content-type": "text/html"})
        if url == "https://example-company.ru/":
            return httpx.Response(200, text="""<html><body>
                <h1>Компания</h1>
                <a href="mailto:info@example-company.ru">Почта</a>
                <a href="/contacts">Контакты</a>
                </body></html>""", headers={"content-type": "text/html"})
        if url == "https://example-company.ru/contacts":
            return httpx.Response(200, text="""<html><body>
                <a href="mailto:hr@example-company.ru">HR</a>
                <a href="tel:+74951234567">Позвонить</a>
                </body></html>""", headers={"content-type": "text/html"})
        pytest.fail("UNEXPECTED_EXTERNAL_URL " + url)

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        result = research.research_public(
            item(), client, frozenset({"hh.ru", "example-company.ru"}),
            now=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"
    assert len(result["pages"]) == 3
    assert result["company_site_candidate"] == "example-company.ru"
    assert any(x["value"] == "hr@example-company.ru"
               and x["role"] == "отдел подбора персонала"
               for x in result["contacts"])
    assert any(x["value"] == "+74951234567" for x in result["contacts"])
    assert not any("fake-employer" in x["value"] for x in result["contacts"])
    assert not any("127.0.0.1" in u for u in visited)


def test_no_unapproved_company_site_fetch_or_marketplace_contacts(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda host: True)
    def respond(request):
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /",
                                  headers={"content-type": "text/plain"})
        assert request.url.host == "hh.ru"
        return httpx.Response(200, text="""<html><body>
            <a href="https://unapproved-employer.ru/">Официальный сайт</a>
            <a href="mailto:operator@hh.ru">Связаться</a>
            </body></html>""", headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = research.research_public(item(), client, frozenset({"hh.ru"}))
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"
    assert result["contacts"] == []
    assert result["company_site_candidate"] == ""
    assert len(result["pages"]) == 1


def test_unknown_host_never_makes_http_request(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda host: pytest.fail("DNS NEVER CALLED"))
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("HTTP NEVER CALLED"))) as client:
        with pytest.raises(research.ResearchBlocked, match="RESEARCH_HOST_NOT_APPROVED"):
            research.research_public(item(url="https://169.254.169.254/latest/meta-data"),
                                     client, frozenset({"hh.ru"}))


def test_robots_disallow_blocks_public_source_without_contact_leak(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda host: True)
    calls = []
    def respond(request):
        calls.append(request.url.path)
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /",
                              headers={"content-type": "text/plain"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = research.research_public(item(), client, frozenset({"hh.ru"}))
    assert result["status"] == "PUBLIC_SOURCE_NOT_READABLE"
    assert result["contacts"] == []
    assert calls == ["/robots.txt"]


def test_append_public_evidence_to_existing_nine_fields_with_provenance():
    fields = {name: "Предварительный отчёт" for name in AI_FIELDS}
    findings = {"status": "PUBLIC_PAGES_INSPECTED",
                "checked_at": "2026-11-06T14:00:00+00:00",
                "pages": [{"url": "https://example-company.ru/contacts",
                           "excerpt": "Официальный адрес компании"}],
                "contacts": [{"type": "email", "value": "hr@example-company.ru",
                              "role": "отдел подбора персонала",
                              "url": "https://example-company.ru/contacts"}]}
    updated = research.enrich_manager_fields(fields, findings,
                                             signal_kind="it_vacancy")
    assert set(updated) == AI_FIELDS
    assert "Найден" in updated[PREFIX+"AI_RECOMMENDATION"]
    assert "hr@example-company.ru" in updated[PREFIX+"AI_FULL_REPORT"]
    assert "https://example-company.ru/contacts" in updated[PREFIX+"AI_FULL_REPORT"]
    assert "HR / ИТ-руководитель" in updated[PREFIX+"AI_NEXT_ACTION"]
    assert "Предварительный отчёт" in updated[PREFIX+"AI_FULL_REPORT"]


def test_model_quote_must_match_public_excerpt_or_title_not_fabricated():
    fields = {name: "Предварительный разбор" for name in AI_FIELDS}
    base = {
        "provider": "gigachat", "origin": "AI_hypothesis",
        "model": "GigaChat-2", "quality_status": "complete",
        "service": "1С", "confidence": 0.8,
        "possible_need": "Может потребоваться поддержка 1С.",
        "evidence_quote": "сопровождение 1С",
    }
    result = model_hypothesis_fields(
        fields, "Вакансия", base, public_excerpt="Требуется сопровождение 1С.")
    assert "сопровождение 1С" in result[PREFIX+"AI_QUOTE"]
    with pytest.raises(Exception, match="GIGACHAT_UNVERIFIED_OUTPUT"):
        model_hypothesis_fields(
            fields, "Вакансия", {**base, "evidence_quote": "Утверждён бюджет"},
            public_excerpt="Требуется сопровождение 1С.")
