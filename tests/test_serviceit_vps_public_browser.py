"""No-network tests for the optional (disabled) Chromium public-browser sidecar."""
from pathlib import Path

import pytest
from leadpilot.serviceit_vps_public_browser import approved_hosts, verify_url, BrowserBlocked


def test_only_explicit_https_allowlisted_hostname_and_public_dns():
    hosts = approved_hosts("hh.ru,b2b-center.ru")
    assert verify_url("https://hh.ru/vacancy/123", hosts,
                      dns_check=lambda h: h == "hh.ru") == "hh.ru"
    assert verify_url("https://www.hh.ru/public", hosts,
                      dns_check=lambda h: True) == "www.hh.ru"


@pytest.mark.parametrize("url", [
    "http://hh.ru", "https://127.0.0.1/", "https://localhost/private",
    "https://169.254.169.254/metadata", "https://hh.ru:444/private",
    "https://hh.ru@127.0.0.1", "https://www.hh.ru#fragment",
    "https://evil.ru/page", "https://hh.ru.evil.ru/page",
    "ftp://hh.ru/foo", "https://hh.ru\r\nHeader: x",
])
def test_block_public_url_policy_without_network(url):
    with pytest.raises(BrowserBlocked):
        verify_url(url, approved_hosts("hh.ru"), dns_check=lambda _: True)


def test_dns_not_public_never_allowed():
    with pytest.raises(BrowserBlocked, match="BROWSER_DNS_NOT_PUBLIC"):
        verify_url("https://hh.ru/", approved_hosts("hh.ru"),
                   dns_check=lambda _: False)


@pytest.mark.parametrize("hosts", [
    "", "localhost", "169.254.169.254", "foo.invalid",
    "https://hh.ru", "hh.ru:443", "evil.ru/", "x.test",
])
def test_reject_missing_or_unsafe_hostname_lists(hosts):
    with pytest.raises(BrowserBlocked):
        approved_hosts(hosts)


def test_not_part_of_default_4_crm_worker_images():
    main = Path("Dockerfile.serviceit-vps").read_text("utf8")
    browser_docker = Path("Dockerfile.serviceit-vps-browser").read_text("utf8")
    browser_compose = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    native = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    assert "playwright install" not in main.lower()
    assert "serviceit_vps_public_browser" not in native
    assert "profiles: [\"browser\"]" in browser_compose
    assert "mcr.microsoft.com/playwright/python:v1.64.1-noble" in browser_docker
    assert "playwright==1.64.1" in browser_docker
    assert "cap_drop: [ALL]" in browser_compose
    assert "read_only: true" in browser_compose
    assert "internal: true" in browser_compose
    assert '"127.0.0.1:18785:8766"' in browser_compose
    assert "ports:" not in browser_compose.split("\n  egress:", 1)[0]
    assert "SERVICEIT_VPS_BROWSER_PROXY_URL" in browser_compose
    assert "serviceit_vps_public_browser_proxy" in browser_compose
    assert "network_mode: host" not in browser_compose
    for text in (browser_docker, browser_compose):
        for forbidden in (
            "serviceit_pg_password", "serviceit_bitrix_webhook",
            "serviceit_routerai_key", "GITHUB_TOKEN", "crm.item.update",
        ):
            assert forbidden not in text
    assert "USER pwuser" in browser_docker


def test_rendered_content_no_automatic_ai_or_crm_side_effects():
    text = Path("leadpilot/serviceit_vps_public_browser.py").read_text("utf8")
    assert 'chromium_sandbox=True' in text
    assert 'service_workers="block"' in text
    assert 'accept_downloads=False' in text
    assert '_robots(client, url, allowed)' in text
    assert 'context.route("**/*", route_guard)' in text
    assert 'verify_url(route.request.url, allowed)' in text
    assert 'follow_redirects=False' in text
    assert 'model_calls": 0' in text
    assert 'crm_writes": 0' in text
    assert 'contacts_authorized": False' in text
