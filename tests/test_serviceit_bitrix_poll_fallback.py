"""No webhook-admin permission polling fallback, entirely offline synthetic tests."""
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from leadpilot.serviceit_bitrix_poll_fallback import (
    PollBlocked, after_activation, build_query, date_value, discover,
    dispatch, run, signed_payload, POLL_FLAG,
)
from leadpilot.serviceit_bitrix_dispatch_worker import (
    EVENT, LIVE_FLAG, REPOSITORY, verify_event,
)

NOW = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)
START = NOW - timedelta(minutes=20)
SECRET = "correct-hmac-secret-for-tests-only-123456789"
WEBHOOK = "https://serviseit.bitrix24.ru/rest/789/authorized_dummy_token_12345"
TOKEN = "synthetic_github_token_123456"


def card(n=101, when=None, category=21):
    when = when or (NOW - timedelta(minutes=1))
    return {"id": n, "categoryId": category, "createdTime": when.isoformat()}


def fake_env(**updates):
    data = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "schedule",
        "SERVICEIT_BITRIX_POLL_ENABLED": POLL_FLAG,
        "SERVICEIT_BITRIX_EVENT_AI_ENABLED": LIVE_FLAG,
        "SERVICEIT_BITRIX_POLL_START_UTC": START.isoformat(),
        "SERVICEIT_BITRIX_WEBHOOK": WEBHOOK,
        "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY": SECRET,
        "GITHUB_TOKEN": TOKEN,
    }
    data.update(updates)
    return data


def test_time_cursor_requires_future_card_after_owner_enables():
    assert after_activation(fake_env(), NOW) == START
    assert after_activation(fake_env(SERVICEIT_BITRIX_POLL_START_UTC=
                                   (NOW - timedelta(hours=3)).isoformat()), NOW) == NOW - timedelta(hours=2)
    with pytest.raises(PollBlocked, match="ACTIVATION_TIMESTAMP"):
        after_activation(fake_env(SERVICEIT_BITRIX_POLL_START_UTC=
                                  (NOW + timedelta(seconds=1)).isoformat()), NOW)
    assert build_query(START, 0)["useOriginalUfNames"] == "N"
    assert build_query(START, 0)["select"] == ["id", "categoryId", "createdTime"]
    assert build_query(START, 0)["order"] == {"id": "ASC"}
    assert build_query(START, 0)["filter"]["categoryId"] == 21
    assert build_query(START, 0)["filter"][">=createdTime"].startswith("2026-11-03")


def test_list_strict_pagination_produces_only_ids():
    calls = []

    def rest(method, params):
        assert method == "crm.item.list"
        calls.append(params["start"])
        if len(calls) == 1:
            return {"items": [card(i) for i in range(101, 151)], "next": 50, "total": 51}
        return {"items": [card(151)], "total": 51}

    assert discover(rest, START, NOW) == list(range(101, 152))
    assert calls == [0, 50]


@pytest.mark.parametrize("response,error", [
    ({"items": [[card()]]}, "POLL_ITEM_INVALID"),
    ({"items": [card(category=999)]}, "POLL_ITEM_SCOPE_MISMATCH"),
    ({"items": [card(101, NOW - timedelta(hours=1))]}, "POLL_ITEM_BEFORE_ACTIVATION"),
    ({"items": [card(101, NOW + timedelta(hours=1))]}, "POLL_ITEM_BEFORE_ACTIVATION"),
    ({"items": [card(101), card(101)]}, "POLL_ITEM_DUPLICATE"),
    ({"items": [], "total": 1}, "POLL_LIST_EMPTY_PAGE"),
    ({"items": [card()], "next": 0}, "POLL_NEXT_UNTRUSTED"),
    ({"items": "invalid"}, "POLL_LIST_SHAPE"),
])
def test_invalid_list_never_makes_any_dispatch(response, error):
    with pytest.raises(PollBlocked, match=error):
        discover(lambda m, p: response, START, NOW)


