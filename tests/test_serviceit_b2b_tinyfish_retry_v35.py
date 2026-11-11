"""Offline tests for persistent source-only V35 retries.

No live site, VPS, Bitrix, PostgreSQL, model or secret access.
"""
from unittest.mock import MagicMock

import pytest

from leadpilot import serviceit_vps_b2b_tinyfish_retry as retry
from leadpilot import serviceit_vps_worker as worker


ENV = {
    "SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED": "YES",
    "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "YES",
    "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "YES",
    "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "NO",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
}
NOTICE = {
    "source": "B2B-Center",
    "tender_id": "4675475",
    "subject": "Приобретение серверов для предприятия",
    "source_url": "https://www.b2b-center.ru/market/servers/tender-4675475/",
}
CARD = (17, "B2B-CENTER-4675475", NOTICE, None)


def _conn():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (True,)  # pg_try_advisory_lock
    return conn, cur


def _arrange(monkeypatch, *, row=CARD, result=None, error=None):
    monkeypatch.setattr(retry, "live_permitted", lambda env: True)
    monkeypatch.setattr(retry, "due_row", lambda conn: row)
    events = []
    def reserve(conn, card, digest):
        events.append("reserve")
        assert card == CARD[0]
        assert len(digest) == 64
        return 1
    monkeypatch.setattr(retry, "reserve", reserve)
    def finish(conn, card, attempt, status, pages):
        events.append(("finalize", status, pages))
    monkeypatch.setattr(retry, "finalize", finish)
    monkeypatch.setattr(retry, "secret_file", lambda *a, **k: "FAKE_NON_SECRET")
    monkeypatch.setattr(retry, "validate_webhook", lambda *a, **k: True)
    monkeypatch.setattr(retry, "validate_identity", lambda *a, **k: True)
    monkeypatch.setattr(retry, "normalized", lambda x: x)
    monkeypatch.setattr(retry, "safe_notice",
                        lambda notice, card, source, item:
                        (NOTICE["source_url"], NOTICE["subject"]))
    def rest_factory(*args, **kwargs):
        events.append("crm.read_only")
        return lambda method, payload: {
            "item": {"id": CARD[0], "source": "B2B-Center"}}
    monkeypatch.setattr(retry, "Rest", rest_factory)
    def fetch(*args, **kwargs):
        events.append("tinyfish.fetch")
        if error:
            raise RuntimeError("SYNTHETIC_SOURCE_UNAVAILABLE")
        return result
    monkeypatch.setattr(retry, "read_free_public_mirror", fetch)
    return events


def test_no_retries_without_existing_approved_public_source_optin(monkeypatch):
    monkeypatch.setattr(retry, "live_permitted", lambda env: True)
    for env in (
        {**ENV, "SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED": "NO"},
        {**ENV, "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "NO"},
        {**ENV, "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO"},
    ):
        conn, cur = _conn()
        got = retry.run_once(env, object(), conn)
        assert got["crm_writes"] == 0
        assert "DISABLED" in got["status"] or "BLOCKED" in got["status"]
        conn.execute.assert_not_called()
        cur.execute.assert_not_called()


def test_retry_one_old_verified_card_even_after_v19_completion(monkeypatch):
    events = _arrange(monkeypatch, result={
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://tendem.ru/tenders/16548471",
                   "excerpt": "Тендер №4675475, закупка серверов"}],
    })
    conn, cur = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got == {"status": "TINYFISH_RETRY_VERIFIED_PUBLIC_PAGE",
                   "verified_pages": 1, "crm_writes": 0}
    assert events == ["reserve", "crm.read_only", "tinyfish.fetch",
                      ("finalize", "VERIFIED_PUBLIC_PAGE", 1)]
    assert "pg_advisory_unlock" in str(cur.execute.call_args_list[-1])


@pytest.mark.parametrize("response", [
    None,
    {"status": "PUBLIC_TENDER_NOT_FOUND", "pages": []},
    {"status": "PUBLIC_PAGES_INSPECTED", "pages": []},
])
def test_no_verified_source_creates_retryable_pg_receipt(monkeypatch, response):
    events = _arrange(monkeypatch, result=response)
    conn, cur = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got["status"] == "TINYFISH_RETRY_NO_VERIFIED_PUBLIC_PAGE"
    assert ("finalize", "NO_VERIFIED_PUBLIC_PAGE", 0) in events
    assert got["crm_writes"] == 0


def test_temporary_provider_failure_does_not_block_next_card(monkeypatch):
    events = _arrange(monkeypatch, error=True)
    conn, _ = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got["status"] == "TINYFISH_RETRY_PROVIDER_EXCEPTION"
    assert ("finalize", "PROVIDER_EXCEPTION", 0) in events
    assert "tinyfish.fetch" in events
    assert got["crm_writes"] == 0


def test_unmatched_bitrix_identity_blocks_before_public_request(monkeypatch):
    events = _arrange(monkeypatch, result=None)
    def reject(*args, **kwargs):
        raise ValueError("SYNTHETIC_INVALID_CRM_ID")
    monkeypatch.setattr(retry, "safe_notice", reject)
    conn, _ = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got["status"] == "TINYFISH_RETRY_IDENTITY_CONFLICT"
    assert "tinyfish.fetch" not in events
    assert ("finalize", "IDENTITY_CONFLICT", 0) in events


def test_no_candidate_does_not_call_external_provider(monkeypatch):
    events = _arrange(monkeypatch, row=None)
    conn, _ = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got == {"status": "TINYFISH_RETRY_NOT_DUE", "crm_writes": 0}
    assert events == []


def test_receipt_scheduler_includes_all_v11_cards_not_only_v19(monkeypatch):
    conn, cur = _conn()
    cur.fetchone.return_value = None
    assert retry.due_row(conn) is None
    sql = cur.execute.call_args.args[0]
    assert "serviceit_vps_b2b_dossier_v11_receipts" in sql
    assert "serviceit_vps_b2b_v19_research_receipts" not in sql
    assert "24 hours" in sql and "72 hours" in sql
    assert "VERIFIED_PUBLIC_PAGE" in sql
    assert "RESERVED" in sql


def test_source_change_blocks_replay_without_reservation(monkeypatch):
    events = _arrange(monkeypatch, row=(*CARD[:3], "0" * 64))
    conn, _ = _conn()
    got = retry.run_once(ENV, object(), conn)
    assert got["status"] == "TINYFISH_RETRY_SOURCE_CHANGED_NO_REPLAY"
    assert events == []


def test_auto_optin_inherits_privacy_and_source_approval(monkeypatch):
    monkeypatch.setattr(worker, "secret_file", lambda *a, **k: "FAKE")
    on = worker.exact_environment({
        **ENV, "SERVICEIT_AI_PROVIDER": "routerai"})
    assert on["SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED"] == "YES"
    off = worker.exact_environment({
        **ENV, "SERVICEIT_AI_PROVIDER": "routerai",
        "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "NO"})
    assert off["SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED"] == "NO"


def test_invalid_result_cannot_be_finalized():
    with pytest.raises(ValueError):
        retry.finalize(None, 17, 1, "FAKE_CRM_WRITE", 2)
    with pytest.raises(ValueError):
        retry.finalize(None, 17, 1, "VERIFIED_PUBLIC_PAGE", 3)
