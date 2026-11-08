"""Offline safety and behavioral tests for browser-to-native AI evidence wiring."""
from pathlib import Path
import socket

import httpx
import pytest

from leadpilot.serviceit_vps_public_browser import BrowserBlocked
from leadpilot import serviceit_vps_public_browser_bridge as bridge
from leadpilot import serviceit_vps_public_browser_proxy as proxy
from leadpilot import serviceit_vps_browser_client as browser_client
from leadpilot import serviceit_public_evidence_research as research


def test_bridge_only_accepts_exact_public_url_no_customer_fields(monkeypatch):
    monkeypatch.setattr(bridge, "render_public", lambda url, allowed: {
        "status": "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED",
        "public_page_host": "hh.ru",
        "text": "Открытый текст со страницы",
        "requests_checked": 1,
    })
    result = bridge.render_request({"url": "https://hh.ru/vacancy/1"}, frozenset({"hh.ru"}))
    assert result["crm_writes"] == 0
    assert result["model_calls"] == 0
    assert result["text"] == "Открытый текст со страницы"
    for bad in ({}, {"card": 3, "url": "https://hh.ru/"}, {"url": 123}):
        with pytest.raises(BrowserBlocked):
            bridge.render_request(bad, frozenset({"hh.ru"}))


def test_proxy_rejects_private_dns_and_unapproved_hosts():
    calls = []
    def private_dns(*args, **kwargs):
        calls.append(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    for host in ("127.0.0.1", "hh.ru.evil.ru", "localhost"):
        with pytest.raises(ValueError):
            proxy.resolve_public_socket(host, 443, frozenset({"hh.ru"}), resolver=private_dns)
    assert not calls, "unapproved targets must be rejected BEFORE DNS resolution"
    with pytest.raises(ValueError, match="PROXY_DNS_NOT_PUBLIC"):
        proxy.resolve_public_socket("hh.ru", 443, frozenset({"hh.ru"}), resolver=private_dns)


def test_proxy_connects_to_checked_ip_not_re_resolved(monkeypatch):
    observed = []
    class FakeSocket:
        def settimeout(self, n):
            pass
        def connect(self, sockaddr):
            observed.append(sockaddr)
        def close(self):
            pass
    monkeypatch.setattr(proxy.socket, "socket", lambda *args: FakeSocket())
    upstream = proxy.resolve_public_socket(
        "hh.ru", 443, frozenset({"hh.ru"}),
        resolver=lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                   ("8.8.8.8", 443))],
    )
    assert upstream is not None
    assert observed == [("8.8.8.8", 443)]


def test_native_bridge_only_posts_approved_url_and_validates_unverified_result(monkeypatch):
    monkeypatch.setattr(browser_client, "_public_dns", lambda x: x == "hh.ru")
    calls = []
    def response(request):
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 18785
        calls.append(request.content)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, json={
            "status": "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED",
            "public_page_host": "hh.ru",
            "text": "Публичный текст JavaScript-страницы",
            "crm_writes": 0,
            "model_calls": 0,
        })
    with httpx.Client(transport=httpx.MockTransport(response), trust_env=False) as client:
        page = browser_client.render_approved_url(
            "https://hh.ru/vacancy/123", frozenset({"hh.ru"}), client=client)
        assert page and page["browser_unverified"] is True
        assert page["emails"] == []
        assert calls and b"crm" not in calls[0].lower()
        assert browser_client.render_approved_url(
            "https://127.0.0.1/private", frozenset({"hh.ru"}), client=client) is None
        assert len(calls) == 1


def test_js_render_fallback_only_adds_unverified_public_evidence(monkeypatch):
    def no_static(*a, **k):
        raise research.ResearchBlocked("RESEARCH_PAGE_UNAVAILABLE")
    monkeypatch.setattr(research, "_page", no_static)
    page = {
        "url": "https://hh.ru/vacancy/123",
        "text": "Публичная вакансия сопровождения 1С",
        "links": [], "emails": [], "tel": [], "employer": {},
        "browser_unverified": True,
    }
    seen = []
    def renderer(url):
        seen.append(url)
        return page
    item = {
        research.PREFIX + "SIGNAL_KIND": "it_vacancy",
        research.PREFIX + "SIGNAL_URL": "https://hh.ru/vacancy/123",
    }
    with httpx.Client() as client:
        result = research.research_public(item, client, frozenset({"hh.ru"}),
                                         browser_render=renderer)
    assert seen == [item[research.PREFIX + "SIGNAL_URL"]]
    assert result["browser_rendered_unverified"] is True
    assert result["contacts"] == []
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"


def test_browser_bridge_fail_closed_by_default_and_no_secrets():
    dispatcher = Path("leadpilot/serviceit_bitrix_dispatch_worker.py").read_text("utf8")
    coordinator = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    browser_compose = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    assert 'SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"' in dispatcher
    assert 'SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES"' in dispatcher
    assert "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED:-NO" in coordinator
    assert "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED:-NO" in coordinator
    assert "internal: true" in browser_compose
    assert "http://egress:3128" in browser_compose
    assert '"127.0.0.1:18785:8766"' in browser_compose\n    assert "ports:" not in browser_compose.split("\\n  egress:", 1)[0]
    for forbidden in ("bitrix_webhook", "routerai_key", "pg_password",
                      "network_mode: host", "privileged: true"):
        assert forbidden not in browser_compose.lower()
