"""Live provisional score is a labelled signal triage rank, never sales approval."""
from datetime import datetime, timezone
from copy import deepcopy

import httpx
import pytest

import leadpilot.serviceit_stage4_provisional_score_live as w
from leadpilot.serviceit_enrichment_worker import PREFIX, AI_FIELDS
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

NOW = datetime(2026, 11, 4, 19, 0, tzinfo=timezone.utc)
WEBHOOK = "https://serviseit.bitrix24.ru/rest/1615/TEST_TOKEN12345678"
ENV = {
    "SERVICEIT_STAGE4_PRELIMINARY_SCORE_ENABLED": w.GATE,
    "GITHUB_REPOSITORY": w.REPOSITORY,
    "GITHUB_REF": "refs/heads/main",
    "SERVICEIT_BITRIX_WEBHOOK": WEBHOOK,
    "GITHUB_TOKEN": "FAKE_TOKEN_123456",
    "SERVICEIT_GH_LEDGER_KEY": "FAKE_FERNET_KEY_PLACEHOLDER",
    "SERVICEIT_BITRIX_POLL_START_UTC": "2026-11-04T18:00:00+00:00",
}


def real_shape(kind="tender-opportunity", inn=None):
    item = {
        "id": 123, "entityTypeId": 1062, "categoryId": 21,
        "createdTime": "2026-11-04T18:50:00+00:00",
        "stageId": "DT1062_21:NEW", "title": "Закупка сопровождения 1С",
        PREFIX + "SIGNAL_ID": "SOURCE-123",
        PREFIX + "SIGNAL_KIND": kind, PREFIX + "SIGNAL_SOURCE": "B2B-Center",
        PREFIX + "COMPANY_INN": inn, PREFIX + "REGION": "Москва",
        PREFIX + "AI_STATUS": "NOT_RUN_SOURCE_UNVERIFIED",
        PREFIX + "AI_SUMMARY": "📑 ТЕНДЕР · Закупка сопровождения 1С",
        PREFIX + "AI_FULL_REPORT": "HOLD — источник не проверен",
        SCORE_FIELD: None, VERSION_FIELD: "", BADGE_FIELD: None,
    }
    for field in AI_FIELDS:
        item.setdefault(field, "Служебное поле предварительной проверки")
    return item


class Fence:
    seen = set()
    commits = []
    def __init__(self, *args):
        self.receipt = None
    def reserve(self, card):
        self.commits.append(("reserve", card))
        if card in self.seen:
            return "ALREADY_FENCED_NO_RETRY"
        self.seen.add(card)
        return "RESERVED"
    def finalize(self, card, result):
        self.commits.append(("finalize", result))
        return True


def wire(monkeypatch, item):
    Fence.seen = set()
    Fence.commits = []
    before = deepcopy(item)
    crm = deepcopy(item)
    updates = []
    monkeypatch.setattr(w, "card_read", lambda client, base, card, *, now:
                        deepcopy(crm))
    monkeypatch.setattr(w, "score_schema_ok", lambda client, base: True)
    monkeypatch.setattr(w, "PreliminaryFence", Fence)
    monkeypatch.setattr(w, "visible_duplicate_status", lambda *args: "ИНН НЕТ")
    def transport(client, base, method, args):
        if method == "crm.item.update":
            assert set(args["fields"]) == w.SCORE_KEYS
            assert args["entityTypeId"] == 1062 and args["id"] == 123
            updates.append(deepcopy(args["fields"]))
            crm.update(args["fields"])
            return {"item": deepcopy(crm)}
        if method == "crm.item.get":
            return {"item": deepcopy(crm)}
        raise AssertionError(method)
    monkeypatch.setattr(w, "score_rest", transport)
    return crm, updates


