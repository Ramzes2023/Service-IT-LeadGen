"""Unusual real Bitrix24 nested items must never bypass ID/category checks."""
import pytest

from leadpilot.serviceit_bitrix_list_shape import (
    strict_bitrix_items, ItemListShapeError
)
from leadpilot.serviceit_cloud_cutover_readonly import latest_existing_card_id
from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_enrichment_worker import poll


def item(i, c=21):
    return {"id": i, "categoryId": c}


def test_documented_flat_array_is_unchanged():
    data = [item(8), item(9)]
    assert strict_bitrix_items({"items": data}) == data


def test_single_extra_group_layer_unwraps_only_all_dicts():
    assert strict_bitrix_items({"items": [[item(25), item(22)]]}) == [item(25), item(22)]
    assert strict_bitrix_items({"items": [[item(25)], [item(22)]]}) == [item(25), item(22)]


def test_empty_flat_list_remains_empty():
    assert strict_bitrix_items({"items": []}) == []


@pytest.mark.parametrize("raw", [
    [[{"id": 1}], "mixed"],
    [[item(25), "secret"]],
    [[item(25)], []],
    [[item(25), [item(22)]]],
    [None],
    [{"id": 25}, [item(22)]],
    [list(range(51))],
])
def test_malformed_nested_input_never_silently_skips(raw):
    with pytest.raises(ValueError):
        strict_bitrix_items({"items": raw})


def test_more_than_50_nested_records_is_blocked():
    with pytest.raises(ValueError, match="BITRIX_LIST_ITEMS_UNVERIFIED"):
        strict_bitrix_items({"items": [[item(k) for k in range(51)]]})


def test_cutover_nested_wrapper_requires_second_readonly_get():
    operations = []
    def rest(method, params):
        operations.append(method)
        if method == "crm.item.list":
            assert params["entityTypeId"] == 1062
            assert params["order"] == {"id": "DESC"}
            return {"items": [[item(29), item(28)]]}
        assert method == "crm.item.get"
        assert params["id"] == 29
        return {"item": {"id": 29, "categoryId": 21, "entityTypeId": 1062}}
    assert latest_existing_card_id(rest) == 29
    assert operations == ["crm.item.list", "crm.item.get"]


@pytest.mark.parametrize("raw", [
    [[item(27), item(28)]],  # reversed ordering
    [[item(27), item(27)]],  # duplicate
    [[item(27, 99)]],      # out-of-scope
])
def test_cutover_nested_fails_closed_on_order_or_category(raw):
    calls = []
    def rest(method, _):
        calls.append(method)
        return {"items": raw}
    with pytest.raises(ValueError):
        latest_existing_card_id(rest)
    assert calls == ["crm.item.list"]


def test_cutover_nested_does_not_trust_wrong_full_item_identity():
    def rest(method, _):
        if method == "crm.item.list":
            return {"items": [[item(67)]]}
        return {"item": {"id": 67, "entityTypeId": 2, "categoryId": 21}}
    with pytest.raises(ValueError, match="CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED"):
        latest_existing_card_id(rest)


def test_new_card_poll_from_nested_group_preserves_queue_and_cursor(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    def rest(method, params):
        assert method == "crm.item.list"
        if params["filter"][">id"] < 9:
            return {"items": [[item(8), item(9)]]}
        return {"items": []}
    assert poll(store, rest, 8) == 2
    assert store.metrics() == {"PENDING_SOURCE_CHECK": 2}
    assert poll(store, rest, 8) == 0
    with store.db() as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone()[0] == "9"


def test_nested_list_with_unsafe_child_never_enqueues(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    with pytest.raises(ValueError):
        poll(store, lambda *_: {"items": [[item(8), "not_an_item"]]}, 8)
    assert store.metrics() == {}


def test_new_card_poll_refuses_nested_record_from_wrong_category(tmp_path):
    store = Store(tmp_path / "ledger.sqlite3")
    with pytest.raises(ValueError):
        poll(store, lambda *_: {"items": [[item(8, 25)]]}, 8)
    assert store.metrics() == {}


def test_nested_empty_group_is_not_treated_as_empty_category():
    with pytest.raises(ItemListShapeError):
        strict_bitrix_items({"items": [[]]})
