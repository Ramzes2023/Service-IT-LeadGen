"""Guarded offline coverage for official public tender pages past 7K and cited dossiers."""
import httpx
import pytest

from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot import serviceit_public_evidence_research as public
from leadpilot import serviceit_b2b_routerai_deep as deep
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

TENDER = "4675475"
SUBJECT = "Приобретение серверного оборудования для нужд МКБ"
URL = "https://www.b2b-center.ru/search/moskva/servernoe-oborudovanie/"
TOKEN = "SYNTHETIC-BRAVE-NOT-REAL"


def _client(html, *, robots="User-agent: *\nAllow: /"):
    calls = []
    def respond(req):
        calls.append((req.url.host, req.url.path))
        if req.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web": {"results": [
                {"url": URL, "description": "A search snippet is NOT evidence"}
            ]}})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots,
                                  headers={"content-type": "text/plain"})
        if req.url.host == "www.b2b-center.ru" and req.url.path.startswith("/search/"):
            return httpx.Response(200, text=html,
                                  headers={"content-type": "text/html"})
        raise AssertionError("Unapproved network request")
    return httpx.Client(transport=httpx.MockTransport(respond),
                        follow_redirects=False, trust_env=False), calls


def test_official_html_late_exact_tender_and_subject_discovered(monkeypatch):
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    filler = "Открытая категория оборудования: подразделения и список тендеров. " * 150
    assert len(filler) > 7000
    title = (" Закупка №4675475/1. Приобретение серверного оборудования "
             "для нужд МКБ. Заказчик ПАО «Московский Кредитный Банк». "
             "Подача заявок до 16.11.2026 16:00 МСК.")
    client, calls = _client("<html><body>" + filler + title + "</body></html>")
    with client:
        found = research.brave_notice_page(
            client, TENDER, frozenset({"b2b-center.ru"}), TOKEN, subject=SUBJECT)
    assert found is not None
    assert found["pages"][0]["url"] == URL
    assert TENDER in found["pages"][0]["excerpt"]
    assert "16.11.2026" in found["pages"][0]["excerpt"]
    assert calls.count(("www.b2b-center.ru", "/robots.txt")) == 2
    assert calls.count(("www.b2b-center.ru", "/search/moskva/servernoe-oborudovanie/")) == 2


def test_official_html_same_id_but_unrelated_procurement_is_rejected(monkeypatch):
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    filler = "Категория прочих услуг. " * 440
    html = "<html><body>" + filler + (
        " Закупка 4675475. Поставка офисной мебели, стульев и столов. "
    ) + "</body></html>"
    client, _ = _client(html)
    with client:
        assert research.brave_notice_page(
            client, TENDER, frozenset({"b2b-center.ru"}), TOKEN,
            subject=SUBJECT) is None


def test_official_html_robots_disallow_does_not_use_search_snippet(monkeypatch):
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    client, calls = _client(
        "<html><body>4675475 Приобретение серверного оборудования для нужд МКБ</body></html>",
        robots="User-agent: *\nDisallow: /")
    with client:
        assert research.brave_notice_page(
            client, TENDER, frozenset({"b2b-center.ru"}), TOKEN,
            subject=SUBJECT) is None
    assert not any(p.startswith("/search/") for _, p in calls)


def test_nine_field_report_counts_distinct_urls_not_quote_count():
    public_findings = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "checked_at": "2026-11-10T17:30:00+00:00",
        "pages": [{"url": URL, "excerpt": (
            "Закупка 4675475. Приобретение серверного оборудования для нужд МКБ. "
            "Заказчик ПАО «Московский Кредитный Банк». "
            "Срок подачи заявок до 16.11.2026 16:00. "
            "Начальная цена: сумма не задана. "
            "Условия участия: предварительный квалификационный отбор. "
            "Поставка серверного оборудования в Москве."
        )}]
    }
    data = deep.source_only_investigation(public_findings)
    assert data["facts"]
    fields = deep.cited_fields(
        {name: "ПРЕДВАРИТЕЛЬНО" for name in AI_FIELDS},
        public_findings, data)
    full = fields[PREFIX + "AI_FULL_REPORT"]
    assert "уникальных публичных URL: 1" in full
    assert "Цитаты одной страницы" in full
    assert URL in full
    assert "HOLD" in full
    assert all(0 < len(v) <= (12000 if k.endswith("AI_FULL_REPORT") else 4096)
               for k,v in fields.items())
