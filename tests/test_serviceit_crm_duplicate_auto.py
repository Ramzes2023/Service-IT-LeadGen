"""Fail-closed CRM duplicate automation tests: no real CRM calls."""
from unittest.mock import Mock
import pytest

from leadpilot.serviceit_crm_duplicate_auto import (
    DuplicateBlocked, add_timeline_note, review_message,
    verify_visible_scope, inspect_one, MARK,
)


def test_found_collision_is_hold_and_not_sales_clearance():
    text = review_message(True)
    assert text.startswith(MARK)
    assert "HOLD" in text
    assert "не связываться" in text


def test_visible_no_match_is_not_global_clearance():
    text = review_message(False)
    assert "НЕ полная проверка CRM" in text
    assert "HOLD" in text


@pytest.mark.parametrize("item", [3, 7, -1])
def test_historical_ids_cannot_create_note(item):
    client = Mock()
    with pytest.raises(DuplicateBlocked):
        add_timeline_note(client, "https://example.invalid", item,
                          review_message(True))
    client.post.assert_not_called()


def test_non_allowlisted_comment_blocked():
    client = Mock()
    with pytest.raises(DuplicateBlocked):
        add_timeline_note(client, "https://example.invalid", 8,
                          "APPROVED TO CONTACT")
    client.post.assert_not_called()


def test_unverified_global_scope_fails_closed(monkeypatch):
    from leadpilot import serviceit_crm_duplicate_auto as module
    monkeypatch.setattr(module, "same_process_visible_duplicates",
                        lambda *_: {"process_visible_duplicate": False,
                                    "visible_pages_complete": True,
                                    "global_crm_clear": True})
    monkeypatch.setattr(module, "crm_inn_visibility_readonly",
                        lambda *_: {"visible_matches": 0,
                                    "visible_pages_complete": True,
                                    "global_dedup_verified": False,
                                    "global_crm_customer_clear": False})
    with pytest.raises(DuplicateBlocked):
        verify_visible_scope(None, "", "", 8, "7707083893")


def test_existing_note_idempotent_without_reservation(monkeypatch):
    from leadpilot import serviceit_crm_duplicate_auto as module
    monkeypatch.setattr(module, "read_item", lambda *_: {
        module.PREFIX + "COMPANY_INN": "7707083893",
        "createdTime": "2026-11-05T19:20:00+00:00",
    })
    monkeypatch.setattr(module, "after_activation",
                        lambda *_: module.datetime(2026, 11, 5,
                                                   tzinfo=module.timezone.utc))
    monkeypatch.setattr(module, "verify_visible_scope", lambda *_: True)
    monkeypatch.setattr(module, "read_comments", lambda *_: [{
        "ID": "100", "COMMENT": review_message(True)
    }])
    fence = Mock()
    result = inspect_one(None, {"SERVICEIT_BITRIX_WEBHOOK": "x"}, "",
                         8, fence,
                         now=module.datetime(2026, 11, 5, 20,
                                             tzinfo=module.timezone.utc))
    assert result == "EXISTING_MATCHING_NOTE"
    fence.reserve.assert_not_called()
