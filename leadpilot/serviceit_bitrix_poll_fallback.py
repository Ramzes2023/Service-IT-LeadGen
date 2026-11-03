"""No-outgoing-webhook Bitrix24 new-card discovery: bounded, ID-only, fail-closed.

GitHub Actions calls an existing authorized incoming REST webhook with a
time/category filter. It signs only verified *new card IDs* and delivers them
to the already CI-tested encrypted one-use Bitrix AI worker via repo dispatch.

Nothing auto-enables this workflow: both independent GitHub variables
SERVICEIT_BITRIX_POLL_ENABLED and SERVICEIT_BITRIX_EVENT_AI_ENABLED must
be explicitly set by the owner after a live READ-ONLY list API preflight.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
import re
import sys
import time
from uuid import uuid4

import httpx

from .serviceit_bitrix_dispatch_worker import (
    EVENT, LIVE_FLAG, MAX_CARD, MIN_CARD, PORTAL, REPOSITORY,
)
from .bitrix_metadata_audit import validate_webhook

POLL_FLAG = "YES_1062_21_READONLY"
WINDOW = timedelta(hours=2)
MAX_PAGES = 4
MAX_ITEMS = 200
MAX_FUTURE_DRIFT = timedelta(minutes=2)
DISPATCH_URI = f"https://api.github.com/repos/{REPOSITORY}/dispatches"


class PollBlocked(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PollBlocked(reason)


def date_value(value: str) -> datetime:
    require(isinstance(value, str) and len(value) <= 50, "POLL_DATE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PollBlocked("POLL_DATE_INVALID") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "POLL_DATE_TIMEZONE_MISSING")
    return parsed.astimezone(timezone.utc)


def after_activation(env: dict, now: datetime) -> datetime:
    require(now.tzinfo is not None, "POLL_NOW_INVALID")
    started = date_value(env.get("SERVICEIT_BITRIX_POLL_START_UTC", ""))
    require(started <= now and started >= now - timedelta(days=365),
            "POLL_ACTIVATION_TIMESTAMP_INVALID")
    return max(started, now - WINDOW)


def build_query(lower: datetime, start: int) -> dict:
    return {
        "entityTypeId": 1062,
        "useOriginalUfNames": "N",
        "filter": {"categoryId": 21,
                   ">=createdTime": lower.isoformat(timespec="seconds")},
        "order": {"id": "ASC"},
        "select": ["id", "categoryId", "createdTime"],
        "start": start,
    }


def discover(rest, start: datetime, now: datetime) -> list[int]:
    """Strict time-verified list with bounded pagination; never enumerate history.

    No dispatch occurs until the WHOLE discovery run has been validated.
    A malformed or truncated page blocks the entire batch.
    """
    result_ids = []
    seen = set()
    next_start = 0
    for _ in range(MAX_PAGES):
        response = rest("crm.item.list", build_query(start, next_start))
        require(type(response) is dict, "POLL_LIST_RESPONSE_INVALID")
        items = response.get("items")
        require(type(items) is list, "POLL_LIST_SHAPE_UNTRUSTED")
        require(len(items) <= 50, "POLL_LIST_PAGE_OVERSIZED")
        total = response.get("total")
        if total is not None:
            require(type(total) is int and 0 <= total <= 2_000_000_000,
                    "POLL_LIST_TOTAL_INVALID")
        if not items:
            require(response.get("next") is None and (total in (None, 0) or next_start >= total),
                    "POLL_LIST_EMPTY_PAGE_UNVERIFIED")
        # A Bitrix installation may return a flat list but ignore a time
        # filter. Individual timestamps are validated below; NEVER trust
        # the server-side filter alone to protect historical cards.
        for item in items:
            require(type(item) is dict, "POLL_ITEM_INVALID")
            raw_id = item.get("id")
            require(type(raw_id) is int or
                    (type(raw_id) is str and re.fullmatch(r"[1-9][0-9]{0,9}", raw_id)),
                    "POLL_ITEM_ID_INVALID")
            card = int(raw_id)
            require(MIN_CARD <= card <= MAX_CARD and card not in (3, 7)
                    and str(item.get("categoryId")) == "21"
                    and ("entityTypeId" not in item
                         or str(item["entityTypeId"]) == "1062"),
                    "POLL_ITEM_SCOPE_MISMATCH")
            created = date_value(item.get("createdTime"))
            require(start <= created <= now + MAX_FUTURE_DRIFT,
                    "POLL_ITEM_BEFORE_ACTIVATION_OR_FUTURE")
            require(card not in seen, "POLL_ITEM_DUPLICATE")
            seen.add(card)
            result_ids.append(card)
        require(len(result_ids) <= MAX_ITEMS, "POLL_RESULT_LIMIT_EXCEEDED")
        next_page = response.get("next")
        if next_page is None:
            # Must not silently lose a page when 'total' is known.
            require(total is None or total <= next_start + len(items),
                    "POLL_INCOMPLETE_PAGINATION")
            return result_ids
        require(type(next_page) is int and next_page > next_start
                and next_page <= MAX_ITEMS, "POLL_NEXT_UNTRUSTED")
        next_start = next_page
    raise PollBlocked("POLL_TOO_MANY_PAGES_NO_DISPATCH")


def calibrate_known_card(rest) -> None:
    """Read-only positive control: N-mode ID 3 must expose creation time.

    A recent-list query that happens to return [] does NOT prove new-card
    discovery works. Pair it with a fixed historical ID positive control.
    ID 3 is never dispatched or updated; this is only an API shape check.
    """
    response = rest("crm.item.list", {
        "entityTypeId": 1062,
        "useOriginalUfNames": "N",
        "filter": {"id": 3},
        "select": ["id", "categoryId", "createdTime"],
        "start": 0,
    })
    require(type(response) is dict, "POLL_CALIBRATION_RESULT_INVALID")
    items = response.get("items")
    require(type(items) is list and len(items) == 1,
            "POLL_CALIBRATION_POSITIVE_MISSING")
    item = items[0]
    require(type(item) is dict and str(item.get("id")) == "3"
            and str(item.get("categoryId")) == "21",
            "POLL_CALIBRATION_IDENTITY_INVALID")
    date_value(item.get("createdTime"))
    require(response.get("next") is None
            and response.get("total") in (None, 1),
            "POLL_CALIBRATION_PAGINATION_INVALID")


def signed_payload(item_id: int, secret: str, now: int) -> dict:
    require(type(item_id) is int and MIN_CARD <= item_id <= MAX_CARD,
            "POLL_DISPATCH_ID_INVALID")
    require(type(secret) is str and len(secret) >= 32,
            "POLL_HMAC_MISSING")
    nonce = str(uuid4())
    message = f"v1|{PORTAL}|1062|{item_id}|{now}|{nonce}"
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), sha256).hexdigest()
    return {
        "version": 1, "portal": PORTAL,
        "entity_type_id": 1062, "item_id": item_id,
        "timestamp": now, "nonce": nonce, "signature": "sha256=" + digest,
    }


def dispatch(client: httpx.Client, card: int, secret: str, github_token: str, now: int):
    require(isinstance(client, httpx.Client) and type(github_token) is str
            and len(github_token) >= 12, "POLL_DISPATCH_CREDENTIALS_MISSING")
    response = client.post(
        DISPATCH_URI,
        headers={
            "Authorization": "Bearer " + github_token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"event_type": EVENT,
              "client_payload": signed_payload(card, secret, now)},
        follow_redirects=False, timeout=25,
    )
    require(response.status_code == 204, "POLL_GITHUB_DISPATCH_UNCONFIRMED")


def run(env: dict, *, client: httpx.Client, now: datetime | None = None,
        probe_only: bool = False) -> dict:
    """Probe is GET/read-only only. Live requires BOTH explicit owner gates."""
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") in ("schedule", "workflow_dispatch", "issue_comment"),
            "POLL_GITHUB_SCOPE_INVALID")
    if not probe_only:
        require(env.get("GITHUB_EVENT_NAME") == "schedule", "POLL_SCHEDULE_REQUIRED")
        if env.get("SERVICEIT_BITRIX_POLL_ENABLED") != POLL_FLAG:
            return {"status": "POLL_DISABLED", "crm_writes": 0, "dispatches": 0}
        require(env.get("SERVICEIT_BITRIX_EVENT_AI_ENABLED") == LIVE_FLAG,
                "POLL_AI_GATE_DISABLED")
    webhook = env.get("SERVICEIT_BITRIX_WEBHOOK")
    require(type(webhook) is str and webhook, "POLL_REST_SECRET_MISSING")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lower = (current - timedelta(minutes=10) if probe_only
             else after_activation(env, current))
    # Read-only REST call preserves Bitrix TOP-LEVEL "next" and "total".
    # The general CRM Rest wrapper intentionally strips these pagination
    # markers; using it here would risk dropping records after the first 50.
    base = validate_webhook(webhook)
    def list_only(method, params):
        require(method == "crm.item.list" and type(params) is dict
                and params.get("entityTypeId") == 1062
                and params.get("useOriginalUfNames") == "N"
                and params.get("select") == ["id", "categoryId", "createdTime"]
                and (params.get("filter", {}).get("categoryId") == 21
                     or (probe_only and params.get("filter") == {"id": 3}))
                and params.get("start") in (0, 50, 100, 150),
                "POLL_READONLY_METHOD_REQUIRED")
        response = client.post(base + "/crm.item.list.json", json=params,
                               follow_redirects=False, timeout=25)
        require(response.status_code == 200 and len(response.content) <= 512000,
                "POLL_REST_HTTP_UNVERIFIED")
        body = response.json()
        require(type(body) is dict and "error" not in body
                and type(body.get("result")) is dict,
                "POLL_REST_RESPONSE_INVALID")
        result = body["result"]
        for name in ("next", "total"):
            if name in body:
                # Reject conflicting metadata before trusting a page as complete.
                if name in result and result[name] != body[name]:
                    raise PollBlocked("POLL_PAGINATION_METADATA_CONFLICT")
                result[name] = body[name]
        return result
    if probe_only:
        calibrate_known_card(list_only)
    cards = discover(list_only, lower, current)
    if probe_only:
        return {"status": "POLL_READONLY_LIST_PASS", "crm_writes": 0,
                "dispatches": 0, "calibration": "KNOWN_ID_3_NMODE_PASS",
                "items_seen": len(cards)}
    require(type(env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY")) is str
            and len(env["SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"]) >= 32,
            "POLL_BRIDGE_HMAC_MISSING")
    require(type(env.get("GITHUB_TOKEN")) is str
            and len(env["GITHUB_TOKEN"]) >= 12, "POLL_GITHUB_TOKEN_MISSING")
    attempted = 0
    for card in cards:
        dispatch(client, card, env["SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"],
                 env["GITHUB_TOKEN"], int(time.time()))
        attempted += 1
    return {"status": "POLL_DISPATCHED_ID_ONLY", "crm_writes": 0,
            "dispatches": attempted}


def main() -> int:
    env = dict(os.environ)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            outcome = run(env, client=client, probe_only="--probe" in sys.argv[1:])
        print(json.dumps(outcome, sort_keys=True))
        return 0
    except (PollBlocked, ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
        # No CRM values, REST response bodies, tokens, IDs or request URLs printed.
        print(json.dumps({"status": "POLL_BLOCKED_REVIEW_REQUIRED",
                          "crm_writes": 0, "dispatches": "UNVERIFIED",
                          "reason": str(exc) if isinstance(exc, PollBlocked) else "TRANSPORT_OR_CONFIG"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