def test_hmac_signed_poll_event_is_accepted_by_exact_existing_worker():
    event = {"action": EVENT, "repository": {"full_name": REPOSITORY},
             "client_payload": signed_payload(101, SECRET, 1793725200)}
    assert verify_event(event, SECRET, now=1793725200) == 101


def test_gates_disallow_scheduler_when_owner_has_not_enabled_it():
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("no network allowed while disabled"))) as client:
        assert run(fake_env(SERVICEIT_BITRIX_POLL_ENABLED="NO"), client=client, now=NOW) == {
            "status": "POLL_DISABLED", "crm_writes": 0, "dispatches": 0}
        with pytest.raises(PollBlocked, match="POLL_AI_GATE_DISABLED"):
            run(fake_env(SERVICEIT_BITRIX_EVENT_AI_ENABLED="NO"), client=client, now=NOW)


def test_manual_probe_only_gets_metadata_no_github_dispatch_and_no_gate_needed():
    requested = []

    def handler(request):
        requested.append((request.method, request.url.path, json.loads(request.content)))
        assert request.url.host == "serviseit.bitrix24.ru"
        return httpx.Response(200, json={"result": {"items": []}, "total": 0})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run(fake_env(GITHUB_EVENT_NAME="workflow_dispatch",
                              SERVICEIT_BITRIX_POLL_ENABLED="NO",
                              SERVICEIT_BITRIX_EVENT_AI_ENABLED="NO"),
                     client=client, now=NOW, probe_only=True)
    assert result == {"status": "POLL_READONLY_LIST_PASS", "crm_writes": 0,
                      "dispatches": 0, "items_seen": 0}
    assert len(requested) == 1
    assert requested[0][0:2] == ("POST", "/rest/789/authorized_dummy_token_12345/crm.item.list.json")
    assert requested[0][2]["useOriginalUfNames"] == "N"
    assert requested[0][2]["select"] == ["id", "categoryId", "createdTime"]
    assert requested[0][2]["filter"]["categoryId"] == 21


def test_scheduled_poll_posts_signed_id_only_and_never_calls_crm_update():
    endpoints = []
    signed = []

    def handler(request):
        endpoints.append(request.url.host + request.url.path)
        if request.url.host == "serviseit.bitrix24.ru":
            return httpx.Response(200, json={"result": {"items": [card()]}, "total": 1})
        event = json.loads(request.content)
        assert event["event_type"] == EVENT
        assert set(event["client_payload"]) == {
            "version", "portal", "entity_type_id", "item_id", "timestamp", "nonce", "signature"}
        assert event["client_payload"]["item_id"] == 101
        signed.append(event["client_payload"])
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run(fake_env(), client=client, now=NOW)
    assert result == {"status": "POLL_DISPATCHED_ID_ONLY",
                      "crm_writes": 0, "dispatches": 1}
    assert len(endpoints) == 2
    assert all("crm.item.update" not in value for value in endpoints)
    assert signed[0]["portal"] == "serviseit.bitrix24.ru"


