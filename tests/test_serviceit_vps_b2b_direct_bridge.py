"""Offline tests for direct Apps Script -> VPS signed source (never live CRM)."""
import hashlib
import hmac
import json
import time

import pytest

from leadpilot import serviceit_vps_b2b_bridge as bridge

KEY = "synthetic-b2b-hmac-key-never-production-0123456789"
SOURCE = "B2B-CENTER-1234567"
NOTICE = {
    "schema": "serviceit-b2b-notice-v1",
    "source": "B2B-Center",
    "email_message_id": "1a19b7630301bafb",
    "tender_id": "1234567",
    "source_url": "https://www.b2b-center.ru/market/example-public-1s/tender-1234567/",
    "subject": "Оказание услуг по сопровождению системы 1С",
    "organizer": "ООО ПРИМЕР",
    "buyer_inn": None,
    "fns_msp": None,
    "region": None,
    "original_publication_verified": False,
    "contact_approved": False,
    "qualified_lead": False,
}


def signed_notice(now=None):
    now = int(time.time()) if now is None else now
    payload_json = json.dumps({
        "version": 1,
        "mailbox": "ramzesisayev444@gmail.com",
        "email_message_id": NOTICE["email_message_id"],
        "dispatch_id": "synthetic-dispatch-000000001",
        "notice": NOTICE,
    }, separators=(",", ":"), ensure_ascii=False)
    timestamp = str(now)
    sig = "sha256=" + hmac.new(
        KEY.encode(), (timestamp + "." + payload_json).encode(), hashlib.sha256
    ).hexdigest()
    return {"timestamp": timestamp, "payload_json": payload_json,
            "signature": sig}


def test_signed_notice_requires_original_hmac_and_nonlead():
    notice, groups, event_id = bridge.signed_notice(
        signed_notice(), KEY)
    assert notice["tender_id"] == "1234567"
    assert groups == ["1C"]
    assert notice["qualified_lead"] is False
    assert notice["contact_approved"] is False
    assert event_id == "synthetic-dispatch-000000001"
    tampered = signed_notice()
    tampered["payload_json"] += " "
    with pytest.raises((bridge.IngestBlocked, bridge.B2BBlocked)):
        bridge.signed_notice(tampered, KEY)
    with pytest.raises((bridge.IngestBlocked, bridge.B2BBlocked)):
        bridge.signed_notice(signed_notice(now=int(time.time())-1600), KEY)


def test_signed_probe_rejects_mismatch_stale_and_weak_key():
    ts = str(int(time.time()))
    nonce = "0123456789abcdef-0123456789abcdef"
    sig = "sha256=" + hmac.new(
        KEY.encode(), ("v1|b2b-probe|" + ts + "|" + nonce).encode(),
        hashlib.sha256).hexdigest()
    payload = {"timestamp": ts, "nonce": nonce, "signature": sig}
    assert bridge.verified_probe(payload, KEY) is True
    with pytest.raises(bridge.B2BBlocked):
        bridge.verified_probe(payload, KEY + "fake")
    with pytest.raises(bridge.B2BBlocked):
        bridge.verified_probe(payload, KEY, now=int(ts)+360)
    with pytest.raises(bridge.B2BBlocked):
        bridge.verified_probe(payload, "bad")


def test_source_flags_fail_closed(monkeypatch):
    assert bridge.enabled({}) is False
    with pytest.raises(bridge.B2BBlocked):
        bridge.enabled({"SERVICEIT_VPS_B2B_RECEIVER_ENABLED": "YES"})
    flags = {
        "SERVICEIT_VPS_B2B_RECEIVER_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_B2B_WRITER_ENABLED": "NO",
    }
    assert bridge.enabled(flags) is True
    with pytest.raises(bridge.B2BBlocked):
        bridge.enabled(flags, writer=True)
    flags["SERVICEIT_VPS_B2B_WRITER_ENABLED"] = "YES"
    monkeypatch.setattr(bridge, "live_permitted", lambda e: True)
    assert bridge.enabled(flags, writer=True)


def test_exact_existing_source_never_posts(monkeypatch):
    monkeypatch.setattr(bridge, "attempted_24h", lambda con: 0)
    monkeypatch.setattr(bridge, "read_candidate", lambda con: (SOURCE, NOTICE))
    monkeypatch.setattr(bridge, "validate_webhook", lambda x: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda client, base: True)
    monkeypatch.setattr(bridge, "exact_duplicate",
        lambda client, base, source: [dict(id=9)])
    monkeypatch.setattr(bridge, "set_existing", lambda con, source: True)
    monkeypatch.setattr(bridge, "bitrix_call",
                        lambda *args: pytest.fail("no CRM write on duplicates"))
    assert bridge.deliver_one(object(),None,"secret") == "EXISTING"


