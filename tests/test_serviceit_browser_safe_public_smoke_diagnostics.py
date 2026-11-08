"""Safe, bounded VPS browser public-page smoke diagnostic categories."""
import json
from pathlib import Path

import pytest

from leadpilot import serviceit_vps_public_browser as browser
from leadpilot.serviceit_public_evidence_research import ResearchBlocked


def test_browser_cli_prints_only_known_reason_code(monkeypatch, capsys):
    monkeypatch.setenv("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS", "hh.ru")
    monkeypatch.setattr(browser, "render_public",
        lambda *args: (_ for _ in ()).throw(
            browser.BrowserBlocked("BROWSER_ROBOTS_DISALLOWED")))
    assert browser.main(["--url", "https://hh.ru/"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "status": "CHROMIUM_BROWSER_BLOCKED_OR_UNVERIFIED",
        "diagnostic_code": "BROWSER_ROBOTS_DISALLOWED",
        "crm_writes": 0, "model_calls": 0, "external_contacts": 0,
    }


def test_malicious_exception_text_is_never_exposed(monkeypatch, capsys):
    monkeypatch.setenv("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS", "hh.ru")
    monkeypatch.setattr(browser, "render_public",
        lambda *args: (_ for _ in ()).throw(browser.BrowserBlocked(
            "https://private.example.net/?access_token=DO_NOT_LOG")))
    assert browser.main(["--url", "https://hh.ru/"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["diagnostic_code"] == "BROWSER_FAILURE_UNCLASSIFIED"
    assert "DO_NOT_LOG" not in json.dumps(data)


@pytest.mark.parametrize("research_code,browser_code", [
    ("RESEARCH_ROBOTS_DISALLOWED", "BROWSER_ROBOTS_DISALLOWED"),
    ("RESEARCH_ROBOTS_UNKNOWN", "BROWSER_ROBOTS_UNAVAILABLE"),
    ("RESEARCH_DNS_UNVERIFIED", "BROWSER_ROBOTS_DNS_UNVERIFIED"),
    ("RESEARCH_PAGE_UNAVAILABLE", "BROWSER_ROBOTS_UNAVAILABLE"),
    ("random exception with https://secret", "BROWSER_ROBOTS_NOT_APPROVED"),
])
def test_robots_diagnostic_uses_constant_codes_only(
        monkeypatch, research_code, browser_code):
    monkeypatch.setenv("SERVICEIT_VPS_BROWSER_PROXY_URL", "http://egress:3128")
    monkeypatch.setattr(browser, "verify_url", lambda *args, **kwargs: "hh.ru")
    monkeypatch.setattr(browser, "_robots", lambda *args: (
        (_ for _ in ()).throw(ResearchBlocked(research_code))))
    with pytest.raises(browser.BrowserBlocked, match="^" + browser_code + "$"):
        browser.render_public("https://hh.ru/", frozenset({"hh.ru"}))


def test_activation_emits_only_audited_category_before_rollback():
    src = Path("deploy/serviceit-vps-browser-live-activate.py").read_text("utf8")
    assert "BROWSER_PUBLIC_SMOKE_DIAGNOSTIC=" in src
    assert "safe_codes =" in src
    assert 'len(result.stdout) <= 1024' in src
    assert 'parsed.get("diagnostic_code") in safe_codes' in src
    assert 'BROWSER_REAL_PUBLIC_RENDER_UNVERIFIED' in src
    assert 'BROWSER_ACTIVATION=BLOCKED_ROLLBACK' in src
    assert "print(result.stdout)" not in src
    assert "print(result.stderr)" not in src
