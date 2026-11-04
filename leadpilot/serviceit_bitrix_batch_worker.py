"""Bounded all-new-card Bitrix24 -> real AI batch catch-up (no demo records).

Runs only in a trusted, scheduled GitHub Actions job with existing owner-enabled
1062/21 gates. Before any write it positively calibrates the CRM list API with
historical card #3, validates complete filtered pagination, and checks the same
encrypted per-card receipt used by the signed event writer.

Each fresh unfenced card is processed sequentially by the EXISTING exact-item
worker, which performs crm.item.get, the nine AI fields, exact readback and a
durable one-use reservation. The scheduler signs an internal, ID-only envelope
with the existing bridge key; this is not a public unauthenticated endpoint.

No old card, task, deal, lead, stage, contact or formal Score is changed here.
Unknown evidence stays HOLD. Remote ambiguous writes are never retried.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
import time

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import (
    EVENT, LIVE_FLAG, REPOSITORY, PORTAL, gate as exact_gate,
    run as run_exact_event,
)
from .serviceit_bitrix_poll_fallback import (
    POLL_FLAG, PollBlocked, after_activation, calibrate_known_card,
    discover, encrypted_receipt_exists, signed_payload,
)

MAX_NEW_CARDS_PER_RUN = 6


class BatchBlocked(ValueError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BatchBlocked(code)


def list_only(client: httpx.Client, base: str, method: str, args: dict) -> dict:
    """Bounded verified CRM listing; preserve top-level Bitrix pagination."""
    require(method == "crm.item.list" and type(args) is dict
            and args.get("entityTypeId") == 1062
            and args.get("useOriginalUfNames") == "N"
            and args.get("select") == ["id", "categoryId", "createdTime"]
            and args.get("start") in (0, 50, 100, 150),
            "BATCH_LIST_SCOPE_INVALID")
    filters = args.get("filter")
    require(type(filters) is dict and (
            filters == {"id": 3}
            or (filters.get("categoryId") == 21
                and set(filters) == {"categoryId", ">=createdTime"})),
            "BATCH_LIST_FILTER_INVALID")
    response = client.post(
        base + "/crm.item.list.json", json=args,
        timeout=25, follow_redirects=False,
    )
    require(response.status_code == 200
            and 0 < len(response.content) <= 512000,
            "BATCH_LIST_HTTP_UNVERIFIED")
    data = response.json()
    require(type(data) is dict and "error" not in data
            and type(data.get("result")) is dict,
            "BATCH_LIST_RESPONSE_UNTRUSTED")
    result = data["result"]
    for key in ("next", "total"):
        if key in data:
            require(key not in result or result[key] == data[key],
                    "BATCH_PAGINATION_CONFLICT")
            result[key] = data[key]
    return result


def run_batch(
    env: dict, *, client: httpx.Client,
    now: datetime | None = None,
    limit: int = MAX_NEW_CARDS_PER_RUN,
) -> dict:
    """Process up to `limit` actual NEW 1062/21 cards; no history backfill."""
    require(type(env) is dict and isinstance(client, httpx.Client)
            and type(limit) is int and 1 <= limit <= MAX_NEW_CARDS_PER_RUN,
            "BATCH_ARGUMENTS_INVALID")
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") == "schedule",
            "BATCH_SCHEDULE_SCOPE_INVALID")
    if env.get("SERVICEIT_BITRIX_POLL_ENABLED") != POLL_FLAG:
        return {"status": "BATCH_DISABLED", "crm_writes": 0,
                "attempted": 0, "candidates": 0}
    require(env.get("SERVICEIT_BITRIX_EVENT_AI_ENABLED") == LIVE_FLAG,
            "BATCH_AI_NOT_AUTHORIZED")
    # The existing exact-item worker retains its own HMAC, ledger and privacy
    # gates. Never skip those gates, even inside the trusted Actions runner.
    exact_env = dict(env, GITHUB_EVENT_NAME="repository_dispatch")
    require(exact_gate(exact_env) == "READY", "BATCH_EXACT_WORKER_NOT_READY")
    secret = env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", "")
    require(type(secret) is str and len(secret) >= 32,
            "BATCH_BRIDGE_KEY_MISSING")
    webhook = env.get("SERVICEIT_BITRIX_WEBHOOK", "")
    base = validate_webhook(webhook)
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "BATCH_PORTAL_FORBIDDEN")
    current = now or datetime.now(timezone.utc)
    require(isinstance(current, datetime) and current.tzinfo is not None
            and current.utcoffset() is not None, "BATCH_CLOCK_INVALID")
    current = current.astimezone(timezone.utc)
    activation = after_activation(env, current)

    def crm_listing(method: str, args: dict) -> dict:
        return list_only(client, base, method, args)

    # Positive control detects the portal's previously seen [[]] / nested
    # empty-shape incident before declaring that no new cards exist.
    calibrate_known_card(crm_listing)
    cards = discover(crm_listing, activation, current)
    result = {
        "status": "BATCH_NO_NEW_CARDS" if not cards else "BATCH_ALL_NEW_PROCESSED",
        "candidates": len(cards), "attempted": 0, "already_fenced": 0,
        "processed": 0, "needs_review": 0, "crm_writes": 0, "pending": False,
    }
    for card in cards:
        if encrypted_receipt_exists(
            client, card, env.get("GITHUB_TOKEN", ""),
            env.get("SERVICEIT_GH_LEDGER_KEY", ""),
        ):
            result["already_fenced"] += 1
            continue
        if result["attempted"] >= limit:
            result["pending"] = True
            result["status"] = "BATCH_PENDING_MORE"
            break
        # Only filtered and timestamp-verified exact IDs enter the existing
        # identical event handling path; the signed envelope is never exported.
        envelope = {
            "action": EVENT,
            "repository": {"full_name": REPOSITORY},
            "client_payload": signed_payload(card, secret, int(time.time())),
        }
        outcome = run_exact_event(envelope, exact_env, client=client)
        result["attempted"] += 1
        if outcome.get("status") == "SERVICEIT_NEW_CARD_EVENT_PROCESSED":
            result["processed"] += 1
            if outcome.get("crm_writes") == 1:
                result["crm_writes"] += 1
        else:
            result["needs_review"] += 1
            result["status"] = "BATCH_REVIEW_REQUIRED"
            # Stop the batch: an ambiguous provider/CRM/receipt result must
            # not be hidden beneath success on later unrelated cards.
            result["pending"] = True
            break
    return result


def main() -> int:
    safe = {"status": "BATCH_BLOCKED_OR_UNCERTAIN", "crm_writes": "UNVERIFIED",
            "attempted": "UNVERIFIED", "replay_allowed": False}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            safe = run_batch(dict(os.environ), client=client)
        print(json.dumps(safe, sort_keys=True), flush=True)
        return 0 if safe["status"] in (
            "BATCH_DISABLED", "BATCH_NO_NEW_CARDS",
            "BATCH_ALL_NEW_PROCESSED", "BATCH_PENDING_MORE") else 2
    except (BatchBlocked, PollBlocked, httpx.HTTPError, ValueError,
            TypeError, KeyError):
        print(json.dumps(safe, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
