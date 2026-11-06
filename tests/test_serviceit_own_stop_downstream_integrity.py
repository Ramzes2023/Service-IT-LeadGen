"""Security checks: independent LeadGen STOP blocks downstream CRM writes."""
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest

import leadpilot.serviceit_crm_duplicate_auto as dedup
import leadpilot.serviceit_fns_msp_crm_result_writer as fns

NOW = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)


def test_own_card_stop_prevents_duplicate_note_without_valid_inn(monkeypatch):
    monkeypatch.setattr(dedup, "read_item", lambda *_: {
        "createdTime": "2026-11-06T17:50:00+00:00",
        dedup.PREFIX + "COMPANY_INN": None,
    })
    monkeypatch.setattr(dedup, "after_activation",
                        lambda *args: datetime(2026, 11, 6, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(dedup, "own_stop_rules", lambda: [
        {"kind": "card_id", "value": "108", "reason": "NOT_RELEVANT"},
    ])
    monkeypatch.setattr(
        dedup, "verify_visible_scope",
        lambda *_: pytest.fail("Stopped card must never run duplicate preflight"))
    fence = Mock()
    with_no_api = Mock()
    assert dedup.inspect_one(with_no_api, {}, "", 108, fence, now=NOW) == (
        "HOLD_OWN_STOP_NO_DUPLICATE_NOTE")
    fence.reserve.assert_not_called()
    with_no_api.post.assert_not_called()


def test_own_company_stop_prevents_fns_result_even_with_receipt(monkeypatch):
    monkeypatch.setattr(fns, "read_item", lambda *_: {
        fns.PREFIX + "COMPANY_INN": "7707083893",
    })
    monkeypatch.setattr(fns, "own_stop_rules", lambda: [
        {"kind": "inn", "value": "7707083893", "reason": "DO_NOT_REPROCESS"},
    ])
    fence = Mock()
    official = Mock()
    client = Mock()
    assert fns.write_one(client, {}, "", fence, official, 108) == (
        "HOLD_OWN_STOP_NO_FNS_WRITE")
    official.read_checked_receipt.assert_not_called()
    fence.reserve.assert_not_called()
    client.post.assert_not_called()


def test_bad_own_stop_config_prevents_fns_and_duplicate_actions(monkeypatch):
    from leadpilot.serviceit_own_signal_stop import SignalStopBlocked
    def bad_config():
        raise SignalStopBlocked("CORRUPT")
    monkeypatch.setattr(fns, "read_item", lambda *_: {
        fns.PREFIX + "COMPANY_INN": "7707083893",
    })
    monkeypatch.setattr(fns, "own_stop_rules", bad_config)
    client, fence, official = Mock(), Mock(), Mock()
    assert fns.write_one(client, {}, "", fence, official, 108) == (
        "HOLD_OWN_STOP_CONFIG_INVALID")
    client.post.assert_not_called()
    official.read_checked_receipt.assert_not_called()
    fence.reserve.assert_not_called()
