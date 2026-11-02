"""Read-only trusted cutover inventory for free GitHub Actions ServiceIT worker.

The live REST webhook is read only from an environment variable and never printed.
No CRM writes, model calls, or source changes. Does not create a test item.
"""
from __future__ import annotations

import os

import httpx

from .serviceit_enrichment_worker import Rest


def latest_existing_card_id(rest) -> int:
    result = rest("crm.item.list", {
        "entityTypeId": 1062,
        "useOriginalUfNames": "Y",
        "filter": {"categoryId": 21},
        "order": {"id": "DESC"},
        # Bitrix24 drops entityTypeId from crm.item.list select. Verify
        # the record identity separately via a read-only crm.item.get.
        "select": ["id", "categoryId"],
        "start": 0,
    })
    items = result.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("CUTOVER_INVENTORY_INVALID")
    seen = set()
    previous = None
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("CUTOVER_ITEM_INVALID")
        try:
            card_id = int(item.get("id", ""))
        except (ValueError, TypeError) as exc:
            raise ValueError("CUTOVER_CARD_ID_INVALID") from exc
        if card_id < 1 or card_id > 2_000_000_000:
            raise ValueError("CUTOVER_CARD_ID_OUT_OF_BOUNDS")
        if (("entityTypeId" in item and str(item["entityTypeId"]) != "1062")
                or str(item.get("categoryId")) != "21"):
            raise ValueError("CUTOVER_CATEGORY_MISMATCH")
        if card_id in seen or (previous is not None and card_id >= previous):
            raise ValueError("CUTOVER_RESULT_NOT_DESCENDING")
        seen.add(card_id)
        previous = card_id
    if not items:
        return 7
    first = int(items[0]["id"])
    # The list operation hides the type marker. Get the highest existing
    # item read-only and verify the server-provided entity type/category.
    # This check also permits historical #3/#7 (read-only, never a write).
    latest = rest("crm.item.get", {
        "entityTypeId": 1062,
        "id": first,
        "useOriginalUfNames": "Y",
    }).get("item")
    if (not isinstance(latest, dict) or str(latest.get("id")) != str(first)
            or str(latest.get("entityTypeId")) != "1062"
            or str(latest.get("categoryId")) != "21"):
        raise ValueError("CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED")
    return max(7, first)


def main() -> int:
    webhook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not webhook:
        print("SERVICEIT_CLOUD_CUTOVER=BLOCKED_MISSING_WEBHOOK")
        return 2
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
            latest = latest_existing_card_id(Rest(webhook, client))
        print(f"SERVICEIT_CLOUD_CUTOVER_MAX_ID={latest}")
        print("SERVICEIT_CLOUD_CUTOVER_CRM_WRITES=0")
        return 0
    except Exception:
        print("SERVICEIT_CLOUD_CUTOVER=UNVERIFIED_READ_ONLY")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
