"""Brave VPS must be a coordinator-only opt-in and never another CRM writer."""
from pathlib import Path
import importlib.util
import json

import httpx

from leadpilot import serviceit_vps_brave_auth_probe as brave_probe


def test_brave_only_overlay_mounts_secret_into_coordinator():
    text = Path("deploy/serviceit-vps-brave.compose.yml").read_text("utf8")
    assert "serviceit_brave_search_key" in text
    assert "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE:" in text
    assert "/run/secrets/serviceit_brave_search_key" in text
    assert "/brave_api_key" in text
    assert "coordinator:" in text
    assert "auxiliaries:" not in text
    assert "observer:" not in text
    assert "business:" not in text
    assert "ingress:" not in text
    assert "BITRIX_API_KEY" not in text
    assert "SERVICEIT_BRAVE_SEARCH_API_KEY:" not in text


def test_brave_web_and_context_live_probe_hides_key_and_has_no_crm(monkeypatch):
    calls = []
    monkeypatch.setattr(brave_probe, "secret_file",
                        lambda env, name: "SECRET_FOR_UNIT_TEST_ONLY")
    def safe_search(client, key, *, context_mode=False):
        assert key == "SECRET_FOR_UNIT_TEST_ONLY"
        calls.append(context_mode)
        return ("BRAVE_CONTEXT_API_200_VALID_GROUNDING" if context_mode else
                "BRAVE_API_200_VALID_WEB_RESULTS")
    monkeypatch.setattr(brave_probe, "_brave", safe_search)
    with httpx.Client(transport=httpx.MockTransport(lambda r: None)) as client:
        result = brave_probe.probe({}, client)
    assert result["status"] == "BRAVE_SEARCH_VPS_READY"
    assert result["api_requests_max"] == 2
    assert calls == [False, True]
    assert "SECRET_FOR_UNIT_TEST_ONLY" not in str(result)
    assert result["crm_writes"] == 0
    assert result["model_calls"] == 0
    assert result["leads_added"] == 0


def test_brave_probe_does_not_call_context_after_web_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(brave_probe, "secret_file", lambda env, name: "123456789012")
    def web_failure(client, key, *, context_mode=False):
        calls.append(context_mode)
        return "BRAVE_RATE_LIMIT_429"
    monkeypatch.setattr(brave_probe, "_brave", web_failure)
    with httpx.Client() as client:
        result = brave_probe.probe({}, client)
    assert result["status"] == "BRAVE_SEARCH_VPS_NOT_AUTHENTICATED"
    assert result["context"] == "NOT_CALLED"
    assert result["api_requests_max"] == 1
    assert calls == [False]


def test_activator_requires_owner_approval_four_live_and_browser():
    src = Path("deploy/serviceit-vps-brave-live-activate.py").read_text("utf8")
    assert "ACTIVATE_VPS_BRAVE_SEARCH_CONTEXT_1062_21" in src
    assert '"SERVICEIT_VPS_WRITER_MODE": "LIVE"' in src
    assert '"SERVICEIT_VPS_POLL_ENABLED": "YES"' in src
    assert '"SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES"' in src
    assert '"SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES"' in src
    assert '"serviceit-leadgen-vps-auxiliaries-1"' in src
    assert '"serviceit-leadgen-vps-business-1"' in src
    assert '"serviceit-leadgen-vps-observer-1"' in src
    assert "BRAVE_SECRET_NOT_COORDINATOR_ONLY" in src
    assert "BRAVE_RAW_KEY_IN_COMPOSE_FORBIDDEN" in src
    assert '"--force-recreate", "coordinator"' in src
    assert "BRAVE_ROLLBACK=RESTORED_CORE_AND_BROWSER" in src
    assert "BRAVE_SEARCH_VPS_READY" in src
    assert "BRAVE_MAX_SEARCH_CALLS_PER_CARD=3" in src
    assert "REAL_NEW_CARD_E2E=NOT_YET_VERIFIED" in src
    assert "crm.item.update" not in src.lower()
    assert "tasks.task.add" not in src.lower()


def test_guarded_workflow_does_not_consume_brave_key_from_github_secrets():
    wf = Path(".github/workflows/serviceit-vps-brave-live-activate.yml").read_text("utf8")
    assert "workflow_dispatch:" in wf
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "ACTIVATE_VPS_BRAVE_SEARCH_CONTEXT_1062_21" in wf
    assert "serviceit-vps-native-shadow-readonly" in wf
    assert "sha256sum --check --status" in wf
    assert "persist-credentials: false" in wf
    assert "SERVICEIT_BRAVE_SEARCH_API_KEY" not in wf
    assert "BRAVE_API_KEY" not in wf
    assert "docker compose up" not in wf
    assert "crm.item.update" not in wf
    assert "brave-live-activate.py" in wf


def test_existing_worker_keeps_three_queries_max_and_optional_fallback():
    worker = Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    company = Path("leadpilot/serviceit_independent_company_research.py").read_text("utf8")
    assert 'secret_file(\n        env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True)' in worker
    assert "MAX_SEARCH_CALLS = 3" in company
    assert "query_indices=(0, 2)" in company
    assert "context_candidates(" in company
    assert "FREE_GDELT_NEWS_ONLY" in company
    assert '"SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED"] = "YES"' in worker


def test_brave_activator_checks_overlay_scoping_without_using_secret_values(monkeypatch):
    module_path = Path("deploy/serviceit-vps-brave-live-activate.py")
    spec = importlib.util.spec_from_file_location("vps_brave_activation", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    overlay = {
        "services": {
            "coordinator": {
                "environment": {
                    "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE": "/run/secrets/serviceit_brave_search_key"
                },
                "secrets": [{"source": "serviceit_pg_password"}, {"source": "serviceit_brave_search_key"}],
            },
            "business": {"secrets": []},
            "observer": {"secrets": []},
            "auxiliaries": {"secrets": []},
        },
        "secrets": {"serviceit_brave_search_key": {
            "file": "/etc/serviceit/secrets/brave_api_key"
        }},
    }
    class Result:
        stdout = json.dumps(overlay)
    monkeypatch.setattr(mod, "compose", lambda *args: Result())
    mod.assert_isolated_brave_overlay((Path("base"), Path("overlay")), Path("vps.env"))
    overlay["services"]["business"]["secrets"].append({"source": "serviceit_brave_search_key"})
    try:
        mod.assert_isolated_brave_overlay((Path("base"), Path("overlay")), Path("vps.env"))
    except mod.Blocked as e:
        assert str(e) == "BRAVE_SECRET_NOT_COORDINATOR_ONLY"
    else:
        raise AssertionError("Brave secret leaked into another worker")
