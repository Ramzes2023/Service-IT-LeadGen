"""Offline synthetic tests for free source-agnostic Bitrix24 event -> CRM manager."""
import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
import time

import httpx
from cryptography.fernet import Fernet
import pytest

from leadpilot import serviceit_bitrix_dispatch_worker as worker

SECRET = "SYNTHETIC-SERVICEIT-BRIDGE-HMAC-123456789012345678"
TOKEN = "SYNTHETIC-GITHUB-ACCESS-TOKEN-NOT-REAL"
PORTAL = "serviseit.bitrix24.ru"


def event(card=107, *, secret=SECRET, now=None):
    timestamp = int(time.time()) if now is None else now
    nonce = "ca14e6b2-2fc0-4a71-a751-b5a540a2266a"
    msg = f"v1|{PORTAL}|1062|{card}|{timestamp}|{nonce}"
    sign = "sha256=" + hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "action": worker.EVENT,
        "repository": {"full_name": worker.REPOSITORY},
        "client_payload": {
            "version": 1, "portal": PORTAL, "entity_type_id": 1062,
            "item_id": card, "timestamp": timestamp, "nonce": nonce,
            "signature": sign,
        },
    }


def settings(**kwargs):
    return {
        "SERVICEIT_BITRIX_EVENT_AI_ENABLED": worker.LIVE_FLAG,
        "GITHUB_REPOSITORY": worker.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "repository_dispatch",
        "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY": SECRET,
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETIC123456",
        "SERVICEIT_GH_LEDGER_KEY": Fernet.generate_key().decode(),
        "GITHUB_TOKEN": TOKEN,
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO",
        **kwargs,
    }


@pytest.mark.parametrize("change,reason", [
    (lambda e: e["repository"].update(full_name="attacker/repo"), "REPOSITORY_INVALID"),
    (lambda e: e.update(action="other"), "EVENT_TYPE_INVALID"),
    (lambda e: e["client_payload"].update(portal="elsewhere.bitrix24.ru"), "EVENT_SCOPE_INVALID"),
    (lambda e: e["client_payload"].update(entity_type_id=1044), "EVENT_SCOPE_INVALID"),
    (lambda e: e["client_payload"].update(item_id=7), "EVENT_CARD_ID_INVALID"),
    (lambda e: e["client_payload"].update(item_id="107"), "EVENT_CARD_ID_INVALID"),
    (lambda e: e["client_payload"].update(signature="sha256=" + "0" * 64), "EVENT_HMAC_MISMATCH"),
])
def test_reject_untrusted_or_out_of_scope_event(change, reason):
    raw = event()
    change(raw)
    with pytest.raises(worker.EventCloudBlocked, match=reason):
        worker.verify_event(raw, SECRET)


def test_reject_stale_and_missing_hmac_key():
    old = int(time.time()) - 3600
    with pytest.raises(worker.EventCloudBlocked, match="EVENT_EXPIRED"):
        worker.verify_event(event(now=old), SECRET)
    with pytest.raises(worker.EventCloudBlocked, match="EVENT_HMAC_SECRET_MISSING"):
        worker.verify_event(event(), "weak")


def test_fence_encrypts_once_and_never_replays_after_remote_200():
    requests = []
    stored = {}
    key = Fernet.generate_key().decode()
    def github(request):
        requests.append(request.method)
        assert request.url.host == "api.github.com"
        # HMAC hex may coincidentally contain the digits 107; only a raw\n        # sequential item ID as a URL path segment would leak identity.\n        tail = request.url.path.rsplit("/", 1)[-1]\n        assert re.fullmatch(r"[0-9a-f]{64}\\.enc", tail) is not None\n        assert "/107/" not in request.url.path and not tail.startswith("107.")
        if request.method == "GET":
            if stored:
                return httpx.Response(200, json={"type": "file",
                                                 "sha": stored["sha"]})
            return httpx.Response(404)
        body = json.loads(request.content)
        assert body["branch"] == worker.BRANCH
        raw = base64.b64decode(body["content"])
        assert b"107" not in raw
        # Ciphertext alone lives on the public GitHub branch.
        parsed = json.loads(Fernet(key.encode()).decrypt(raw))
        assert parsed["card"] == 107
        assert all(term not in json.dumps(parsed)
                   for term in ("company", "phone", "email", "url", "inn"))
        if request.method == "PUT" and "sha" not in body:
            stored["sha"] = "single-use-github-blob-sha"
            return httpx.Response(201, json={"content": {"sha": stored["sha"]}})
        return httpx.Response(200, json={"content": {"sha": "updated-sha"}})
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        fence = worker.EncryptedEventFence(client, TOKEN, key)
        assert fence.reserve(107) == "RESERVED"
        assert fence.finalize(107, "HOLD_9_WRITTEN") is True
        assert fence.reserve(107) == "ALREADY_FENCED_NO_RETRY"
    assert requests == ["GET", "PUT", "PUT", "GET"]


def test_remote_error_fails_closed_without_rest_or_model(monkeypatch):
    requests = []
    def github(request):
        requests.append(request.method)
        return httpx.Response(503)
    def no_model_or_crm(*args, **kwargs):
        pytest.fail("No provider/Bitrix calls when remote fence is uncertain")
    monkeypatch.setattr(worker, "preliminary_once", no_model_or_crm)
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        with pytest.raises(worker.EventCloudBlocked, match="FENCE_REMOTE_STATE_UNVERIFIED"):
            worker.run(event(), settings(), client=client)
    assert requests == ["GET"]