def test_one_real_hold_requires_pg_reservation_before_crm_add(monkeypatch):
    called = []
    monkeypatch.setattr(bridge, "attempted_24h", lambda con: 0)
    monkeypatch.setattr(bridge, "read_candidate", lambda con: (SOURCE, NOTICE))
    monkeypatch.setattr(bridge, "validate_webhook", lambda x: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda client, base: True)
    monkeypatch.setattr(bridge, "exact_duplicate",
        lambda client, base, source: [])
    monkeypatch.setattr(bridge, "reserve_post",
        lambda con, source: called.append("RESERVE") or True)
    monkeypatch.setattr(bridge, "confirm",
        lambda con, source, card: called.append("CONFIRM"))
    monkeypatch.setattr(bridge, "insert_new",
        lambda con, card: called.append("NATIVE_QUEUE") or True)
    def fake_call(client, base, method, params):
        if method == "crm.item.add":
            called.append("ADD")
            fake_call.payload = params
            assert params["fields"][bridge.SIGNAL] == SOURCE
            assert not (bridge.AI_FIELDS & params["fields"].keys())
            return {"result": {"item":{"id":45}}}
        if method == "crm.item.get":
            called.append("GET")
            return {"result":{"item":{
                "id":45,"entityTypeId":1062,"categoryId":21,
                **fake_call.payload["fields"],
            }}}
        pytest.fail("unexpected CRM call")
    monkeypatch.setattr(bridge, "bitrix_call", fake_call)
    assert bridge.deliver_one(object(),None,"secret") == "ONE_REAL_HOLD_QUEUED"
    assert called == ["RESERVE", "ADD", "GET", "NATIVE_QUEUE", "CONFIRM"]


