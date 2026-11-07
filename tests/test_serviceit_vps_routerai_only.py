"""RouterAI-only VPS configuration tests; no real credentials or network."""
from pathlib import Path

import pytest

from leadpilot import serviceit_vps_worker as worker
from leadpilot.serviceit_vps_pg_queue import VpsBlocked


def fake_secret_file(env, name, optional=False):
    assert name != "SERVICEIT_GIGACHAT_CREDENTIALS"
    return "SYNTHETIC_ONLY_NOT_A_REAL_KEY_1234567890"


def test_only_routerai_and_no_gigachat_fallback(monkeypatch):
    monkeypatch.setattr(worker, "secret_file", fake_secret_file)
    env = {
        "SERVICEIT_AI_PROVIDER": "routerai",
        "GIGACHAT_CREDENTIALS": "MUST_NOT_BE_USED_ON_VPS",
        "SERVICEIT_GIGACHAT_CREDENTIALS_FILE":
            "/run/secrets/unexpected-credential",
        "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT": "YES",
    }
    actual = worker.exact_environment(env)
    assert actual["AI_PROVIDER"] == "routerai"
    assert actual["ROUTERAI_API_KEY"].startswith("SYNTHETIC_ONLY")
    assert actual["GIGACHAT_CREDENTIALS"] == ""
    assert actual["SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT"] == "NO"


@pytest.mark.parametrize("provider", ["gigachat", "openai", "GigaChat", "none"])
def test_other_providers_fail_closed(monkeypatch, provider):
    monkeypatch.setattr(worker, "secret_file", fake_secret_file)
    with pytest.raises(VpsBlocked, match="ROUTERAI_ONLY"):
        worker.exact_environment({"SERVICEIT_AI_PROVIDER": provider})


def test_routerai_only_by_default(monkeypatch):
    monkeypatch.setattr(worker, "secret_file", fake_secret_file)
    actual = worker.exact_environment({})
    assert actual["AI_PROVIDER"] == "routerai"
    assert actual["GIGACHAT_CREDENTIALS"] == ""


def test_compose_requires_eight_secrets_no_gigachat():
    text = (
        Path(__file__).resolve().parents[1]
        / "deploy" / "serviceit-vps.compose.yml"
    ).read_text(encoding="utf8")
    assert 'SERVICEIT_AI_PROVIDER: "routerai"' in text
    assert 'SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT: "NO"' in text
    assert "serviceit_gigachat_credentials" not in text.lower()
    assert "GIGACHAT_SCOPE:" not in text
    assert "GIGACHAT_MODEL:" not in text
    assert "serviceit_gigachat_ca_setup" not in text
    assert "serviceit_routerai_key" in text
    assert "serviceit_github_ledger_pat" in text


def test_readonly_compose_unaffected():
    text = (
        Path(__file__).resolve().parents[1]
        / "deploy" / "serviceit-vps-two-secret-readonly.compose.yml"
    ).read_text(encoding="utf8")
    assert 'SERVICEIT_VPS_WRITER_MODE: "READONLY"' in text
    assert 'SERVICEIT_VPS_POLL_ENABLED: "NO"' in text
    assert 'SERVICEIT_VPS_CUTOVER_AUTHORIZED: "NO"' in text
