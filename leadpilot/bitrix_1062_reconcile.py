"""Read-only reconciliation of ONE synthetic 1062 outbox key against Bitrix24.

Requires an externally supplied read-only REST transport. This module
does not construct or hold a webhook, send HTTP, or expose methods for writes.
No match is *not* permission to resend; 2+ matches is a hard conflict.
"""
from __future__ import annotations

import re
from typing import Callable, Any

from .bitrix_1062_outbox import confirm_remote_readback, SyntheticPlanRejected

ENTITY_TYPE_ID = 1062
CATEGORY_ID = 21
KEY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
KEY_PATTERN = re.compile(r"DEMO-IDEMP-[a-f0-9]{32}\Z")
READ_ONLY = frozenset({"crm.item.list", "crm.item.get"})


def _read(call: Callable[[str, dict], Any], method: str, params: dict) -> dict:
    if method not in READ_ONLY or params.get("entityTypeId") != ENTITY_TYPE_ID:
        raise SyntheticPlanRejected("READ_METHOD_OR_ENTITY_FORBIDDEN")
    # The call adapter should return the REST result content, without the
    # outer transport metadata. Do not pass a writable method or any token.
    result = call(method, params)
    if not isinstance(result, dict):
        raise SyntheticPlanRejected("REST_RESULT_INVALID")
    return result


def inspect_remote_synthetic_key(call: Callable[[str, dict], Any], key: str) -> dict:
    """Read only 1062 category 21 records matching this precise synthetic key."""
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise SyntheticPlanRejected("SYNTHETIC_KEY_INVALID")
    params = {
        "entityTypeId": ENTITY_TYPE_ID,
        "filter": {"categoryId": CATEGORY_ID, KEY_FIELD: key},
        "select": ["id", "entityTypeId", "categoryId", "stageId", "title", KEY_FIELD],
        "useOriginalUfNames": "Y",
        "start": 0,
    }
    listing = _read(call, "crm.item.list", params)
    items = listing.get("items")
    if not isinstance(items, list):
        raise SyntheticPlanRejected("REMOTE_ITEMS_MALFORMED")
    if len(items) > 1:
        return {"status": "DUPLICATE_REMOTE_CANDIDATES", "count": len(items), "crm_writes": "NO"}
    if len(items) == 0:
        return {"status": "NO_VISIBLE_MATCH_NOT_PROOF_OF_ABSENCE", "count": 0,
                "crm_writes": "NO"}
    item = items[0]
    if (not isinstance(item, dict) or type(item.get("id")) is not int
            or item["id"] <= 0 or item.get("categoryId") != CATEGORY_ID
            or item.get(KEY_FIELD) != key
            or item.get("entityTypeId") != ENTITY_TYPE_ID):
        raise SyntheticPlanRejected("REMOTE_LIST_SCOPE_MISMATCH")
    details = _read(call, "crm.item.get", {
        "entityTypeId": ENTITY_TYPE_ID,
        "id": item["id"],
        "useOriginalUfNames": "Y",
    })
    full_item = details.get("item")
    if (not isinstance(full_item, dict)
            or full_item.get("id") != item["id"]
            or full_item.get("entityTypeId") != ENTITY_TYPE_ID
            or full_item.get("categoryId") != CATEGORY_ID
            or full_item.get(KEY_FIELD) != key
            or not isinstance(full_item.get("title"), str)
            or not full_item["title"].startswith("DEMO ServiceIT AI — DEMO-")):
        raise SyntheticPlanRejected("REMOTE_GET_SCOPE_MISMATCH")
    return {"status": "EXACT_SYNTHETIC_ITEM_VISIBLE",
            "count": 1, "item": full_item, "crm_writes": "NO"}


def reconcile_uncertain_key(db_path: str, call: Callable[[str, dict], Any], key: str) -> dict:
    """Reconcile by remote read-back, never by repeating a POST."""
    match = inspect_remote_synthetic_key(call, key)
    if match["status"] != "EXACT_SYNTHETIC_ITEM_VISIBLE":
        return {"status": match["status"], "requires_manual_review": True,
                "crm_writes": "NO"}
    receipt = confirm_remote_readback(db_path, key, crm_item=match["item"])
    return {"status": "RECONCILED_REMOTE_READBACK", "external_id": receipt["external_id"],
            "crm_writes": "NO", "post_retries": 0}