def test_event_drives_exact_id_worker_only_after_encrypted_reservation(monkeypatch):
    called = []
    key = Fernet.generate_key().decode()
    stored = {}
    def github(request):
        called.append(request.method)
        if request.method == "GET":
            return httpx.Response(404) if not stored else httpx.Response(200)
        body = json.loads(request.content)
        if "sha" not in body:
            stored["sha"] = "sha123"
            return httpx.Response(201, json={"content": {"sha": "sha123"}})
        return httpx.Response(200, json={"content": {"sha": "sha124"}})
    def prelim(store, rest, item_id, **kwargs):
        assert item_id == 107
        assert store.inspect()[0]["item_id"] == 107
        assert called[:2] == ["GET", "PUT"]
        called.append("CRM_EXACT_READ_WRITE")
        return "HOLD_9_WRITTEN"
    monkeypatch.setattr(worker, "preliminary_once", prelim)
    env = settings(SERVICEIT_GH_LEDGER_KEY=key)
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        out = worker.run(event(), env, client=client)
    assert out["status"] == "SERVICEIT_NEW_CARD_EVENT_PROCESSED"
    assert out["result"] == "HOLD_9_WRITTEN"
    assert out["crm_writes"] == 1
    assert out["qualified_leads"] == 0
    assert called == ["GET", "PUT", "CRM_EXACT_READ_WRITE", "PUT"]


def test_opt_out_skips_preliminary_worker_without_external_side_effects():
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network blocked"))) as c:
        assert worker.run(event(), {"SERVICEIT_BITRIX_EVENT_AI_ENABLED": "NO"}, client=c) == {
            "status": "DISABLED", "crm_writes": 0}


def test_no_legacy_crm_list_polling_or_source_ingestion():
    code = Path("leadpilot/serviceit_bitrix_dispatch_worker.py").read_text("utf-8")
    workflow = Path(".github/workflows/serviceit-bitrix-new-card-ai.yml").read_text("utf-8")
    assert "crm.item.list" not in code
    assert "b2b_github_to_bitrix" not in code
    assert "repository_dispatch:" in workflow
    assert "serviceit_bitrix_item_add_v1" in workflow
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED" in workflow
    assert "SERVICEIT_BITRIX_WEBHOOK" in workflow
    assert "SERVICEIT_GH_LEDGER_KEY" in workflow


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.parametrize("approved", [False, True])
def test_signed_event_end_to_end_and_readonly_recovery(monkeypatch, ambiguous, approved):
    from types import SimpleNamespace
    from test_serviceit_enrichment_worker import CRM, approval, model
    crm = CRM()
    if ambiguous:
        crm.fail = TimeoutError("synthetic write acknowledgement lost")
    monkeypatch.setattr(worker, "Rest", lambda *args: crm)
    key = Fernet.generate_key().decode()
    saved = {}
    def github(request):
        if request.method == "GET":
            return httpx.Response(200, json=saved) if saved else httpx.Response(404)
        body = json.loads(request.content)
        code = 200 if "sha" in body else 201
        saved.update(content=body["content"], sha="checkpoint-sha")
        return httpx.Response(code, json={"content": {"sha": "checkpoint-sha"}})
    env = settings(SERVICEIT_GH_LEDGER_KEY=key)
    model_calls = []
    if approved:
        class Provider:
            config = SimpleNamespace(model="GigaChat", provider="gigachat")
            def __init__(self, *args, **kwargs):
                pass
            def classify(self, text):
                model_calls.append(text)
                return model(text)
            def close(self):
                pass
        monkeypatch.setattr(worker, "AIProvider", Provider)
        env.update(SERVICEIT_CORPORATE_APPROVALS_JSON=json.dumps({"101": approval(crm)}),
                   SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED="YES", AI_ENABLE="1",
                   AI_PROVIDER="gigachat", GIGACHAT_CREDENTIALS="synthetic-not-real")
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        result = worker.run(event(101), env, client=client)
        assert result["result"] == ("UNCERTAIN" if ambiguous else
                                    "VERIFIED" if approved else "HOLD_9_WRITTEN")
        result = worker.run(event(101), env, client=client)
        assert result == {"status": "EXACT_TARGET_READBACK", "crm_writes": 0}
    assert crm.writes == 1
    assert len(model_calls) == int(approved)
    assert all(crm.item[k] for k in worker.AI_FIELDS)
    assert not any(method == "crm.item.list" for method, _ in crm.calls)


def test_checkpoint_must_be_acknowledged_before_crm_update():
    key = Fernet.generate_key().decode()
    def github(request):
        if request.method == "GET":
            return httpx.Response(404)
        if "sha" not in json.loads(request.content):
            return httpx.Response(201, json={"content": {"sha": "reserved"}})
        return httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        fence = worker.EncryptedEventFence(client, TOKEN, key)
        assert fence.reserve(101) == "RESERVED"
        with pytest.raises(worker.EventCloudBlocked, match="WRITE_CHECKPOINT_UNVERIFIED"):
            fence.checkpoint_write(101, {k: "synthetic" for k in worker.AI_FIELDS})


def test_manual_recovery_missing_receipt_never_writes():
    methods = []
    def github(request):
        methods.append(request.method)
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(github)) as client:
        fence = worker.EncryptedEventFence(client, TOKEN, Fernet.generate_key().decode())
        with pytest.raises(worker.EventCloudBlocked, match="RECOVERY_RECEIPT_NOT_FOUND"):
            fence.observe(101, lambda *args: pytest.fail("CRM must not be called"))
    assert methods == ["GET"]
