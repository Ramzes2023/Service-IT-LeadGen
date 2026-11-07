"""VPS Bitrix new-card REST list compatibility preflight. READONLY only.

Makes crm.item.list requests with strict pagination, a known historical
positive control and a short future-only scan. Returns counts, never card
data or customer details. No model, queue, ledger, task or CRM write calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sys

import httpx

from .serviceit_vps_pg_queue import secret_file
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import (
    PollBlocked, calibrate_known_card, discover,
)

SCAN_MINUTES = 10


def inspect_api(env: dict, *, client: httpx.Client, now: datetime | None = None) -> dict:
    """Return only safe aggregate counts; never enumerate or store IDs."""
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    base = validate_webhook(webhook)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("UTC_AWARE_TIME_REQUIRED")
    current = current.astimezone(timezone.utc)
    lower = current - timedelta(minutes=SCAN_MINUTES)

    def rest(method: str, args: dict) -> dict:
        return list_only(client, base, method, args)

    # Prevent a misleading PASS when Bitrix silently returns [] for every
    # request, as observed in this portal on an earlier occasion.
    calibrate_known_card(rest)
    cards = discover(rest, lower, current)
    return {
        "status": "BITRIX_1062_21_POLL_READONLY_COMPATIBLE",
        "positive_control_card3": "PASS",
        "new_card_window_minutes": SCAN_MINUTES,
        "new_card_candidates": len(cards),
        "crm_writes": 0,
        "queue_inserts": 0,
        "ai_calls": 0,
        "secrets_disclosed": False,
    }


def main() -> int:
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = inspect_api(dict(os.environ), client=client)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        # Only fixed internal reason codes are safe to print. No URLs,
        # CRM data or HTTP exception bodies are ever written to stdout.
        code = "UNVERIFIED_API_SHAPE_OR_CONNECTION"
        if isinstance(exc, PollBlocked):
            reason = str(exc)
            if reason in {
                "POLL_CALIBRATION_POSITIVE_MISSING",
                "POLL_CALIBRATION_IDENTITY_INVALID",
                "POLL_CALIBRATION_PAGINATION_INVALID",
                "POLL_LIST_RESPONSE_INVALID",
                "POLL_LIST_SHAPE_UNTRUSTED",
                "POLL_LIST_PAGE_OVERSIZED",
                "POLL_LIST_TOTAL_INVALID",
                "POLL_LIST_EMPTY_PAGE_UNVERIFIED",
                "POLL_ITEM_INVALID",
                "POLL_ITEM_ID_INVALID",
                "POLL_ITEM_SCOPE_MISMATCH",
                "POLL_ITEM_BEFORE_ACTIVATION_OR_FUTURE",
                "POLL_ITEM_DUPLICATE",
                "POLL_RESULT_LIMIT_EXCEEDED",
                "POLL_INCOMPLETE_PAGINATION",
                "POLL_NEXT_UNTRUSTED",
                "POLL_TOO_MANY_PAGES_NO_DISPATCH",
            }:
                code = reason
        print(json.dumps({
            "status": "BITRIX_POLL_READONLY_BLOCKED",
            "reason": code,
            "crm_writes": 0,
            "queue_inserts": 0,
            "ai_calls": 0,
            "secrets_disclosed": False,
        }, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