def test_post_timeout_is_ambiguous_never_automatically_retried(monkeypatch):
    called = []
    monkeypatch.setattr(bridge, "attempted_24h",lambda con:0)
    monkeypatch.setattr(bridge, "read_candidate",lambda con:(SOURCE,NOTICE))
    monkeypatch.setattr(bridge, "validate_webhook",lambda x:"https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda client, base: True)
    monkeypatch.setattr(bridge, "exact_duplicate",
        lambda client, base, source:[dict(id=9)] if source ==
        "B2B-CENTER-4665948" else [])
    monkeypatch.setattr(bridge, "reserve_post",
                        lambda con, source:called.append("RESERVE") or True)
    def fail(*args):
        called.append("POST")
        raise TimeoutError("external outcome unknown")
    monkeypatch.setattr(bridge, "bitrix_call", fail)
    with pytest.raises(TimeoutError):
        bridge.deliver_one(object(),None,"secret")
    assert called == ["RESERVE", "POST"]
    assert bridge.INTERVAL_SECONDS == 300
    assert bridge.MAX_PER_CYCLE == 4
    assert bridge.MAX_PER_24H == 240




def test_continuous_four_items_without_manual_restart(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def fake_db(env):
        yield object()
    monkeypatch.setattr(bridge, "db", fake_db)
    monkeypatch.setattr(bridge, "migrate", lambda conn: None)
    monkeypatch.setattr(bridge, "statistics", lambda conn: {"PENDING": 2})
    monkeypatch.setattr(bridge, "preflight", lambda *args: None)
    monkeypatch.setattr(bridge, "ReadOnlyBitrix", lambda *a, **kw: object())
    monkeypatch.setattr(bridge, "secret_file", lambda *a: "synthetic")
    monkeypatch.setattr(bridge, "enabled", lambda *a, **kw: True)
    monkeypatch.setattr(bridge, "reconcile_uncertain", lambda *args: 0)
    seq = iter([
        "ONE_REAL_HOLD_QUEUED", "EXISTING",
        "ONE_REAL_HOLD_QUEUED", "ONE_REAL_HOLD_QUEUED",
        "ONE_REAL_HOLD_QUEUED", "NO_PENDING",
    ])
    monkeypatch.setattr(bridge, "deliver_one", lambda *a: next(seq))
    result = bridge.worker_once({}, client=object())
    assert result["status"] == "B2B_VPS_CONTINUOUS_CYCLE"
    assert result["batch_processed"] == 4
    assert result["new_holds"] == 3
    assert result["existing"] == 1
    assert result["queue_counts"]["PENDING"] == 2
    result2 = bridge.worker_once({}, client=object())
    assert result2["new_holds"] == 1
    assert result2["batch_processed"] == 2
    assert not result2["backlog_throttled"]


def test_rolling_throttle_only_defers_not_terminates_service(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def fake_db(env):
        yield object()
    monkeypatch.setattr(bridge, "db", fake_db)
    monkeypatch.setattr(bridge, "migrate", lambda conn: None)
    monkeypatch.setattr(bridge, "statistics", lambda conn: {"PENDING": 2})
    monkeypatch.setattr(bridge, "preflight", lambda *args: None)
    monkeypatch.setattr(bridge, "ReadOnlyBitrix", lambda *a, **kw: object())
    monkeypatch.setattr(bridge, "secret_file", lambda *a: "synthetic")
    monkeypatch.setattr(bridge, "enabled", lambda *a, **kw: True)
    monkeypatch.setattr(bridge, "reconcile_uncertain", lambda *args: 0)
    monkeypatch.setattr(bridge, "deliver_one",
                        lambda *a: "DAILY_BUDGET_THROTTLED")
    result = bridge.worker_once({}, client=object())
    assert result["batch_processed"] == 1
    assert result["new_holds"] == 0
    assert result["backlog_throttled"] is True


def test_rewritten_native_title_does_not_invalidate_exact_source_receipt():
    payload = bridge.hold_payload(NOTICE, bridge.classify_services(NOTICE["subject"]))
    fields = payload["fields"]
    for key in bridge.AI_FIELDS:
        fields.pop(key)
    card = {"id": 44, "entityTypeId": 1062, **fields}
    card["title"] = "📑 ТЕНДЕР · B2B — отображение Канбана"
    assert bridge.initial_readback_matches(card, 44, fields) is True
    card[bridge.SIGNAL] = "B2B-CENTER-0000000"
    assert bridge.initial_readback_matches(card, 44, fields) is False
    card[bridge.SIGNAL] = SOURCE
    card["UF_CRM_13_SVCIT_EVIDENCE_STATUS"] = "VERIFIED"
    assert bridge.initial_readback_matches(card, 44, fields) is False


def test_uncertain_existing_card_auto_recovers_once_no_second_crm_post(monkeypatch):
    events = []
    monkeypatch.setattr(bridge, "read_uncertain", lambda conn: [(SOURCE, NOTICE)])
    monkeypatch.setattr(bridge, "validate_webhook", lambda token: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda *a: True)
    monkeypatch.setattr(bridge, "exact_duplicate", lambda *a: [{"id": 44}])
    payload = bridge.hold_payload(NOTICE, bridge.classify_services(NOTICE["subject"]))
    fields = payload["fields"]
    for key in bridge.AI_FIELDS:
        fields.pop(key)
    actual = {"id": 44, "entityTypeId": 1062, **fields,
              "title": "Тендер | новое автоматическое оформление"}
    def only_read(client, base, method, params):
        assert method == "crm.item.get", "no crm.item.add on uncertain receipts"
        events.append("GET")
        return {"result": {"item": actual}}
    monkeypatch.setattr(bridge, "bitrix_call", only_read)
    monkeypatch.setattr(bridge, "insert_new",
                        lambda c, cid: events.append("QUEUE") or True)
    monkeypatch.setattr(bridge, "confirm",
                        lambda c, src, cid: events.append("CONFIRM") or None)
    assert bridge.reconcile_uncertain(object(), object(), "secret") == 1
    assert events == ["GET", "QUEUE", "CONFIRM"]


def test_uncertain_missing_or_ambiguous_crm_source_is_never_reposted(monkeypatch):
    monkeypatch.setattr(bridge, "read_uncertain", lambda conn: [(SOURCE, NOTICE)])
    monkeypatch.setattr(bridge, "validate_webhook", lambda token: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda *a: True)
    monkeypatch.setattr(bridge, "bitrix_call",
                        lambda *a: pytest.fail("no CRM GET/POST without unique source"))
    monkeypatch.setattr(bridge, "confirm",
                        lambda *a: pytest.fail("uncertain receipt remains fenced"))
    monkeypatch.setattr(bridge, "insert_new",
                        lambda *a: pytest.fail("ambiguous record never enqueued"))
    for matches in ([], [{"id": 41}, {"id": 42}]):
        monkeypatch.setattr(bridge, "exact_duplicate", lambda *a: matches)
        assert bridge.reconcile_uncertain(object(), object(), "secret") == 0

