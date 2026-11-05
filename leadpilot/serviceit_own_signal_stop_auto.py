"""Cloud ServiceIT-only STOP marker writer, future 1062/21 cards ONLY.

Reads independent versioned signal STOP list, performs exact INN match,
adds one internal HOLD comment only on positive match.  No company record,
stage, score, task, contact, email or existing historical CRM modifications.

One-use GitHub encrypted ledger ensures ambiguous writes never blindly retry.
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
from .serviceit_own_signal_stop import (
    read_rules, check as own_check, ALLOWED_REASONS,
)
from .serviceit_fns_msp_timeline import read_comments, ENTITY_TYPE

MARK = "[SERVICEIT_OWN_SIGNAL_STOP_V1]"
FLAG = "YES_1062_21_OWN_SIGNAL_STOP_NOTES"
MAX_CARDS = 20


class OwnStopWriteBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise OwnStopWriteBlocked(code)


class OwnSignalFence(EncryptedEventFence):
    def url(self, card):
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "OWN_STOP_CARD_OUT_OF_SCOPE")
        digest = hmac.new(self.path_key,
                          f"own-signal-stop-v1:1062:21:{card}".encode(),
                          sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/own-signal-stop-once/{digest}.enc")


def message(reason):
    require(reason in ALLOWED_REASONS, "OWN_STOP_REASON_NOT_ALLOWED")
    return (MARK + " HOLD: этот новый сигнал внесён в отдельный STOP-лист "
            "проекта «Сервис ИТ» (причина: " + reason + "). "
            "Не обрабатывать автоматически и не инициировать контакт. "
            "Это НЕ корпоративный STOP-лист и НЕ полный аудит CRM.")


def add_comment(client, base, card, value):
    require(type(card) is int and 8 <= card <= 2_000_000_000
            and card not in (3, 7)
            and value in [message(x) for x in ALLOWED_REASONS],
            "OWN_STOP_COMMENT_NOT_ALLOWLISTED")
    try:
        res = client.post(base + "/crm.timeline.comment.add.json", json={
            "fields": {"ENTITY_TYPE": ENTITY_TYPE,
                       "ENTITY_ID": card, "COMMENT": value},
        }, timeout=25, follow_redirects=False)
        require(res.status_code == 200 and 0 < len(res.content) <= 250000,
                "OWN_STOP_COMMENT_HTTP_UNVERIFIED")
        data = res.json()
        require(type(data) is dict and "error" not in data
                and str(data.get("result", "")).isdigit()
                and int(data["result"]) > 0, "OWN_STOP_COMMENT_ACK_INVALID")
        return int(data["result"])
    except (httpx.HTTPError, TypeError, ValueError):
        raise OwnStopWriteBlocked("OWN_STOP_COMMENT_RESULT_UNCERTAIN") from None


def process_one(client, env, base, card, fence, rules, *, now):
    before = read_item(client, base, card)
    require(date_value(before.get("createdTime")) >= after_activation(env, now),
            "OWN_STOP_HISTORICAL_CARD_FORBIDDEN")
    inn = before.get(PREFIX + "COMPANY_INN")
    status, reason = own_check(inn, rules)
    if status != "HOLD_OWN_SIGNAL_STOP":
        return status
    value = message(reason)
    existing = read_comments(client, base, card)
    ours = [row for row in existing if row["COMMENT"].startswith(MARK)]
    if ours:
        return ("ALREADY_EXACT_OWN_STOP_NOTE" if len(ours) == 1
                and ours[0]["COMMENT"] == value
                else "OWN_STOP_EXISTING_NOTE_REVIEW")
    last = read_item(client, base, card)
    require(fingerprint(last) == fingerprint(before),
            "OWN_STOP_CARD_CHANGED")
    if fence.reserve(card) != "RESERVED":
        return "OWN_STOP_ALREADY_RESERVED_NO_RETRY"
    fence.receipt = {"expected": {"comment_sha256": sha256(value.encode()).hexdigest()}}
    require(fence.finalize(card, "UNCERTAIN"),
            "OWN_STOP_PREWRITE_LEDGER_UNVERIFIED")
    try:
        ident = add_comment(client, base, card, value)
        after = read_comments(client, base, card)
        require(len([row for row in after if row["COMMENT"] == value
                     and int(row["ID"]) == ident]) == 1
                and len([row for row in after
                         if row["COMMENT"].startswith(MARK)]) == 1,
                "OWN_STOP_EXACT_READBACK_FAILED")
    except Exception:
        return "OWN_STOP_NOTE_UNCERTAIN_NO_RETRY"
    return ("OWN_STOP_NOTE_READBACK_VERIFIED"
            if fence.finalize(card, "VERIFIED")
            else "OWN_STOP_NOTE_VERIFIED_LEDGER_UNCERTAIN")


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("SERVICEIT_OWN_SIGNAL_STOP_ENABLED") == FLAG
            and env.get("GITHUB_EVENT_NAME")
                in ("schedule", "repository_dispatch", "issue_comment"),
            "OWN_STOP_SCOPE_OR_OWNER_GATE_INVALID")
    if env["GITHUB_EVENT_NAME"] == "issue_comment":
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        require(path.is_file() and 0 < path.stat().st_size <= 256000,
                "OWN_STOP_EVENT_MISSING")
        event = json.loads(path.read_text("utf8"))
        require(event.get("action") == "created"
                and (event.get("repository") or {}).get("full_name") == REPOSITORY
                and (event.get("issue") or {}).get("number") == 200
                and (event.get("sender") or {}).get("login") == "Ramzes2023"
                and (event.get("comment") or {}).get("body")
                    == "SERVICEIT_OWN_SIGNAL_STOP_LIVE_ONCE_V1"
                and not (event.get("issue") or {}).get("pull_request"),
                "OWN_STOP_OWNER_EVENT_INVALID")
    rules = read_rules()
    if not rules:
        return {"status": "OWN_SIGNAL_STOP_ACTIVE_EMPTY",
                "configured_rules": 0, "eligible_new_cards": 0,
                "checked": 0, "notes_written_verified": 0,
                "crm_global_stop_clear": False, "customer_contacted": False}
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "OWN_STOP_PORTAL_INVALID")
    reader = lambda method, params: list_only(client, base, method, params)
    calibrate_known_card(reader)
    cards = discover(reader, after_activation(env, now), now)
    require(len(cards) <= 200, "OWN_STOP_DISCOVERY_UNVERIFIED")
    if env["GITHUB_EVENT_NAME"] == "repository_dispatch":
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        require(path.is_file() and 0 < path.stat().st_size <= 256000,
                "OWN_STOP_SIGNED_EVENT_MISSING")
        event = json.loads(path.read_text("utf8"))
        card = verify_event(event, env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", ""))
        cards = [c for c in cards if c == card]
    result = {"status": "OWN_SIGNAL_STOP_AUTOMATIC",
              "configured_rules": len(rules), "eligible_new_cards": len(cards),
              "checked": 0, "notes_written_verified": 0,
              "held": 0, "unknown": 0, "crm_global_stop_clear": False,
              "customer_contacted": False}
    if not cards:
        return result
    fence = OwnSignalFence(client, env.get("GITHUB_TOKEN", ""),
                           env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    # Rotate batches to avoid starving newer cards when the current window
    # holds more than one batch; no implicit processing of historic cards.
    windows = max(1, (len(cards) + MAX_CARDS - 1) // MAX_CARDS)
    slot = (int(now.timestamp()) // 1200) % windows
    selected = cards[slot * MAX_CARDS:(slot + 1) * MAX_CARDS]
    for card in selected:
        try:
            status = process_one(client, env, base, card, fence, rules, now=now)
        except Exception:
            status = "OWN_STOP_ACCESS_OR_EVIDENCE_UNKNOWN"
        result["checked"] += 1
        result["held"] += status in ("OWN_STOP_NOTE_READBACK_VERIFIED",
                                     "ALREADY_EXACT_OWN_STOP_NOTE")
        result["notes_written_verified"] += status == "OWN_STOP_NOTE_READBACK_VERIFIED"
        result["unknown"] += status in ("OWN_STOP_ACCESS_OR_EVIDENCE_UNKNOWN",
                                         "OWN_STOP_NOTE_UNCERTAIN_NO_RETRY",
                                         "OWN_STOP_NOTE_VERIFIED_LEDGER_UNCERTAIN")
        if status in ("OWN_STOP_NOTE_UNCERTAIN_NO_RETRY",
                      "OWN_STOP_NOTE_VERIFIED_LEDGER_UNCERTAIN"):
            break
    return result


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            output = run(client, dict(os.environ))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "OWN_SIGNAL_STOP_CLOUD_HOLD",
                          "crm_global_stop_clear": False,
                          "notes_written_verified": 0,
                          "customer_contacted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