def test_network_or_permission_denial_propagates_without_dispatch():
    endpoints = []
    def handler(request):
        endpoints.append(request.url.path)
        return httpx.Response(403, json={"error": "ACCESS_DENIED"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PollBlocked, match="POLL_REST_HTTP_UNVERIFIED"):
            run(fake_env(), client=client, now=NOW)
    assert len(endpoints) == 1


def test_owner_powershell_safeguards_and_workflow_double_gate():
    from pathlib import Path
    ps = Path("scripts/ENABLE_SERVICEIT_BITRIX_POLL_FALLBACK.ps1").read_text("utf-8")
    workflow = Path(".github/workflows/serviceit-bitrix-poll-fallback.yml").read_text("utf-8")
    module = Path("leadpilot/serviceit_bitrix_poll_fallback.py").read_text("utf-8")
    assert "crm.item.list.json" in ps
    assert "BITRIX_LIST_READONLY=PASS" in ps
    assert "CORPORATE_CRM_AI_WRITE_APPROVAL_REQUIRED" not in ps
    assert "SERVICEIT_BITRIX_POLL_START_UTC" in ps
    assert "SERVICEIT_BITRIX_POLL_ENABLED' 'NO'" in ps
    assert "SERVICEIT_BITRIX_POLL_ENABLED' 'YES_1062_21_READONLY'" in ps
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'YES_1062_21_CLOUD'" in ps
    assert "SERVICEIT_POLL_FALLBACK=ACTIVATED_AFTER_READONLY_PROBE" in ps
    assert ps.index("SERVICEIT_BITRIX_LIST_READONLY=PASS") < ps.index(
        "SERVICEIT_BITRIX_POLL_ENABLED' 'YES_1062_21_READONLY'")
    assert "serviceit_bitrix_poll_fallback --probe" in workflow
    assert "SERVICEIT_BITRIX_POLL_ENABLED == 'YES_1062_21_READONLY'" in workflow
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED == 'YES_1062_21_CLOUD'" in workflow
    assert "cron:" in workflow
    assert "repository_dispatch" not in module or "dispatch" in module
    assert "crm.item.update" not in module



def test_live_n_mode_list_filter_is_consistent_with_existing_verified_readonly_probe():
    from pathlib import Path
    source = Path("leadpilot/serviceit_bitrix_poll_fallback.py").read_text("utf-8")
    old = Path("leadpilot/serviceit_b2b_list_shape_probe.py").read_text("utf-8")
    assert '"useOriginalUfNames": "N"' in source
    assert '"useOriginalUfNames": "Y"' not in source
    assert "CATEGORY_N_ID_ONLY" in old
    assert "CATEGORY_N_GT_3_ORDER" in old
    assert "CATEGORY_N_RECENT" in old
    ps = Path("scripts/ENABLE_SERVICEIT_BITRIX_POLL_FALLBACK.ps1").read_text("utf-8")
    assert "useOriginalUfNames = 'N'" in ps
    assert "useOriginalUfNames = 'Y'" not in ps


def test_conflicting_pagination_metadata_is_rejected_without_dispatch():
    env = fake_env(GITHUB_EVENT_NAME="workflow_dispatch")
    def handler(request):
        assert request.url.path.endswith("crm.item.list.json")
        return httpx.Response(200, json={
            "result": {"items": [], "total": 2},
            "total": 0,
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PollBlocked, match="POLL_PAGINATION_METADATA_CONFLICT"):
            run(env, client=client, now=NOW, probe_only=True)


def test_server_ignoring_created_time_filter_never_dispatches_old_card():
    def handler(request):
        if request.url.host == "serviseit.bitrix24.ru":
            return httpx.Response(200, json={
                "result": {"items": [card(101, START - timedelta(hours=1))]},
                "total": 1,
            })
        pytest.fail("no GitHub dispatch for old card")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PollBlocked, match="POLL_ITEM_BEFORE_ACTIVATION_OR_FUTURE"):
            run(fake_env(), client=client, now=NOW)


def test_empty_list_api_flat_shape_alone_does_not_prove_live_ai_activation():
    env = fake_env(GITHUB_EVENT_NAME="workflow_dispatch",
                   SERVICEIT_BITRIX_POLL_ENABLED="NO",
                   SERVICEIT_BITRIX_EVENT_AI_ENABLED="NO")
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"result":{"items":[]}, "total":0}))) as client:
        assert run(env, client=client, now=NOW, probe_only=True)["status"] == "POLL_READONLY_LIST_PASS"
    assert env["SERVICEIT_BITRIX_POLL_ENABLED"] == "NO"
    assert env["SERVICEIT_BITRIX_EVENT_AI_ENABLED"] == "NO"
