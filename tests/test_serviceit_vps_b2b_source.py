"""Fail-closed tests: B2B source must not bypass native VPS PostgreSQL."""
from __future__ import annotations

from email.message import EmailMessage

import pytest

from leadpilot import serviceit_vps_b2b_source as s


ROW = {
    "tender_id": "4672286",
    "source_url": "https://www.b2b-center.ru/market/soprovozhdenie-1s/tender-4672286/",
    "subject": "Оказание услуг по сопровождению системы 1С",
    "organizer_as_named_in_email": 'ООО "ПРИМЕР"',
    "source_service_groups": ["1C"],
}


def config():
    return {
        "SERVICEIT_VPS_B2B_SOURCE_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_CRM_WRITE_ENABLED": "NO",
        "SERVICEIT_VPS_B2B_LEGACY_WRITERS_DISABLED": "YES",
        "SERVICEIT_VPS_WRITER_MODE": "LIVE",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
        "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_POLL_ENABLED": "YES",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
        "SERVICEIT_AI_PROVIDER": "routerai",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
        "SERVICEIT_VPS_B2B_ACTIVATION_UTC": "2026-11-09T00:00:00Z",
    }


def test_source_disabled_by_default():
    assert s.enabled({}) == (False, False)
    with pytest.raises(s.SourceBlocked):
        s.enabled({"SERVICEIT_VPS_B2B_CRM_WRITE_ENABLED": "YES"})


def test_no_legacy_gh_writer_or_parallel_ingress(monkeypatch):
    monkeypatch.setattr(s, "live_permitted", lambda e: True)
    env = config()
    assert s.enabled(env) == (True, False)
    env["SERVICEIT_VPS_B2B_CRM_WRITE_ENABLED"] = "YES"
    assert s.enabled(env) == (True, True)
    env["SERVICEIT_VPS_B2B_LEGACY_WRITERS_DISABLED"] = "NO"
    with pytest.raises(s.SourceBlocked):
        s.enabled(env)
    env["SERVICEIT_VPS_B2B_LEGACY_WRITERS_DISABLED"] = "YES"
    env["SERVICEIT_VPS_INTAKE_ENABLED"] = "YES"
    with pytest.raises(s.SourceBlocked):
        s.enabled(env)


def mail(*, authenticated=True, sender="marketplace@b2b-center.ru"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "procurement@example.invalid"
    msg["Subject"] = "IT offer"
    if authenticated:
        msg["Authentication-Results"] = (
            "mx.google.com; dkim=pass header.i=@b2b-center.ru; "
            "dmarc=pass header.from=b2b-center.ru"
        )
    msg.set_content("Public tender")
    return msg.as_bytes()


def test_dkim_dmarc_and_sender_required():
    s.verified_google_sender(mail())
    with pytest.raises(s.SourceBlocked):
        s.verified_google_sender(mail(authenticated=False))
    with pytest.raises(s.SourceBlocked):
        s.verified_google_sender(mail(sender="attacker@example.com"))


def test_b2b_notice_cannot_be_qualified():
    n = s.notice_for(ROW)
    assert n["buyer_inn"] is None
    assert n["qualified_lead"] is False
    assert n["contact_approved"] is False
    assert n["original_publication_verified"] is False
    assert s.INTERVAL_SECONDS == 300
    assert s.MAX_PER_CYCLE == 1


def test_preexisting_source_never_calls_bitrix_add(monkeypatch):
    monkeypatch.setattr(s, "validate_webhook", lambda hook: "https://example.invalid/rest")
    monkeypatch.setattr(s, "exact_duplicate",
                        lambda client, base, sid: [dict(id=8)])
    monkeypatch.setattr(s, "reservation", lambda con, sid: False)
    monkeypatch.setattr(s, "bitrix_call", lambda *a: pytest.fail("CRM POST forbidden"))
    assert s.write_hold(None, object(), "fake", ROW) == "ALREADY_IN_CRM"


def test_new_card_reserved_before_only_post_and_native_queued(monkeypatch):
    events = []
    monkeypatch.setattr(s, "validate_webhook", lambda hook: "https://example.invalid/rest")
    monkeypatch.setattr(s, "exact_duplicate",
                        lambda client, base, sid: [dict(id=8)] if
                        sid == "B2B-CENTER-4665948" else [])
    def reserve(conn, source):
        events.append("RESERVE")
        return True
    monkeypatch.setattr(s, "reservation", reserve)
    monkeypatch.setattr(s, "mark",
                        lambda conn, src, state, card: events.append("VERIFIED"))
    monkeypatch.setattr(s, "insert_new",
                        lambda conn, card: events.append("QUEUE") or True)
    def bitrix_call(client, base, method, params):
        if method == "crm.item.add":
            events.append("ADD")
            assert params["fields"][s.SIGNAL] == "B2B-CENTER-4672286"
            assert not s.AI_FIELDS.intersection(params["fields"])
            bitrix_call.payload = params
            return {"result": {"item": {"id": 42}}}
        if method == "crm.item.get":
            events.append("GET")
            return {"result": {"item": {
                **bitrix_call.payload["fields"], "id": 42,
                "entityTypeId": 1062, "categoryId": 21,
            }}}
        pytest.fail("unexpected method")
    monkeypatch.setattr(s, "bitrix_call", bitrix_call)
    result = s.write_hold(None, object(), "fake", ROW)
    assert result == "HOLD_VERIFIED_NATIVE_QUEUE"
    assert events == ["RESERVE", "ADD", "GET", "VERIFIED", "QUEUE"]


def test_unknown_write_receipt_is_never_replayed(monkeypatch):
    monkeypatch.setattr(s, "validate_webhook", lambda hook: "https://example.invalid/rest")
    monkeypatch.setattr(s, "exact_duplicate",
                        lambda client, base, sid: [dict(id=8)] if
                        sid == "B2B-CENTER-4665948" else [])
    monkeypatch.setattr(s, "reservation", lambda con, sid: False)
    monkeypatch.setattr(s, "bitrix_call", lambda *a: pytest.fail("must not POST"))
    assert s.write_hold(None, object(), "fake", ROW) == "ALREADY_RESERVED_NO_RETRY"
