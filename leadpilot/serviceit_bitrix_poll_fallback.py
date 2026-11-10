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
import base64
import binascii
from hashlib import sha256
import hmac
import json
import os
import re
import sys
import time
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .serviceit_bitrix_dispatch_worker import (
    EVENT, LIVE_FLAG, MAX_CARD, MIN_CARD, PORTAL, REPOSITORY,
    BRANCH as LEDGER_BRANCH, FOLDER as LEDGER_FOLDER,
)
from .bitrix_metadata_audit import validate_webhook
from .serviceit_safe_crm_read_retry import post_read

POLL_FLAG = "YES_1062_21_READONLY"
WINDOW = timedelta(days=7)
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
    """Prove Bitrix can read a real 1062/21 card, without relying on deleted ID 3.

    Prefer the historical read-only control. If that card is absent, select
    ONE real card from the bounded category-21 list and confirm its exact ID
    via an independent filtered request. No updates, queue inserts or writes.
    Empty/malformed/ignored-filter responses fail closed.
    """
    fields = ["id", "categoryId", "createdTime"]
    response = rest("crm.item.list", {
        "entityTypeId": 1062, "useOriginalUfNames": "N",
        "filter": {"id": 3}, "select": fields, "start": 0,
    })
    require(type(response) is dict, "POLL_CALIBRATION_RESULT_INVALID")
    items = response.get("items")
    require(type(items) is list, "POLL_CALIBRATION_POSITIVE_MISSING")
    control_id = 3
    if not items:
        require(response.get("next") is None
                and response.get("total") in (None, 0),
                "POLL_CALIBRATION_PAGINATION_INVALID")
        # A historical card may have been deleted or moved. A category list
        # provides a current candidate, not proof until the exact-ID GET.
        candidates = rest("crm.item.list", {
            "entityTypeId": 1062, "useOriginalUfNames": "N",
            "filter": {"categoryId": 21},
            "order": {"id": "ASC"}, "select": fields, "start": 0,
        })
        require(type(candidates) is dict, "POLL_CALIBRATION_RESULT_INVALID")
        page = candidates.get("items")
        require(type(page) is list and 1 <= len(page) <= 50,
                "POLL_CALIBRATION_POSITIVE_MISSING")
        first = page[0]
        require(type(first) is dict
                and str(first.get("categoryId")) == "21",
                "POLL_CALIBRATION_IDENTITY_INVALID")
        raw_id = first.get("id")
        require((type(raw_id) is int or
                 (type(raw_id) is str
                  and re.fullmatch(r"[1-9][0-9]{0,9}", raw_id)))
                and MIN_CARD <= int(raw_id) <= MAX_CARD,
                "POLL_CALIBRATION_IDENTITY_INVALID")
        date_value(first.get("createdTime"))
        control_id = int(raw_id)
        response = rest("crm.item.list", {
            "entityTypeId": 1062, "useOriginalUfNames": "N",
            "filter": {"id": control_id}, "select": fields, "start": 0,
        })
        require(type(response) is dict, "POLL_CALIBRATION_RESULT_INVALID")
        items = response.get("items")
    require(type(items) is list and len(items) == 1,
            "POLL_CALIBRATION_POSITIVE_MISSING")
    item = items[0]
    require(type(item) is dict and str(item.get("id")) == str(control_id)
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


def encrypted_receipt_exists(client: httpx.Client, card: int,
                             github_token: str, ledger_key: str) -> bool:
    """Observe the exact GitHub one-use reservation, never read card data.

    A prior accepted event may have already processed this item. Without this
    check, an overlapping seven-day schedule would flood Actions with
    duplicate repository_dispatch events and discard pending jobs.
    """
    require(type(card) is int and MIN_CARD <= card <= MAX_CARD,
            "POLL_LEDGER_CARD_ID_INVALID")
    require(type(ledger_key) is str and len(ledger_key) == 44
            and type(github_token) is str and len(github_token) >= 12,
            "POLL_LEDGER_CREDENTIALS_INVALID")
    try:
        key = base64.urlsafe_b64decode(ledger_key.encode("ascii"))
        require(len(key) == 32, "POLL_LEDGER_KEY_INVALID")
    except (ValueError, TypeError, UnicodeError):
        raise PollBlocked("POLL_LEDGER_KEY_INVALID") from None
    path_key = hmac.new(
        key, f"serviceit-v1:1062:21:{card}".encode(), sha256).hexdigest()
    url = (f"https://api.github.com/repos/{REPOSITORY}/contents/"
           f"{LEDGER_FOLDER}/{path_key}.enc")
    response = client.get(
        url, params={"ref": LEDGER_BRANCH},
        headers={"Authorization": "Bearer " + github_token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=20, follow_redirects=False)
    require(response.status_code in (200, 404), "POLL_LEDGER_READ_UNVERIFIED")
    if response.status_code == 404:
        return False
    try:
        record = response.json()
        require(type(record) is dict and type(record.get("content")) is str
                and len(record["content"]) <= 32768,
                "POLL_LEDGER_RECEIPT_INVALID")
        plaintext = Fernet(ledger_key.encode("ascii")).decrypt(
            base64.b64decode(record["content"]))
        receipt = json.loads(plaintext)
        require(type(receipt) is dict
                and receipt.get("schema") == "serviceit-event-v1"
                and type(receipt.get("card")) is int
                and receipt["card"] == card,
                "POLL_LEDGER_RECEIPT_MISMATCH")
    except (ValueError, TypeError, UnicodeError, InvalidToken, KeyError,
            binascii.Error):
        raise PollBlocked("POLL_LEDGER_RECEIPT_UNVERIFIED") from None
    return True


OWNER_ONCE_MARKER = "SERVICEIT_OWNER_NEW_CARD_LIVE_ONCE_V1"
OWNER_ONCE_ISSUE = 74
OWNER_LOGIN = "Ramzes2023"


def owner_once_event_verified(env: dict, *, marker: str = OWNER_ONCE_MARKER) -> bool:
    """Validate one exact owner comment, never ingest untrusted comment text.

    This authorizes an immediate production-equivalent scan of future-only
    cards. It does NOT authorize creating a new CRM card or changing a
    historical record. Both existing live GitHub variable gates still apply.
    """
    if (env.get("GITHUB_EVENT_NAME") != "issue_comment"
            or env.get("GITHUB_ACTOR") != OWNER_LOGIN):
        return False
    path = env.get("GITHUB_EVENT_PATH")
    if not isinstance(path, str) or not path:
        return False
    try:
        file = __import__("pathlib").Path(path)
        if not file.is_file() or not 0 < file.stat().st_size <= 256_000:
            return False
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, UnicodeError):
        return False
    if not isinstance(payload, dict):
        return False
    repo = payload.get("repository")
    sender = payload.get("sender")
    issue = payload.get("issue")
    comment = payload.get("comment")
    return (payload.get("action") == "created"
            and isinstance(repo, dict)
            and repo.get("full_name") == REPOSITORY
            and isinstance(sender, dict)
            and sender.get("login") == OWNER_LOGIN
            and isinstance(issue, dict)
            and issue.get("number") == OWNER_ONCE_ISSUE
            and not issue.get("pull_request")
            and isinstance(comment, dict)
            and comment.get("body") == marker
            and isinstance(comment.get("user"), dict)
            and comment["user"].get("login") == OWNER_LOGIN)


def run(env: dict, *, client: httpx.Client, now: datetime | None = None,
        probe_only: bool = False, owner_once: bool = False) -> dict:
    """Probe is GET/read-only only. Live requires BOTH explicit owner gates."""
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") in ("schedule", "workflow_dispatch", "issue_comment"),
            "POLL_GITHUB_SCOPE_INVALID")
    if not probe_only:
        if owner_once:
            require(owner_once_event_verified(env), "POLL_OWNER_ONCE_NOT_AUTHORIZED")
        else:
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
                     or (probe_only
                         and type(params.get("filter")) is dict
                         and set(params["filter"]) == {"id"}
                         and type(params["filter"]["id"]) is int
                         and (params["filter"]["id"] == 3 or
                              MIN_CARD <= params["filter"]["id"] <= MAX_CARD)))
                and params.get("start") in (0, 50, 100, 150),
                "POLL_READONLY_METHOD_REQUIRED")
        # Only read-only item.list can retry on temporary 5xx/network errors.
        # Signed GitHub dispatch and every CRM mutation remain at-most-once.
        response = post_read(client, base, method, params, timeout=25)
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
    require(type(env.get("SERVICEIT_GH_LEDGER_KEY")) is str,
            "POLL_LEDGER_KEY_MISSING")
    attempted = 0
    already_fenced = 0
    # GitHub concurrency retains at most one running and one pending job.
    # Send at most ONE new event per 10-min tick; the seven-day window and
    # encrypted one-use ledger allow other unfenced cards to drain later.
    # Never advance a local-only cursor or silently mark a card processed.
    for card in cards:
        if encrypted_receipt_exists(client, card, env["GITHUB_TOKEN"],
                                    env["SERVICEIT_GH_LEDGER_KEY"]):
            already_fenced += 1
            continue
        dispatch(client, card, env["SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"],
                 env["GITHUB_TOKEN"], int(time.time()))
        attempted = 1
        break
    return {"status": "POLL_DISPATCHED_ID_ONLY", "crm_writes": 0,
            "dispatches": attempted, "already_fenced": already_fenced,
            "pending_capped": len(cards) > already_fenced + attempted}


def main() -> int:
    env = dict(os.environ)
    try:
        flags = sys.argv[1:]
        require(all(flag in ("--probe", "--owner-once") for flag in flags)
                and len(flags) <= 1, "POLL_CLI_FLAGS_INVALID")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            outcome = run(env, client=client, probe_only="--probe" in flags,
                          owner_once="--owner-once" in flags)
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
