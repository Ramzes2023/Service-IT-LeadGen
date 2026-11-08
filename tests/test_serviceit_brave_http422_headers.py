"""Brave Search 422 hardening: required Cache-Control and safe diagnostics only."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_brave_connection_readonly import _brave

KEY = "SYNTHETIC-ONLY-NOT-REAL-API-KEY"


@pytest.mark.parametrize("context_mode", [False, True])
def test_brave_auth_emits_explicit_cache_control_no_cache(context_mode):
    seen = []
    def response(request):
        seen.append(request)
        assert request.headers["Cache-Control"] == "no-cache"
        assert request.headers["X-Subscription-Token"] == KEY
        assert request.url.host == "api.search.brave.com"
        if context_mode:
            return httpx.Response(200, json={
                "grounding": {"generic": []}, "sources": {},
            })
        return httpx.Response(200, json={"web": {"results": []}})
    with httpx.Client(transport=httpx.MockTransport(response),
                      trust_env=False) as client:
        status = _brave(client, KEY, context_mode=context_mode)
    assert len(seen) == 1
    assert status == (
        "BRAVE_CONTEXT_API_200_VALID_GROUNDING" if context_mode
        else "BRAVE_API_200_VALID_WEB_RESULTS"
    )


@pytest.mark.parametrize("location,safe", [
    (["header", "cache-control"], "BRAVE_VALIDATION_CACHE_CONTROL_422"),
    (["query", "country"], "BRAVE_VALIDATION_COUNTRY_422"),
    (["query", "search_lang"], "BRAVE_VALIDATION_SEARCH_LANG_422"),
    (["query", "q"], "BRAVE_PLAN_OR_QUERY_REJECTED_422"),
])
def test_brave_422_never_logs_provider_text_or_token(location, safe):
    def response(request):
        return httpx.Response(422, json={"error": {
            "code": "VALIDATION", "detail": "DO_NOT_REVEAL_SECRET_URL_TOKEN",
            "meta": {"errors": [{"loc": location, "msg": "DO_NOT_REVEAL_SECRET_URL_TOKEN"}]},
        }})
    with httpx.Client(transport=httpx.MockTransport(response),
                      trust_env=False) as client:
        status = _brave(client, KEY)
    assert status == safe
    assert KEY not in status and "DO_NOT_REVEAL" not in status


def test_brave_production_web_and_context_paths_both_use_no_cache():
    root = Path("leadpilot")
    paths = [
        root / "serviceit_brave_connection_readonly.py",
        root / "serviceit_independent_company_research.py",
        root / "serviceit_brave_llm_context.py",
    ]
    for path in paths:
        text = path.read_text("utf8")
        assert '"Cache-Control": "no-cache"' in text
        assert '"X-Subscription-Token":' in text
    assert "MAX_SEARCH_CALLS = 3" in paths[1].read_text("utf8")


def test_activator_never_prints_untrusted_brave_body_on_failure():
    path = Path("deploy/serviceit-vps-brave-live-activate.py")
    spec = importlib.util.spec_from_file_location("brave_safe_status_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = json.dumps({
        "web": "BRAVE_VALIDATION_CACHE_CONTROL_422",
        "context": "NOT_CALLED",
        "raw_token": "DO_NOT_SHOW",
    })
    assert module.safe_probe_labels(raw) == (
        "BRAVE_VALIDATION_CACHE_CONTROL_422", "NOT_CALLED")
    assert module.safe_probe_labels('{"web":"DO_NOT_SHOW","context":"UNKNOWN"}') == (
        "UNVERIFIED", "UNVERIFIED")
    assert module.safe_probe_labels("not-json") == ("UNVERIFIED", "UNVERIFIED")
    assert module.safe_probe_labels("X"*1601) == ("UNVERIFIED", "UNVERIFIED")
    text = path.read_text("utf8")
    assert "BRAVE_ROLLBACK=NO_LIVE_CONTAINER_CHANGED" in text
    assert "BRAVE_CONTEXT_STATUS=" in text
    assert "BRAVE_WEB_STATUS=" in text
    assert "print(test.stdout)" not in text
    assert "print(test.stderr)" not in text
