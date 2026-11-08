"""The browser must defer public DNS to the pinned HTTPS CONNECT egress proxy."""
from pathlib import Path

import httpx
import pytest

from leadpilot import serviceit_vps_public_browser as browser
from leadpilot.serviceit_public_evidence_research import ResearchBlocked


def test_browser_approves_public_hostname_without_container_dns():
    hosts = browser.approved_hosts("hh.ru")
    assert browser._proxy_validated_host("https://hh.ru/", hosts) == "hh.ru"
    for url in (
        "http://hh.ru/", "https://127.0.0.1/", "https://localhost/",
        "https://hh.ru.evil.example/", "https://hh.ru:5432/",
        "https://a:b@hh.ru/", "https://hh.ru/#internal",
        "https://169.254.169.254/", "https://hh.ru/\r\nHost:127.0.0.1",
    ):
        with pytest.raises(browser.BrowserBlocked):
            browser._proxy_validated_host(url, hosts)


def test_browser_robots_allow_and_disallow_via_proxy_without_local_dns():
    seen = []
    def allow(request):
        seen.append((request.url.host, request.url.path, request.headers.get("user-agent")))
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    with httpx.Client(transport=httpx.MockTransport(allow), trust_env=False) as client:
        browser._robots(client, "https://hh.ru/", frozenset({"hh.ru"}))
    assert len(seen) == 1
    assert seen[0][0:2] == ("hh.ru", "/robots.txt")
    assert seen[0][2]

    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    ), trust_env=False) as client:
        with pytest.raises(ResearchBlocked, match="RESEARCH_ROBOTS_DISALLOWED"):
            browser._robots(client, "https://hh.ru/", frozenset({"hh.ru"}))


@pytest.mark.parametrize("code", [301, 302, 403, 404, 500])
def test_browser_robots_fails_closed_on_redirect_or_unavailable(code):
    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(code, headers={"Location": "https://evil.example/"})
    ), trust_env=False, follow_redirects=False) as client:
        with pytest.raises(ResearchBlocked, match="RESEARCH_ROBOTS_UNKNOWN"):
            browser._robots(client, "https://hh.ru/", frozenset({"hh.ru"}))


def test_browser_robots_oversize_fails_closed():
    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"a" * (browser.MAX_HTML + 1))
    ), trust_env=False) as client:
        with pytest.raises(ResearchBlocked, match="RESEARCH_ROBOTS_UNKNOWN"):
            browser._robots(client, "https://hh.ru/", frozenset({"hh.ru"}))


def test_browser_requires_pinned_proxy_before_dns_delegation(monkeypatch):
    monkeypatch.delenv("SERVICEIT_VPS_BROWSER_PROXY_URL", raising=False)
    with pytest.raises(browser.BrowserBlocked, match="BROWSER_PINNED_EGRESS_PROXY_REQUIRED"):
        browser.render_public("https://hh.ru/", frozenset({"hh.ru"}))


def test_pinned_proxy_keeps_atomic_global_ip_validation_and_browser_sandbox():
    proxy = Path("leadpilot/serviceit_vps_public_browser_proxy.py").read_text("utf8")
    source = Path("leadpilot/serviceit_vps_public_browser.py").read_text("utf8")
    compose = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    assert "ipaddress.ip_address(info[4][0]).is_global" in proxy
    assert "upstream.connect(sockaddr)" in proxy
    assert 'if proxy != "http://egress:3128"' in source
    assert "_robots(client, url, allowed)" in source
    assert "_proxy_validated_host(route.request.url, allowed)" in source
    assert "_proxy_validated_host(page.url, allowed)" in source
    assert "chromium_sandbox=True" in source
    assert 'follow_redirects=False' in source
    assert "internal: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert 'apparmor:serviceit-chromium' in compose
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" in compose
    assert '127.0.0.1:18785:8766' in compose
    assert "network_mode: host" not in compose
