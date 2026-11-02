"""CRM list omits entityTypeId by documented Bitrix24 contract.

Identity of the full item is separately validated by the write worker using
crm.item.get. Never enqueue a mismatched category or malformed ID.
"""
import pytest

from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_enrichment_worker import poll


def test_realistic_bitrix_list_without_entity_type_enqueues_only_new_items(tmp_path):
    store = Store(tmp_path / "new-card-events.sqlite3")
    def rest(method, params):
        assert method == "crm.item.list"
        assert params["select"] == ["id", "categoryId"]
        assert params["entityTypeId"] == 1062
        return {"items": [
            {"id": 8, "categoryId": 21},
            {"id": 9, "categoryId": 21},
        ] if params["filter"][">id"] < 8 else []}
    assert poll(store, rest, 8) == 2
    assert store.metrics() == {"PENDING_SOURCE_CHECK": 2}
    assert poll(store, rest, 8) == 0


@pytest.mark.parametrize("bad", [
    {"id": 11, "categoryId": 22},
    {"id": 11},
    {"id": 11, "categoryId": 21, "entityTypeId": 999},
    {"id": "eleven", "categoryId": 21},
    {"id": True, "categoryId": 21},
    {"id": 7, "categoryId": 21},
    {"id": 11.5, "categoryId": 21},
])
def test_bad_list_record_never_enqueued_or_advance_cursor(tmp_path, bad):
    store = Store(tmp_path / "new-card-events.sqlite3")
    with pytest.raises(ValueError):
        poll(store, lambda *_: {"items": [bad]}, 8)
    assert store.metrics() == {}
    with store.db() as db:
        assert db.execute(
            "SELECT value FROM worker_meta WHERE key='cursor'"
        ).fetchone() is None


def test_out_of_order_list_does_not_advance_to_last_wrong_id(tmp_path):
    store = Store(tmp_path / "new-card-events.sqlite3")
    with pytest.raises(ValueError, match="POLL_ORDER_INVALID"):
        poll(store, lambda *_: {"items": [
            {"id": 9, "categoryId": 21},
            {"id": 8, "categoryId": 21},
        ]}, 8)
    with store.db() as db:
        # Ledger may have enqueued first valid ID, but cursor never commits.
        assert db.execute(
            "SELECT value FROM worker_meta WHERE key='cursor'"
        ).fetchone() is None