def test_real_new_hold_card_gets_numeric_preliminary_score_and_status(monkeypatch):
    crm, updates = wire(monkeypatch, real_shape())
    with httpx.Client() as client:
        result = w.evaluate_one(client, ENV, 123, now=NOW)
        repeated = w.evaluate_one(client, ENV, 123, now=NOW)
    assert result == "PROVISIONAL_SCORE_EXACT_READBACK"
    assert repeated == "ALREADY_PROVISIONAL_NO_REWRITE"
    assert len(updates) == 1
    assert type(crm[SCORE_FIELD]) is int and 0 <= crm[SCORE_FIELD] <= 100
    assert crm[VERSION_FIELD] == w.VERSION
    assert "ПРЕДВАРИТЕЛЬНО" in crm[BADGE_FIELD]
    assert "ФНС: НЕ ПРОВЕРЕНА" in crm[BADGE_FIELD]
    assert "Контакт: НЕ РАЗРЕШЁН" in crm[BADGE_FIELD]
    assert crm[PREFIX + "AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"
    assert Fence.commits == [("reserve", 123), ("finalize", "UNCERTAIN"),
                             ("finalize", "HOLD")]


def test_fns_absence_is_not_blocking_if_new_ai_fields_already_filled(monkeypatch):
    crm, updates = wire(monkeypatch, real_shape())
    assert w.provisional_payload(65, "НЕ ПРОВЕРЕНЫ")[VERSION_FIELD] == w.VERSION
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "PROVISIONAL_SCORE_EXACT_READBACK"
    assert len(updates) == 1


def test_known_visible_duplicate_is_hold_without_any_score_write(monkeypatch):
    _, updates = wire(monkeypatch, real_shape(inn="7700000009"))
    monkeypatch.setattr(w, "visible_duplicate_status", lambda *a: "ДУБЛЬ НАЙДЕН")
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "VISIBLE_DUPLICATE_HOLD_NO_SCORE"
    assert updates == []


def test_untested_dedup_does_not_fake_global_clearance(monkeypatch):
    crm, updates = wire(monkeypatch, real_shape())
    monkeypatch.setattr(w, "visible_duplicate_status", lambda *a: "НЕ ПРОВЕРЕНЫ")
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "PROVISIONAL_SCORE_EXACT_READBACK"
    assert "Дубли: НЕ ПРОВЕРЕНЫ" in crm[BADGE_FIELD]
    assert len(updates) == 1


def test_main_ai_must_fill_fields_before_score(monkeypatch):
    item = real_shape()
    item[PREFIX + "AI_STATUS"] = None
    _, updates = wire(monkeypatch, item)
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "WAIT_FOR_AI_MANAGER_FIELDS"
    assert updates == []


@pytest.mark.parametrize("value", [3, 7, 0, 2_000_000_001])
def test_historical_card_or_invalid_id_never_written(monkeypatch, value):
    _, updates = wire(monkeypatch, real_shape())
    with httpx.Client() as client:
        with pytest.raises(w.ProvisionalBlocked, match="PROVISIONAL_CARD_OUT_OF_SCOPE"):
            w.evaluate_one(client, ENV, value, now=NOW)
    assert updates == []


def test_existing_formal_or_manual_score_never_overwritten(monkeypatch):
    item = real_shape()
    item[SCORE_FIELD] = 90
    item[VERSION_FIELD] = "svc-score-verified"
    item[BADGE_FIELD] = "🟢 90/100"
    _, updates = wire(monkeypatch, item)
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "OTHER_SCORE_PRESENT_NO_REWRITE"
    assert updates == []


def test_no_source_records_tasks_or_outreach_modified():
    from pathlib import Path
    code = Path("leadpilot/serviceit_stage4_provisional_score_live.py").read_text("utf8")
    assert "tasks.task.add" not in code
    assert "crm.item.add" not in code
    assert "crm.contact.update" not in code
    assert "crm.item.update" in code
    doc = Path(".github/workflows/serviceit-stage4-prelim-score-live.yml").read_text("utf8")
    assert "serviceit-bitrix-new-card-free-event" in doc
    assert "SERVICEIT_STAGE4_PRELIM_SCORE_LIVE_ONCE_V1" in doc
    assert "YES_1062_21_PROVISIONAL_ONLY" in doc


@pytest.mark.parametrize("field", sorted(AI_FIELDS))
@pytest.mark.parametrize("incomplete", [None, "", "   ", 1])
def test_provisional_score_waits_for_each_of_all_nine_ai_fields(
        monkeypatch, field, incomplete):
    # A status+summary+full report alone must not unlock Score when one of
    # the other six AI fields is blank or not a meaningful string.
    item = real_shape()
    item[field] = incomplete
    _, updates = wire(monkeypatch, item)
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "WAIT_FOR_AI_MANAGER_FIELDS"
    assert updates == []
    assert Fence.commits == []


def test_missing_one_of_nine_fields_refuses_to_score(monkeypatch):
    item = real_shape()
    item.pop(PREFIX + "AI_QUOTE")
    _, updates = wire(monkeypatch, item)
    with httpx.Client() as client:
        with pytest.raises(w.ProvisionalBlocked, match="AI_FIELDS_NOT_READABLE"):
            w.evaluate_one(client, ENV, 123, now=NOW)
    assert updates == []


def test_explicit_model_hypothesis_source_unverified_is_preliminary_only(monkeypatch):
    item = real_shape()
    item[PREFIX + "AI_STATUS"] = "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    _, updates = wire(monkeypatch, item)
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "PROVISIONAL_SCORE_EXACT_READBACK"
    assert len(updates) == 1
    assert "НЕ ПРОВЕРЕНА" in updates[0][BADGE_FIELD]
    assert "НЕ РАЗРЕШЁН" in updates[0][BADGE_FIELD]
    assert updates[0][VERSION_FIELD] == w.VERSION



def test_own_stop_exact_inn_blocks_provisional_score(monkeypatch):
    _, updates = wire(monkeypatch, real_shape(inn="7700000009"))
    monkeypatch.setattr(w, "own_stop_rules", lambda: [
        {"kind": "inn", "value": "7700000009",
         "reason": "REJECTED_BY_SERVICEIT"},
    ])
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "OWN_SIGNAL_STOP_MATCH_HOLD_NO_SCORE"
    assert updates == [] and Fence.commits == []


def test_own_stop_card_id_blocks_without_valid_inn(monkeypatch):
    _, updates = wire(monkeypatch, real_shape(inn=None))
    monkeypatch.setattr(w, "own_stop_rules", lambda: [
        {"kind": "card_id", "value": "123", "reason": "NOT_RELEVANT"},
    ])
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "OWN_SIGNAL_STOP_MATCH_HOLD_NO_SCORE"
    assert updates == [] and Fence.commits == []


def test_invalid_own_stop_config_fails_closed(monkeypatch):
    from leadpilot.serviceit_own_signal_stop import SignalStopBlocked
    _, updates = wire(monkeypatch, real_shape(inn="7700000009"))
    monkeypatch.setattr(
        w, "own_stop_rules",
        lambda: (_ for _ in ()).throw(SignalStopBlocked("BROKEN_CONFIG")))
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "OWN_SIGNAL_STOP_CONFIG_HOLD_NO_SCORE"
    assert updates == []


def test_missing_egrul_api_does_not_authorize_contact_or_formal_score(monkeypatch):
    _, updates = wire(monkeypatch, real_shape(inn="7700000009"))
    with httpx.Client() as client:
        assert w.evaluate_one(client, ENV, 123, now=NOW) == "PROVISIONAL_SCORE_EXACT_READBACK"
    assert len(updates) == 1
    assert updates[0][VERSION_FIELD] == w.VERSION
    assert "ФНС: НЕ ПРОВЕРЕНА" in updates[0][BADGE_FIELD]
    assert "Контакт: НЕ РАЗРЕШЁН" in updates[0][BADGE_FIELD]
