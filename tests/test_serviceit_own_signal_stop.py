"""Independent ServiceIT signal-only STOP: fail-closed tests."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from leadpilot.serviceit_own_signal_stop import (
    VERSION, SCOPE, read_rules, check, SignalStopBlocked,
)
from leadpilot.serviceit_own_signal_stop_auto import (
    add_comment, message, OwnStopWriteBlocked, process_one, run, FLAG,
)


def config(tmp_path, rules):
    p = tmp_path / "stop.json"
    p.write_text(json.dumps({"version": VERSION, "scope": SCOPE,
                            "rules": rules}), encoding="utf8")
    return p


def test_new_list_is_empty_and_is_not_corporate_clearance():
    assert read_rules() == []
    assert check("7707083893", []) == ("OWN_SIGNAL_STOP_EMPTY", None)


def test_own_inn_match_with_reason(tmp_path):
    rules = [{"kind": "inn", "value": "7707083893",
              "reason": "REJECTED_BY_SERVICEIT"}]
    assert check("7707083893", read_rules(config(tmp_path, rules))) == (
        "HOLD_OWN_SIGNAL_STOP", "REJECTED_BY_SERVICEIT")
    assert check("7700000009", read_rules(config(tmp_path, rules))) == (
        "NOT_IN_OWN_SIGNAL_STOP", None)


def test_invalid_inn_not_implicitly_clear(tmp_path):
    assert check("wrong", []) == ("HOLD_MISSING_VALID_INN", None)


def test_duplicate_rules_rejected(tmp_path):
    item = {"kind": "inn", "value": "7707083893", "reason": "NOT_RELEVANT"}
    with pytest.raises(SignalStopBlocked):
        read_rules(config(tmp_path, [item, item]))


def test_invalid_reason_rejected(tmp_path):
    with pytest.raises(SignalStopBlocked):
        read_rules(config(tmp_path, [{"kind": "inn",
                  "value": "7707083893", "reason": "APPROVED_TO_CONTACT"}]))


@pytest.mark.parametrize("card", [3, 7, 0])
def test_never_write_historical_cards(card):
    c = Mock()
    with pytest.raises(OwnStopWriteBlocked):
        add_comment(c, "https://example.invalid", card,
                    message("NOT_RELEVANT"))
    c.post.assert_not_called()


def test_empty_list_ends_without_api_or_secret(monkeypatch):
    from leadpilot import serviceit_own_signal_stop_auto as m
    monkeypatch.setattr(m, "read_rules", lambda: [])
    env = {"GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
           "GITHUB_REF": "refs/heads/main",
           "GITHUB_EVENT_NAME": "schedule",
           "SERVICEIT_OWN_SIGNAL_STOP_ENABLED": FLAG}
    report = run(Mock(), env)
    assert report["status"] == "OWN_SIGNAL_STOP_ACTIVE_EMPTY"
    assert report["notes_written_verified"] == 0
    assert report["crm_global_stop_clear"] is False


def test_no_match_never_reserves_or_comments(monkeypatch):
    from leadpilot import serviceit_own_signal_stop_auto as m
    monkeypatch.setattr(m, "read_item", lambda *_: {
        "createdTime": "2026-11-05T19:20:00+00:00",
        m.PREFIX + "COMPANY_INN": "7700000009",
    })
    monkeypatch.setattr(m, "after_activation",
                        lambda *_: m.datetime(2026, 11, 5,
                                             tzinfo=m.timezone.utc))
    fence = Mock()
    status = process_one(None, {}, "", 8, fence,
                         [{"kind": "inn", "value": "7707083893",
                           "reason": "NOT_RELEVANT"}],
                         now=m.datetime(2026, 11, 5, 20,
                                        tzinfo=m.timezone.utc))
    assert status == "NOT_IN_OWN_SIGNAL_STOP"
    fence.reserve.assert_not_called()

def test_exact_card_only_does_not_block_another_tender(tmp_path):
    rules = read_rules(config(tmp_path, [
        {"kind": "card_id", "value": "108", "reason": "NOT_RELEVANT"}
    ]))
    assert check("7707083893", rules, card_id=108) == (
        "HOLD_OWN_SIGNAL_STOP", "NOT_RELEVANT")
    assert check("7707083893", rules, card_id=109) == (
        "NOT_IN_OWN_SIGNAL_STOP", None)
    assert check(None, rules, card_id=108) == (
        "HOLD_OWN_SIGNAL_STOP", "NOT_RELEVANT")


def test_historical_card_rule_forbidden(tmp_path):
    with pytest.raises(SignalStopBlocked):
        read_rules(config(tmp_path, [
            {"kind": "card_id", "value": "3", "reason": "NOT_RELEVANT"}
        ]))

def test_own_stop_prevents_new_automatic_review_task(monkeypatch):
    from leadpilot import serviceit_stage3_all_new_task_automation as m
    monkeypatch.setattr(m, "validate_webhook",
                        lambda _: "https://serviseit.bitrix24.ru/rest/1615/hidden")
    monkeypatch.setattr(m, "card_read",
                        lambda *_a, **_k: {
                            "UF_CRM_13_SVCIT_COMPANY_INN": "7707083893",
                        })
    monkeypatch.setattr(m, "own_signal_stop_rules", lambda: [
        {"kind": "inn", "value": "7707083893",
         "reason": "DO_NOT_REPROCESS"},
    ])
    monkeypatch.setattr(m, "check_task_binding",
                        lambda *_: pytest.fail("task check after STOP"))
    env = {
        "SERVICEIT_STAGE3_TASK_WRITE_ENABLED": m.WRITE_GATE,
        "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF": "refs/heads/main",
        "SERVICEIT_BITRIX_WEBHOOK": "hidden",
    }
    assert m.process_card(Mock(), env, 108) == "OWN_SIGNAL_STOP_HOLD_NO_TASK"
