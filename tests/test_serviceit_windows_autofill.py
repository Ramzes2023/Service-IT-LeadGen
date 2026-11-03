"""Offline, no real Bitrix/AI: owner-operated Windows universal card auto-fill."""
from copy import deepcopy

import pytest

from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_windows_autofill import (
    AI_FIELDS, FIELDS, PREFIX, AutoFillBlocked,
    preliminary_fields, preliminary_once, run_cycle,
)


class CRM:
    def __init__(self, item_id=101, *, existing=False, update_error=False):
        self.item = {k: None for k in FIELDS}
        self.item.update({
            "id": item_id, "entityTypeId": 1062, "categoryId": 21,
            PREFIX + "SIGNAL_ID": "INDEPENDENT-101",
            PREFIX + "SIGNAL_KIND": "verified-review-pending",
            PREFIX + "SIGNAL_SOURCE": "corporate-ingestion",
            PREFIX + "SIGNAL_URL": "https://example.org/signal",
            PREFIX + "STOP_REASON": "",
        })
        if existing:
            self.item[PREFIX + "AI_STATUS"] = "ALREADY_FILLED"
        self.writes = 0
        self.reads = 0
        self.list_calls = 0
        self.update_error = update_error

    def __call__(self, method, params):
        if method == "crm.item.list":
            self.list_calls += 1
            minimum = int(params["filter"][">id"])
            return {"items": ([{
                "id": self.item["id"], "entityTypeId": 1062, "categoryId": 21
            }] if self.item["id"] > minimum else [])}
        if method == "crm.item.get":
            self.reads += 1
            return {"item": deepcopy(self.item)}
        if method == "crm.item.update":
            self.writes += 1
            assert params["entityTypeId"] == 1062
            assert params["id"] not in (3, 7)
            assert set(params["fields"]) == AI_FIELDS
            assert PREFIX + "LEAD_SCORE" not in params["fields"]
            assert PREFIX + "AI_SCORE_BADGE" not in params["fields"]
            self.item.update(params["fields"])
            if self.update_error:
                raise TimeoutError("synthetic ambiguous response")
            return {"item": deepcopy(self.item)}
        raise AssertionError(method)


@pytest.fixture
def fast_schema(monkeypatch):
    import leadpilot.serviceit_windows_autofill as module
    monkeypatch.setattr(module, "schema_limits", lambda rest: {k: 12000 for k in FIELDS})


def test_unverified_card_gets_all_nine_honest_report_fields():
    fields = preliminary_fields({"id": 101})
    assert set(fields) == AI_FIELDS
    assert fields[PREFIX + "AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"
    assert "GigaChat: NOT_RUN" in fields[PREFIX + "AI_FULL_REPORT"]
    assert "Lead Score: НЕ РАССЧИТАН" in fields[PREFIX + "AI_FULL_REPORT"]
    assert all(0 < len(value) <= 12000 for value in fields.values())


def test_source_preliminary_hold_is_preserved(tmp_path, fast_schema):
    crm = CRM()
    crm.item.update(preliminary_fields(crm.item))
    original = deepcopy(crm.item)
    store = Store(tmp_path / "hold.sqlite3")
    store.enqueue(101)
    assert preliminary_once(store, crm, 101) == "HOLD_9_PRESENT"
    assert crm.writes == 0 and crm.item == original


def test_partial_source_hold_only_fills_missing_fields(tmp_path, fast_schema):
    crm = CRM()
    crm.item[PREFIX + "AI_STATUS"] = "NOT_RUN_SOURCE_UNVERIFIED"
    crm.item[PREFIX + "AI_SUMMARY"] = "Existing manager source context"
    store = Store(tmp_path / "partial.sqlite3")
    store.enqueue(101)
    assert preliminary_once(store, crm, 101) == "HOLD_9_WRITTEN"
    assert crm.item[PREFIX + "AI_SUMMARY"] == "Existing manager source context"
    assert all(crm.item[k] for k in AI_FIELDS)


def test_one_real_contract_write_and_repeat_exactly_once(tmp_path, fast_schema):
    state = Store(tmp_path / "new-card-events.sqlite3")
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    crm = CRM()
    first = run_cycle(state, crm, approvals_dir=approvals)
    assert first["new_items_discovered"] == 1
    assert first["processed"] == [{"id": 101, "status": "HOLD_9_WRITTEN"}]
    assert crm.writes == 1
    assert set(k for k in AI_FIELDS if crm.item[k]) == AI_FIELDS
    assert crm.item[PREFIX + "LEAD_SCORE"] is None
    assert crm.item[PREFIX + "SCORE_VERSION"] is None
    assert crm.item[PREFIX + "AI_SCORE_BADGE"] is None
    second = run_cycle(state, crm, approvals_dir=approvals)
    assert second["new_items_discovered"] == 0
    assert second["processed"] == []
    assert crm.writes == 1
    assert state.metrics()["HOLD"] == 1


def test_ambiguous_post_is_fenced_forever(tmp_path, fast_schema):
    store = Store(tmp_path / "new-card-events.sqlite3")
    crm = CRM(update_error=True)
    store.enqueue(101)
    assert preliminary_once(store, crm, 101) == "UNCERTAIN"
    assert crm.writes == 1
    assert preliminary_once(store, crm, 101) == "IDLE"
    assert crm.writes == 1
    assert store.metrics()["UNCERTAIN"] == 1


def test_existing_analysis_never_overwritten(tmp_path, fast_schema):
    store = Store(tmp_path / "new-card-events.sqlite3")
    crm = CRM(existing=True)
    store.enqueue(101)
    assert preliminary_once(store, crm, 101) == "HOLD"
    assert crm.writes == 0
    assert crm.item[PREFIX + "AI_STATUS"] == "ALREADY_FILLED"


@pytest.mark.parametrize("old_id", [0, 3, 7, -1])
def test_historical_and_invalid_items_do_not_enter_writer(tmp_path, old_id):
    store = Store(tmp_path / "new-card-events.sqlite3")
    with pytest.raises(AutoFillBlocked, match="HISTORICAL_ITEMS_EXCLUDED"):
        preliminary_once(store, CRM(old_id), old_id)


def test_no_approval_or_model_means_no_gigachat_and_no_score(tmp_path, fast_schema):
    store = Store(tmp_path / "new-card-events.sqlite3")
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    crm = CRM()
    def model(text):
        pytest.fail("Unverified corporate source must not reach GigaChat")
    assert run_cycle(store, crm, approvals_dir=approvals, model=model)["processed"][0]["status"] == "HOLD_9_WRITTEN"
    assert crm.writes == 1
    assert crm.item[PREFIX + "AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"


def test_source_metadata_and_stage_untouched(tmp_path, fast_schema):
    store = Store(tmp_path / "new-card-events.sqlite3")
    crm = CRM()
    crm.item["stageId"] = "DT1062_21:NEW"
    before = {k: v for k, v in crm.item.items() if k not in FIELDS}
    store.enqueue(101)
    assert preliminary_once(store, crm, 101) == "HOLD_9_WRITTEN"
    assert {k: v for k, v in crm.item.items() if k not in FIELDS} == before
