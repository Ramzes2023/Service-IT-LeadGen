"""Write an unambiguous signal icon into the TITLE of future 1062/21 cards.

The Kanban tile always shows its native title, regardless of administrator
selection of optional AI_SUMMARY fields. No title shortening, no edits to old
cards, stages, deal pipelines, sales scores, contacts or customer records.

An encrypted per-card GitHub CAS fence is durably reserved *before* the first
write; ambiguous Bitrix replies never trigger blind retries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_dispatch_worker import (
    EncryptedEventFence, PORTAL, REPOSITORY, verify_event,
)
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, date_value, discover,
)
from .serviceit_enrichment_worker import PREFIX, fingerprint
from .serviceit_stage4_fns_dedupe_watch import read_item
from .serviceit_three_signal_triage import classify_signal
from .serviceit_kanban_tile import DISPLAY_BADGES, ALL_PREFIXES

MARKER = "SERVICEIT_KANBAN_3_SIGNAL_TITLES_ONCE_V1"
FLAG = "YES_1062_21_THREE_SIGNAL_TITLE"
MAX_SCAN = 20
MAX_TITLE = 255


class KanbanTitleBlocked(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise KanbanTitleBlocked(reason)


class KanbanTitleFence(EncryptedEventFence):
    def url(self, card):
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "TITLE_CARD_NOT_FUTURE")
        digest = hmac.new(
            self.path_key,
            f"kanban-three-signal-title-v1:1062:21:{card}".encode("ascii"),
            sha256,
        ).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/kanban-title-once/{digest}.enc")


def title_decision(item, *, now):
    """Conservative exact-title proposal with NO truncation of original text."""
    title = item.get("title")
    require(type(title) is str and 0 < len(title.strip()) <= 240,
            "TITLE_EMPTY_OR_TOO_LONG_FOR_SAFE_PREFIX")
    require(title == title.strip() and not any(
        ord(ch) < 32 or ord(ch) == 127 for ch in title
    ), "TITLE_CONTROL_CHARACTERS_NOT_SUPPORTED")
    triage = classify_signal({
        "title": title,
        "description": "",
        "signal_kind": item.get(PREFIX + "SIGNAL_KIND") or "",
        "source": item.get(PREFIX + "SIGNAL_SOURCE") or "",
        "region": item.get(PREFIX + "REGION") or "",
        "company": "",
    }, now=now)
    badge = DISPLAY_BADGES[triage["type"]]
    for pref in ALL_PREFIXES:
        if title.startswith(pref + " · "):
            if pref == badge:
                return None, "ALREADY_CORRECT_NATIVE_TITLE"
            # A source/manager created a different icon. Never rewrite it
            # automatically, even if classifier currently disagrees.
            return None, "EXISTING_DIFFERENT_BADGE_HOLD"
    proposed = f"{badge} · {title}"
    require(len(proposed) <= MAX_TITLE, "TITLE_WOULD_EXCEED_MAX_LENGTH")
    require(proposed != title, "TITLE_NO_EFFECT")
    return proposed, "TITLE_UPDATE_REQUIRED"


def title_rest(client, base, card, fields):
    require(type(card) is int and 8 <= card <= 2_000_000_000
            and card not in (3, 7)
            and type(fields) is dict and set(fields) == {"title"}
            and type(fields["title"]) is str
            and 1 <= len(fields["title"]) <= MAX_TITLE
            and any(fields["title"].startswith(x + " · ")
                    for x in DISPLAY_BADGES.values()),
            "TITLE_WRITE_ALLOWLIST_INVALID")
    try:
        response = client.post(base + "/crm.item.update.json", json={
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            "fields": fields,
        }, follow_redirects=False, timeout=25)
        require(response.status_code == 200
                and 0 < len(response.content) <= 400_000,
                "TITLE_UPDATE_HTTP_UNVERIFIED")
        body = response.json()
        require(type(body) is dict and "error" not in body
                and type(body.get("result")) is dict,
                "TITLE_UPDATE_RESPONSE_UNVERIFIED")
    except (ValueError, httpx.HTTPError, TypeError):
        raise KanbanTitleBlocked("TITLE_WRITE_RESPONSE_UNCERTAIN") from None


def process_one(client, env, base, card, fence, *, now):
    before = read_item(client, base, card)
    created = date_value(before.get("createdTime"))
    require(after_activation(env, now) <= created <= now,
            "TITLE_HISTORICAL_OR_FUTURE_ITEM")
    proposed, decision = title_decision(before, now=now)
    if proposed is None:
        return decision
    latest = read_item(client, base, card)
    require(fingerprint(latest) == fingerprint(before),
            "TITLE_CONCURRENT_CARD_CHANGE")
    if fence.reserve(card) != "RESERVED":
        return "TITLE_ALREADY_RESERVED_NO_RETRY"
    fence.receipt = {"expected": {
        "title_sha256": sha256(proposed.encode("utf-8")).hexdigest(),
        "before_sha256": fingerprint(before),
    }}
    require(fence.finalize(card, "UNCERTAIN"),
            "TITLE_PREWRITE_CHECKPOINT_MISSING")
    try:
        title_rest(client, base, card, {"title": proposed})
        after = read_item(client, base, card)
        require(after.get("title") == proposed,
                "TITLE_NOT_APPLIED_EXACTLY")
        allowed = {"title", "updatedTime", "updatedBy",
                   "lastActivityTime", "lastActivityBy"}
        require(all(after.get(k) == v
                    for k, v in latest.items() if k not in allowed),
                "TITLE_NON_TARGET_FIELD_CHANGED")
    except Exception:
        return "TITLE_UPDATE_UNCERTAIN_NO_RETRY"
    return ("TITLE_EXACT_READBACK_VERIFIED"
            if fence.finalize(card, "VERIFIED")
            else "TITLE_READBACK_OK_LEDGER_UNCERTAIN")


def check_event(env):
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("SERVICEIT_KANBAN_3_SIGNAL_TITLE_ENABLED") == FLAG
            and env.get("GITHUB_EVENT_NAME")
                in ("schedule", "issue_comment", "repository_dispatch"),
            "TITLE_DEPLOYMENT_GATE_INVALID")
    if env["GITHUB_EVENT_NAME"] == "issue_comment":
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        require(path.is_file() and 0 < path.stat().st_size < 256_000,
                "TITLE_OWNER_EVENT_FILE_INVALID")
        event = json.loads(path.read_text("utf-8"))
        require(event.get("action") == "created"
                and (event.get("repository") or {}).get("full_name") == REPOSITORY
                and (event.get("issue") or {}).get("number") == 200
                and not (event.get("issue") or {}).get("pull_request")
                and (event.get("sender") or {}).get("login") == "Ramzes2023"
                and (event.get("comment") or {}).get("body") == MARKER,
                "TITLE_OWNER_EVENT_INVALID")


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    check_event(env)
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "TITLE_PORTAL_UNVERIFIED")
    list_rest = lambda m, p: list_only(client, base, m, p)
    calibrate_known_card(list_rest)
    cards = discover(list_rest, after_activation(env, now), now)
    require(len(cards) <= 200, "TITLE_NEW_ITEM_DISCOVERY_OVER_BUDGET")
    if env["GITHUB_EVENT_NAME"] == "repository_dispatch":
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        require(path.is_file() and 0 < path.stat().st_size < 256_000,
                "TITLE_SIGNED_EVENT_MISSING")
        card = verify_event(json.loads(path.read_text("utf-8")),
                            env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", ""))
        cards = [c for c in cards if c == card]
    total = {"status": "KANBAN_THREE_SIGNAL_TITLE_AUTO",
             "eligible_new_cards": len(cards), "checked": 0,
             "native_titles_verified": 0, "already_labeled": 0,
             "held_or_uncertain": 0, "historic_records_changed": 0,
             "other_crm_fields_changed": 0, "outreach": 0,
             "shared_kanban_view_configured": False}
    if not cards:
        return total
    fence = KanbanTitleFence(client, env.get("GITHUB_TOKEN", ""),
                             env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    windows = max(1, (len(cards) + MAX_SCAN - 1) // MAX_SCAN)
    slot = (int(now.timestamp()) // 1200) % windows
    selected = cards[slot * MAX_SCAN:(slot + 1) * MAX_SCAN]
    for card in selected:
        try:
            decision = process_one(client, env, base, card, fence, now=now)
        except Exception:
            decision = "TITLE_READ_OR_EVIDENCE_HOLD"
        total["checked"] += 1
        total["native_titles_verified"] += decision == "TITLE_EXACT_READBACK_VERIFIED"
        total["already_labeled"] += decision == "ALREADY_CORRECT_NATIVE_TITLE"
        total["held_or_uncertain"] += decision not in (
            "TITLE_EXACT_READBACK_VERIFIED", "ALREADY_CORRECT_NATIVE_TITLE")
        if decision in ("TITLE_UPDATE_UNCERTAIN_NO_RETRY",
                        "TITLE_READBACK_OK_LEDGER_UNCERTAIN"):
            break
    return total


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            output = run(client, dict(os.environ))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "KANBAN_TITLE_AUTO_HOLD",
                          "historic_records_changed": 0,
                          "other_crm_fields_changed": 0,
                          "shared_kanban_view_configured": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
