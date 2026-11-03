"""Exact Bitrix24 event-queue to manager-card automation, without CRM list polling.

One ONCRMDYNAMICITEMADD for entity 1062/category 21 is stored by the separately
authenticated receiver. This consumer claims only those exact IDs, populates
nine manager-facing fields for unverified signals or performs the existing
approved GigaChat + deterministic Score workflow. It never modifies contacts,
stages, tasks, or historical cards 3/7. Provider and write reservations use
the same durable SQLite queue; ambiguous results are never blindly replayed.
"""
from __future__ import annotations

from pathlib import Path

from .serviceit_enrichment_store import Store
from .serviceit_enrichment_worker import Worker, load_approval
from .serviceit_windows_autofill import preliminary_once

MAX_ITEMS_PER_CYCLE = 10
MIN_NEW_ITEM_ID = 8


class EventAutofillBlocked(ValueError):
    pass


def pending_event_ids(store: Store, limit: int = MAX_ITEMS_PER_CYCLE) -> list[int]:
    if not isinstance(store, Store) or type(limit) is not int or not 1 <= limit <= 10:
        raise EventAutofillBlocked("EVENT_STORE_OR_LIMIT_INVALID")
    with store.db() as db:
        rows = db.execute(
            "SELECT item_id FROM new_cards WHERE entity_type_id=1062 "
            "AND status='PENDING_SOURCE_CHECK' AND item_id>=? "
            "ORDER BY item_id ASC LIMIT ?",
            (MIN_NEW_ITEM_ID, limit),
        ).fetchall()
    return [int(r[0]) for r in rows]


def run_event_cycle(
    store: Store, rest, *,
    approvals_dir: Path,
    model=None,
    public_ai_hosts: frozenset[str] = frozenset(),
    max_items: int = MAX_ITEMS_PER_CYCLE,
) -> dict:
    """Process only authenticated exact event IDs; never call crm.item.list.

    Unapproved sources get nine honest manager-review fields and no fake Score.
    Corporate-approved sources use existing verified_worker logic, including
    real GigaChat, deterministic score, exact write/readback and privacy fences.
    """
    if not isinstance(approvals_dir, Path) or not approvals_dir.is_absolute() \
            or not approvals_dir.is_dir():
        raise EventAutofillBlocked("APPROVAL_DIRECTORY_NOT_READY")
    if not callable(rest):
        raise EventAutofillBlocked("BITRIX_REST_NOT_CONFIGURED")
    ids = pending_event_ids(store, max_items)
    worker = Worker(
        store, rest,
        lambda number: load_approval(approvals_dir, number),
        model, enabled=True,
    )
    processed = []
    for item_id in ids:
        try:
            approval = load_approval(approvals_dir, item_id)
            state = (
                worker.once(item_id)
                if approval is not None else
                preliminary_once(store, rest, item_id, model=model,
                                 public_ai_hosts=public_ai_hosts)
            )
        except Exception:
            # Never retry an item with unknown prior side effects by guessing.
            # The durable per-item reservation remains the authority. A later
            # cycle may inspect a still-PENDING entry if failure was pre-claim.
            state = "REVIEW_REQUIRED_NO_UNSAFE_RETRY"
        processed.append({"id": item_id, "status": state})
    return {
        "status": "SERVICEIT_EXACT_EVENT_AUTOFILL_CYCLE",
        "event_ids_seen": len(ids),
        "processed": processed,
        "crm_list_calls": 0,
        "historical_cards_touched": False,
    }
