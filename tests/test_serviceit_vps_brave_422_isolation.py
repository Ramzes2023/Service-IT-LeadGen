"""Offline Brave full-vs-minimal HTTP 422 isolation and no-LIVE-mutation checks."""
from pathlib import Path

import httpx
import pytest

from leadpilot import serviceit_vps_brave_422_isolation as diag


def fake_secret(monkeypatch):
    monkeypatch.setattr(diag, "secret_file",
                        lambda env, name: "SYNTHETIC_BRAVE_KEY_DO_NOT_LOG_123")


def test_full_422_minimal_200_and_safe_location_only(monkeypatch):
    fake_secret(monkeypatch)
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.host == "api.search.brave.com"
        assert request.headers["Cache-Control"] == "no-cache"
        assert request.headers["Accept-Encoding"] == "gzip"
        assert request.headers["X-Subscription-Token"].startswith("SYNTHETIC_BRAVE_")
        if request.url.params.get("country") == "RU":
            assert request.url.params["search_lang"] == "ru"
            return httpx.Response(422, json={
                "error": {"code": "VALIDATION",
                          "detail": "SECRET_MESSAGE_DO_NOT_LOG",
                          "meta": {"errors": [
                              {"loc": ["query", "country"],
                               "msg": "PRIVATE_PROVIDER_TEXT_SHOULD_NEVER_BE_LOGGED"}
                          ]}}
            })
        assert set(request.url.params) == {"q"}
        return httpx.Response(200, json={"web": {"results": []}})
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        result = diag.inspect({}, client)
    assert len(calls) == 2
    assert result["full"] == {
        "status": "HTTP_422", "code": "VALIDATION", "field": "QUERY_COUNTRY"}
    assert result["minimal"] == {
        "status": "HTTP_200", "code": "NONE", "field": "NONE"}
    assert result["requests_max"] == 2
    assert result["crm_writes"] == result["model_calls"] == 0
    assert "SECRET_MESSAGE" not in str(result)
    assert "SYNTHETIC_BRAVE_KEY" not in str(result)


def test_full_200_makes_no_extra_requests(monkeypatch):
    fake_secret(monkeypatch)
    hits = []
    def handle(request):
        hits.append(request)
        return httpx.Response(200, json={"web": {"results": []}})
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        result = diag.inspect({}, client)
    assert len(hits) == 1
    assert result["minimal"]["status"] == "NOT_CALLED"
    assert result["requests_max"] == 1


def test_full_429_does_not_retry_and_never_touches_minimal(monkeypatch):
    fake_secret(monkeypatch)
    count = []
    def handle(request):
        count.append(1)
        return httpx.Response(429, json={"error": "RATE_LIMITED"})
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        result = diag.inspect({}, client)
    assert count == [1]
    assert result["full"]["status"] == "HTTP_429"
    assert result["minimal"]["status"] == "NOT_CALLED"


def test_unknown_private_error_location_and_code_hidden(monkeypatch):
    fake_secret(monkeypatch)
    def handle(request):
        return httpx.Response(422, json={"error": {
            "code": "PRIVATE_SECRET_VALUE",
            "meta": {"errors": [
                {"loc": ["header", "authorization-token-secret"], "msg": "secret"},
            ]},
        }})
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        result = diag.inspect({}, client)
    assert result["full"]["code"] == "UNKNOWN"
    assert result["full"]["field"] == "UNKNOWN"
    assert result["minimal"]["status"] == "HTTP_422"
    assert "PRIVATE_SECRET" not in str(result)


def test_key_missing_causes_zero_vendor_requests(monkeypatch):
    monkeypatch.setattr(diag, "secret_file", lambda *a, **kw: (_ for _ in ()).throw(
        diag.VpsBlocked("SECRET_MISSING")))
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail("EXTERNAL_REQUEST_FORBIDDEN")), trust_env=False) as client:
        result = diag.inspect({}, client)
    assert result["status"] == "SECRET_NOT_ACCESSIBLE"
    assert result["requests_max"] == 0


def test_owner_only_readonly_workflow_runs_on_vps_without_mutation():
    wf = Path(".github/workflows/serviceit-vps-brave-422-isolation.yml").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.ref == 'refs/heads/main'" in wf
    assert "DIAGNOSE_BRAVE_422_FULL_VS_MINIMAL_2_CALLS" in wf
    assert "serviceit-vps-native-shadow-readonly" in wf
    assert "persist-credentials: false" in wf
    assert "sha256(data).hexdigest() != digest" in wf
    assert '"run", "--rm", "--no-deps", "--pull", "never"' in wf
    assert '"-T", "coordinator", "python", "-"' in wf
    assert "NATIVE_FOUR_HEALTHY=" in wf
    assert "CHROMIUM_HEALTHY=" in wf
    assert "CRM_WRITES=0;EXISTING_CONTAINERS_RESTARTED=0" in wf
    assert "BRAVE_FULL_ERROR_FIELD=" in wf.replace('" + label.upper() + "', "FULL")
    assert "BRAVE_REQUESTS_MAX=" in wf


def test_vps_minimal_request_matches_successful_windows_brave_query():
    # Windows returned HTTP 200 for /web/search?q=brave%20search with
    # Accept, Accept-Encoding=gzip and Cache-Control=no-cache headers.
    assert diag.QUERY == "brave search"
    assert diag.MINIMAL == {"q": "brave search"}
    text = Path("leadpilot/serviceit_vps_brave_422_isolation.py").read_text("utf8")
    assert '"Accept-Encoding": "gzip"' in text
    assert '"Cache-Control": "no-cache"' in text
    assert "REAL_NEW_CARD_E2E=NOT_YET_VERIFIED" in wf
    for forbidden in ("docker compose up", '"build", "coordinator"',
                      "crm.item.update", "tasks.task.add",
                      "SERVICEIT_BRAVE_SEARCH_API_KEY: ${{",
                      "secrets.SERVICEIT_BRAVE_SEARCH_API_KEY",
                      "print(outcome.stderr)", "print(outcome.stdout)"):
        assert forbidden not in wf


def test_failed_brave_probe_no_longer_attempts_old_image_retag():
    src = Path("deploy/serviceit-vps-brave-live-activate.py").read_text("utf8")
    cutover = src.index("if changed:", src.index('print("BRAVE_VPS_ACTIVATION=BLOCKED_ROLLBACK"'))
    retag = src.index('call("docker", "tag", old_image', cutover)
    unchanged = src.index('BRAVE_ROLLBACK=NO_LIVE_CONTAINER_CHANGED', cutover)
    assert cutover < retag < unchanged
    assert 'core_healthy()' in src[retag:unchanged]
