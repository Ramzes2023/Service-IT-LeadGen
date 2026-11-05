"""FNS MSP timeline fallback: never touch legacy cards or unrelated CRM fields."""
from unittest.mock import Mock
import pytest

from leadpilot.serviceit_fns_msp_timeline import (
    TimelineBlocked, timeline_rest, add_once, PREFIX,
)


def receipt():
    return {"msp_category": "SMALL", "published": "2026-10-10",
            "fns_egrul_verified": False, "formal_score_authorized": False}


@pytest.mark.parametrize("card", [3, 7, 0, -2])
def test_protect_existing_cards_without_network(card):
    client = Mock()
    with pytest.raises(TimelineBlocked):
        timeline_rest(client, "https://example.invalid",
                      "crm.timeline.comment.list", {
                          "filter": {"ENTITY_ID": card,
                                     "ENTITY_TYPE": "DYNAMIC_1062"},
                          "order": {"ID": "ASC"}, "start": 0,
                      })
    client.post.assert_not_called()


def test_only_dedicated_comment_is_allowed():
    client = Mock()
    with pytest.raises(TimelineBlocked):
        timeline_rest(client, "https://example.invalid",
                      "crm.timeline.comment.add", {
                          "fields": {"ENTITY_ID": 8,
                                     "ENTITY_TYPE": "DYNAMIC_1062",
                                     "COMMENT": "looks like a verified customer"},
                      })
    client.post.assert_not_called()


def test_existing_exact_comment_is_idempotent(monkeypatch):
    from leadpilot import serviceit_fns_msp_timeline as mod
    msg = PREFIX + " " + mod.receipt_label(receipt())
    monkeypatch.setattr(mod, "read_comments", lambda *_: [{
        "ID": "101", "ENTITY_ID": "8", "ENTITY_TYPE": "dynamic_1062",
        "COMMENT": msg,
    }])
    fence = Mock()
    assert add_once(Mock(), "https://example.invalid", fence, 8, receipt()) == "TIMELINE_ALREADY_EXACT"
    fence.reserve.assert_not_called()


def test_conflicting_existing_comment_holds_without_write(monkeypatch):
    from leadpilot import serviceit_fns_msp_timeline as mod
    monkeypatch.setattr(mod, "read_comments", lambda *_: [{
        "ID": "101", "ENTITY_ID": "8", "ENTITY_TYPE": "dynamic_1062",
        "COMMENT": PREFIX + " previous source",
    }])
    fence = Mock()
    assert add_once(Mock(), "https://example.invalid", fence, 8, receipt()) == "TIMELINE_EXISTING_RESULT_REVIEW"
    fence.reserve.assert_not_called()
