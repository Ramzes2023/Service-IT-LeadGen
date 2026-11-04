"""No-network regression for multi-card scheduled AI catch-up."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import httpx
from cryptography.fernet import Fernet
import pytest

from leadpilot import serviceit_bitrix_batch_worker as batch
from leadpilot.serviceit_bitrix_dispatch_worker import (
    LIVE_FLAG, REPOSITORY, verify_event,
)
from leadpilot.serviceit_bitrix_poll_fallback import POLL_FLAG, PollBlocked

NOW = datetime(2026, 11, 4, 19, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=1)
KEY = "test-batch-hmac-long-secret-2026-11-04-only-abcdef"


def environment(**extra):
    result = {
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "SERVICEIT_BITRIX_POLL_ENABLED": POLL_FLAG,
        "SERVICEIT_BITRIX_EVENT_AI_ENABLED": LIVE_FLAG,
        "SERVICEIT_BITRIX_POLL_START_UTC": START.isoformat(),
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETIC123456",
        "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY": KEY,
        "SERVICEIT_GH_LEDGER_KEY": Fernet.generate_key().decode(),
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO",
        "GITHUB_TOKEN": "synthetic_github_token_123456",
    }
    result.update(extra)
    return result


def item(card):
    return {"id": card, "categoryId": 21,
            "createdTime": (NOW - timedelta(minutes=4)).isoformat()}


def service(ids=(201, 202, 203, 204), *, bad=False):
    calls = []

    def handler(req):
        assert req.method == "POST"
        assert req.url.host == "serviseit.bitrix24.ru"
        assert req.url.path.endswith("/crm.item.list.json")
        params = json.loads(req.content)
        calls.append(params)
        if params["filter"] == {"id": 3}:
            data = {"items": [dict(item(3), createdTime=(NOW - timedelta(days=10)).isoformat())],
                    "total": 1}
        else:
            data = {"items": [item(i) for i in ids], "total": len(ids)}
            if bad:
                data["items"] = [[item(201)]]
        return httpx.Response(200, json={"result": data})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_four_actual_future_cards_are_processed_in_one_batch_without_fake_items(monkeypatch):
    processed = []

    def fake_exact(event, env, *, client):
        card = verify_event(event, KEY, now=event["client_payload"]["timestamp"])
        assert env["GITHUB_EVENT_NAME"] == "repository_dispatch"
        assert env["SERVICEIT_BITRIX_EVENT_AI_ENABLED"] == LIVE_FLAG
        processed.append(card)
        return {"status": "SERVICEIT_NEW_CARD_EVENT_PROCESSED",
                "crm_writes": 1, "result": "HOLD_9_WRITTEN"}

    monkeypatch.setattr(batch, "run_exact_event", fake_exact)
    monkeypatch.setattr(batch, "encrypted_receipt_exists",
                        lambda client, card, token, key: False)
    client, calls = service()
    with client:
        result = batch.run_batch(environment(), client=client, now=NOW)
    assert processed == [201, 202, 203, 204]
    assert result == {
        "status": "BATCH_ALL_NEW_PROCESSED", "candidates": 4,
        "attempted": 4, "already_fenced": 0, "processed": 4,
        "needs_review": 0, "crm_writes": 4, "pending": False}
    assert calls[0]["filter"] == {"id": 3}
    assert calls[1]["filter"]["categoryId"] == 21


def test_durable_receipt_skips_duplicates_and_limit_drains_in_later_run(monkeypatch):
    seen = []
    monkeypatch.setattr(batch, "encrypted_receipt_exists",
                        lambda client, card, token, key: card == 201)
    def fake(event, env, *, client):
        seen.append(event["client_payload"]["item_id"])
        return {"status": "SERVICEIT_NEW_CARD_EVENT_PROCESSED",
                "crm_writes": 1}
    monkeypatch.setattr(batch, "run_exact_event", fake)
    client, _ = service()
    with client:
        result = batch.run_batch(environment(), client=client, now=NOW, limit=2)
    assert seen == [202, 203]
    assert result["already_fenced"] == 1
    assert result["crm_writes"] == 2
    assert result["pending"] is True
    assert result["status"] == "BATCH_PENDING_MORE"


def test_uncertain_result_halts_next_card_instead_of_claiming_full_success(monkeypatch):
    seen = []
    monkeypatch.setattr(batch, "encrypted_receipt_exists", lambda *args: False)
    def fake(event, env, *, client):
        seen.append(event["client_payload"]["item_id"])
        return {"status": "SERVICEIT_NEW_CARD_NEEDS_REVIEW", "crm_writes": "UNVERIFIED"}
    monkeypatch.setattr(batch, "run_exact_event", fake)
    client, _ = service()
    with client:
        result = batch.run_batch(environment(), client=client, now=NOW)
    assert seen == [201]
    assert result["status"] == "BATCH_REVIEW_REQUIRED"
    assert result["pending"] is True
    assert result["crm_writes"] == 0
    assert result["needs_review"] == 1


@pytest.mark.parametrize("updates", [
    {"GITHUB_EVENT_NAME": "issue_comment"},
    {"GITHUB_REPOSITORY": "attacker/repo"},
    {"GITHUB_REF": "refs/heads/demo"},
    {"SERVICEIT_BITRIX_EVENT_AI_ENABLED": "NO"},
    {"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES", "AI_ENABLE": "0"},
])
def test_invalid_owner_scopes_stop_before_any_http_or_ai(updates):
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail("unauthorized network request"))) as client:
        with pytest.raises(batch.BatchBlocked):
            batch.run_batch(environment(**updates), client=client, now=NOW)


def test_disabled_does_not_read_or_write_crm():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail("disabled network request"))) as client:
        result = batch.run_batch(
            environment(SERVICEIT_BITRIX_POLL_ENABLED="NO"),
            client=client, now=NOW)
    assert result["status"] == "BATCH_DISABLED"


def test_untrusted_nested_listing_rejected_before_fence_or_crm_write(monkeypatch):
    monkeypatch.setattr(batch, "run_exact_event",
                        lambda *a, **k: pytest.fail("unexpected live worker"))
    monkeypatch.setattr(batch, "encrypted_receipt_exists",
                        lambda *a: pytest.fail("unexpected ledger mutation"))
    client, calls = service(bad=True)
    with client:
        with pytest.raises(PollBlocked, match="POLL_ITEM_INVALID"):
            batch.run_batch(environment(), client=client, now=NOW)
    assert len(calls) == 2


def test_workflow_is_guarded_and_never_creates_demo_cards():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/serviceit-bitrix-all-new-batch-ai.yml"
    content = path.read_text(encoding="utf-8")
    assert "github.event_name == 'schedule'" in content
    assert "SERVICEIT_BITRIX_POLL_ENABLED" in content
    assert "serviceit-bitrix-new-card-free-event" in content
    assert "python -m leadpilot.serviceit_bitrix_batch_worker" in content
    assert "crm.item.add" not in content
    assert "SERVICEIT_CORPORATE_APPROVALS_JSON" in content


def test_exact_owner_action_runs_real_filtered_batch_without_creating_cards(monkeypatch, tmp_path):
    """Owner comment is only authorization to process already-existing new IDs."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({
        "action": "created",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": "Ramzes2023"},
        "issue": {"number": 74},
        "comment": {
            "body": batch.BATCH_OWNER_MARKER,
            "user": {"login": "Ramzes2023"},
        },
    }), encoding="utf-8")
    processed = []
    monkeypatch.setattr(batch, "encrypted_receipt_exists", lambda *a: False)
    def process_existing(event, env, *, client):
        processed.append(event["client_payload"]["item_id"])
        return {"status": "SERVICEIT_NEW_CARD_EVENT_PROCESSED", "crm_writes": 1}
    monkeypatch.setattr(batch, "run_exact_event", process_existing)
    env = environment(GITHUB_EVENT_NAME="issue_comment",
                      GITHUB_ACTOR="Ramzes2023",
                      GITHUB_EVENT_PATH=str(event_file))
    client, calls = service((201, 202))
    with client:
        outcome = batch.run_batch(env, client=client, now=NOW)
    assert outcome["processed"] == 2
    assert processed == [201, 202]
    assert len(calls) == 2  # historical positive control + real future-only list


@pytest.mark.parametrize("change", [
    lambda e: e.update(GITHUB_ACTOR="attacker"),
    lambda e: e.update(GITHUB_EVENT_PATH=""),
])
def test_owner_command_requires_exact_identity_and_event_file(change):
    env = environment(GITHUB_EVENT_NAME="issue_comment")
    change(env)
    with httpx.Client(transport=httpx.MockTransport(
            lambda r: pytest.fail("unauthorized network call"))) as client:
        with pytest.raises(batch.BatchBlocked, match="SCOPE_INVALID"):
            batch.run_batch(env, client=client, now=NOW)
