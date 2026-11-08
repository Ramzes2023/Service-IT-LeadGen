"""Browser stays internal; secretless egress forwards only bounded localhost requests."""
from pathlib import Path
import importlib.util
import json

import pytest

from leadpilot.serviceit_vps_public_browser_ingress import approved_url_only, browser_request


def test_ingress_permits_only_approved_https_hosts():
    allow = frozenset({"hh.ru"})
    assert approved_url_only("https://hh.ru/", allow)
    assert approved_url_only("https://career.hh.ru/vacancy/7", allow)
    for url in [
        "http://hh.ru/", "https://hh.ru.evil.example/",
        "https://127.0.0.1/", "https://localhost/",
        "https://admin:pw@hh.ru/", "https://hh.ru:444/",
        "https://hh.ru/#section", "https://evil.example/",
        "https://hh.ru\nInjected: X", 42, None,
    ]:
        assert not approved_url_only(url, allow)


def test_host_port_only_on_secretless_egress_network():
    text = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    browser = text.split("\n  egress:", 1)[0]
    egress = text.split("\n  egress:", 1)[1]
    assert "ports:" not in browser
    assert "127.0.0.1:18785:8766" in egress
    assert "127.0.0.1:18785" not in browser
    assert "3128:" not in egress
    assert "internal: true" in text
    assert "browser_internal" in browser
    assert "public_egress" not in browser
    assert "public_egress" in egress
    assert "cap_drop: [ALL]" in browser and "cap_drop: [ALL]" in egress
    assert "apparmor:serviceit-chromium" in browser
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" in browser
    assert "read_only: true" in browser and "read_only: true" in egress
    assert "BITRIX_WEBHOOK" not in text
    assert "PG_PASSWORD" not in text


def test_egress_relay_cannot_send_arbitrary_routes_or_proxy_destinations():
    ingress = Path("leadpilot/serviceit_vps_public_browser_ingress.py").read_text("utf8")
    proxy = Path("leadpilot/serviceit_vps_public_browser_proxy.py").read_text("utf8")
    assert 'UPSTREAM = "browser"' in ingress
    assert "UPSTREAM_PORT = 8765" in ingress
    assert 'connection.request("GET", "/health"' in ingress
    assert 'connection.request("POST", "/render"' in ingress
    assert "MAX_REQUEST_BYTES = 1200" in ingress
    assert "RENDER_LIMIT = threading.BoundedSemaphore(1)" in ingress
    assert 'IngressServer(("0.0.0.0", 8766), hosts)' in proxy
    assert 'ProxyServer(("0.0.0.0", 3128), hosts)' in proxy
    assert "exec(" not in ingress and "eval(" not in ingress
    assert "SERVICEIT_VPS_BROWSER_APPROVED_HOSTS" in proxy


def test_guarded_activation_checks_port_mapping_and_updates_flags_without_duplicates():
    src = Path("deploy/serviceit-vps-browser-live-activate.py").read_text("utf8")
    assert "BROWSER_HOST_PORT_EXPOSURE_FORBIDDEN" in src
    assert "BROWSER_EGRESS_LOOPBACK_BINDING_INVALID" in src
    assert 'ProxyHandler({})' in src
    assert "BROWSER_EGRESS_NETWORK_BOUNDARY_INVALID" in src
    assert "set_existing_or_append_flags(original" in src
    assert 'new_config += ("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED=YES' not in src
    module_path = Path("deploy/serviceit-vps-browser-live-activate.py")
    spec = importlib.util.spec_from_file_location("browser_vps_activate_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = "A=1\nSERVICEIT_VPS_BROWSER_RESEARCH_ENABLED=NO\n# keep\n"
    updated = module.set_existing_or_append_flags(old, {
        "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
        "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
    })
    assert updated.count("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED=") == 1
    assert updated.count("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED=") == 1
    assert "# keep\n" in updated
    assert "A=1\n" in updated
    assert "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED=YES\n" in updated
    assert module.set_existing_or_append_flags(updated, {
        "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
        "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
    }) == updated
