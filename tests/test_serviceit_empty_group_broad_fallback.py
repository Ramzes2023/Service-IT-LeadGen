"""Offline regression tests for live Bitrix24 nonstandard [[]] responses."""
import pytest

from leadpilot.serviceit_cloud_cutover_readonly import latest_existing_card_id
from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_enrichment_worker import poll


def card(i, cat=21):
    return {"id": i, "categoryId": cat}


def test_cutover_empty_group_uses_conservative_all_category_upper_bound():
    calls = []
    def rest(method, args):
        calls.append((method, args))
        if method == "crm.item.list" and "filter" in args:
            return {"items": [[]], "total": 0}
        if method == "crm.item.list":
            assert args["entityTypeId"] == 1062
            assert args["order"] == {"id": "DESC"}
            return {"items": [card(120, 22), card(119)], "total": 2}
        assert method == "crm.item.get" and args["id"] == 120
        return {"item": {"id": 120, "entityTypeId": 1062,
                         "categoryId": 22}}
    assert latest_existing_card_id(rest) == 120
    assert [method for method, _ in calls] == [
        "crm.item.list", "crm.item.list", "crm.item.get"]


def test_cutover_does_not_treat_two_empty_groups_as_no_historical_cards():
    def rest(method, args):
        return {"items": [[]], "total": 0}
    with pytest.raises(ValueError, match="CUTOVER_ITEM_INVALID"):
        latest_existing_card_id(rest)


def test_cutover_requires_broad_nonempty_if_filtered_is_unknown():
    def rest(method, args):
        if "filter" in args:
            return {"items": [[]]}
        return {"items": [], "total": 0}
    with pytest.raises(ValueError, match="CUTOVER_BROAD_EMPTY_UNVERIFIED"):
        latest_existing_card_id(rest)


def test_cutover_full_get_wrong_type_refuses_fallback():
    def rest(method, args):
        if method == "crm.item.list" and "filter" in args:
            return {"items": [[]]}
        if method == "crm.item.list":
            return {"items": [card(19, 21)]}
        return {"item": {"id": 19, "entityTypeId": 999, "categoryId": 21}}
    with pytest.raises(ValueError, match="CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED"):
        latest_existing_card_id(rest)


def test_poll_broad_fallback_handles_other_category_without_enqueuing(tmp_path):
    store = Store(tmp_path / "queue.sqlite3")
    methods = []
    def rest(method, args):
        methods.append(method)
        if method == "crm.item.list":
            if "categoryId" in args["filter"]:
                return {"items": [[]], "total": 0}
            assert args["filter"] == {">id": 7}
            return {"items": [card(8, 22), card(9, 21)], "total": 2}
        assert method == "crm.item.get"
        card_id = args["id"]
        return {"item": {"id": card_id, "categoryId": 22 if card_id == 8 else 21,
                         "entityTypeId": 1062}}
    assert poll(store, rest, 8) == 1
    assert methods == ["crm.item.list", "crm.item.list", "crm.item.get", "crm.item.get"]
    assert store.metrics() == {"PENDING_SOURCE_CHECK": 1}
    with store.db() as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone()[0] == "9"
        assert db.execute("SELECT item_id FROM new_cards").fetchone()[0] == 9


def test_poll_broad_zero_with_explicit_total_only_is_safe(tmp_path):
    store = Store(tmp_path / "queue.sqlite3")
    def rest(method, args):
        if "categoryId" in args["filter"]:
            return {"items": [[]], "total": 0}
        assert args["filter"] == {">id": 7}
        return {"items": [], "total": 0}
    assert poll(store, rest, 8) == 0
    assert store.metrics() == {}


@pytest.mark.parametrize("broad", [
    {"items": []},
    {"items": [[]], "total": 0},
    {"items": [], "total": 0, "next": 50},
])
def test_poll_unknown_broad_empty_never_advances_cursor(tmp_path, broad):
    store = Store(tmp_path / "queue.sqlite3")
    def rest(method, args):
        if "categoryId" in args["filter"]:
            return {"items": [[]], "total": 0}
        return broad
    with pytest.raises(ValueError):
        poll(store, rest, 8)
    with store.db() as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone() is None


def test_poll_broad_full_get_wrong_identity_blocks_before_write(tmp_path):
    store = Store(tmp_path / "queue.sqlite3")
    def rest(method, args):
        if method == "crm.item.list" and "categoryId" in args["filter"]:
            return {"items": [[]]}
        if method == "crm.item.list":
            return {"items": [card(8)]}
        return {"item": {"id": 8, "categoryId": 21, "entityTypeId": 2}}
    with pytest.raises(ValueError):
        poll(store, rest, 8)
    assert store.metrics() == {}
    with store.db() as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone() is None
