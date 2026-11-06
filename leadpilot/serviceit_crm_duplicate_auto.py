"""Automatic exact-INN duplicate review for real NEW ServiceIT 1062/21 cards.

Checks the scoped process AND corporate company/contact requisites.  A
non-match can only mean NOT FOUND IN VISIBLE SCOPE; it never authorizes
contact, a formal Score, or a company-wide CRM clearance.

Uses an idempotent ledger and adds one internal review comment only; no
contact, company, stage, task, 9 AI field, or Score mutations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_dispatch_worker import (
    EncryptedEventFence, PORTAL, REPOSITORY, verify_event,
)
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, date_value, discover,
)
from .serviceit_enrichment_worker import PREFIX, fingerprint
from .serviceit_stage4_fns_dedupe_watch import (
    read_item, same_process_visible_duplicates,
)
from .serviceit_qualification_proof_v2 import crm_inn_visibility_readonly
from .serviceit_fns_msp_timeline import read_comments, ENTITY_TYPE
from .serviceit_own_signal_stop import read_rules as own_stop_rules, check as own_stop_check, SignalStopBlocked

MARK = "[SERVICEIT_CRM_DUPLICATE_SCOPE_V1]"
FLAG = "YES_1062_21_VISIBLE_DUPLICATE_REVIEW"
MAX_CARDS = 20


class DuplicateBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise DuplicateBlocked(code)


class DuplicateFence(EncryptedEventFence):
    def url(self, card):
        need(type(card) is int and 8 <= card <= 2_000_000_000
             and card not in (3, 7), "DUPLICATE_ID_OUTSIDE_SCOPE")
        digest = hmac.new(
            self.path_key, f"serviceit-crm-visible-dedup-v1:1062:21:{card}".encode(),
            sha256,
        ).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/crm-visible-dedup-once/{digest}.enc")


def review_message(has_duplicate):
    need(type(has_duplicate) is bool, "DUPLICATE_RESULT_NOT_BOOLEAN")
    if has_duplicate:
        return (MARK + " Обнаружено совпадение ИНН среди доступных записей CRM "
                "(компании/контакты/реквизиты или AI-сигналы). "
                "HOLD: менеджеру проверить существующего клиента и связанные сделки; "
                "не создавать дубликат и не связываться с компанией автоматически. "
                "Полный корпоративный охват не подтвержден.")
    return (MARK + " По ИНН совпадений среди доступных реквизитов "
            "компаний/контактов и AI-сигналов не найдено. "
            "Это НЕ полная проверка CRM: записи других сотрудников, "
            "объекты без ИНН и STOP-лист могут быть недоступны. "
            "Статус HOLD; требуется решение менеджера перед контактом.")


def verify_visible_scope(client, base, webhook, card, inn):
    need(type(inn) is str and _is_valid_inn(inn),
         "DUPLICATE_COMPANY_INN_MISSING_OR_INVALID")
    process = same_process_visible_duplicates(client, base, inn, card)
    requisites = crm_inn_visibility_readonly(client, webhook, inn)
    need(process.get("visible_pages_complete") is True
         and process.get("global_crm_clear") is False
         and requisites.get("visible_pages_complete") is True
         and requisites.get("global_dedup_verified") is False
         and requisites.get("global_crm_customer_clear") is False
         and type(requisites.get("visible_matches")) is int
         and requisites["visible_matches"] >= 0,
         "DUPLICATE_RESULT_OR_PAGINATION_UNVERIFIED")
    return bool(process["process_visible_duplicate"]
                or requisites["visible_matches"] > 0)


def add_timeline_note(client, base, card, comment):
    need(type(card) is int and 8 <= card <= 2_000_000_000
         and card not in (3, 7)
         and type(comment) is str
         and comment in (review_message(True), review_message(False)),
         "DUPLICATE_TIMELINE_ALLOWLIST")
    try:
        res = client.post(
            base + "/crm.timeline.comment.add.json",
            json={"fields": {"ENTITY_TYPE": ENTITY_TYPE,
                             "ENTITY_ID": card, "COMMENT": comment}},
            timeout=25, follow_redirects=False,
        )
        need(res.status_code == 200 and 0 < len(res.content) <= 256000,
             "DUPLICATE_COMMENT_HTTP_UNVERIFIED")
        data = res.json()
        need(type(data) is dict and "error" not in data
             and str(data.get("result", "")).isascii()
             and str(data.get("result", "")).isdigit()
             and int(data["result"]) > 0,
             "DUPLICATE_COMMENT_ACK_UNVERIFIED")
        return int(data["result"])
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise DuplicateBlocked("DUPLICATE_COMMENT_NETWORK_UNCERTAIN") from None


def inspect_one(client, env, base, card, fence, *, now):
    before = read_item(client, base, card)
    need(date_value(before.get("createdTime")) >= after_activation(env, now),
         "DUPLICATE_HISTORICAL_CARD_FORBIDDEN")
    inn = before.get(PREFIX + "COMPANY_INN")
    # A ServiceIT-only STOP overrides duplicate review and suppresses an
    # additional manager comment. Exact card_id rules work without an INN.
    try:
        own_status, _ = own_stop_check(inn, own_stop_rules(), card_id=card)
    except SignalStopBlocked:
        return "HOLD_OWN_STOP_CONFIG_INVALID"
    if own_status == "HOLD_OWN_SIGNAL_STOP":
        return "HOLD_OWN_STOP_NO_DUPLICATE_NOTE"
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_NO_VALID_INN"
    need(own_status in ("OWN_SIGNAL_STOP_EMPTY", "NOT_IN_OWN_SIGNAL_STOP"),
         "OWN_STOP_STATUS_UNVERIFIED")
    found = verify_visible_scope(
        client, base, env["SERVICEIT_BITRIX_WEBHOOK"], card, inn)
    comment = review_message(found)
    previous = read_comments(client, base, card)
    matches = [row for row in previous
               if row["COMMENT"].startswith(MARK)]
    if matches:
        return ("EXISTING_MATCHING_NOTE" if len(matches) == 1
                and matches[0]["COMMENT"] == comment
                else "EXISTING_DIFFERENT_NOTE_REVIEW")
    after_checks = read_item(client, base, card)
    need(fingerprint(after_checks) == fingerprint(before),
         "DUPLICATE_CARD_CHANGED_DURING_CHECK")
    if fence.reserve(card) != "RESERVED":
        return "ALREADY_RESERVED_NO_RETRY"
    fence.receipt = {"expected": {"comment_sha256":
                     sha256(comment.encode("utf-8")).hexdigest()}}
    need(fence.finalize(card, "UNCERTAIN"),
         "DUPLICATE_PREWRITE_DURABLE_CHECKPOINT_MISSING")
    try:
        created_id = add_timeline_note(client, base, card, comment)
        readback = read_comments(client, base, card)
        matched = [row for row in readback
                   if row["COMMENT"] == comment
                   and int(row["ID"]) == created_id]
        need(len(matched) == 1
             and len([row for row in readback
                      if row["COMMENT"].startswith(MARK)]) == 1,
             "DUPLICATE_COMMENT_EXACT_READBACK_FAILED")
    except Exception:
        return "DUPLICATE_NOTE_UNCERTAIN_NO_RETRY"
    return ("EXACT_DUPLICATE_NOTE_READBACK" if fence.finalize(card, "VERIFIED")
            else "DUPLICATE_READBACK_OK_LEDGER_UNCERTAIN")


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") in
             ("schedule", "repository_dispatch", "issue_comment")
         and env.get("SERVICEIT_CRM_DUPLICATE_AUTO_ENABLED") == FLAG,
         "DUPLICATE_EVENT_GATE_INVALID")
    if env["GITHUB_EVENT_NAME"] == "issue_comment":
        event_file = Path(env.get("GITHUB_EVENT_PATH", ""))
        need(event_file.is_file() and 0 < event_file.stat().st_size <= 256000,
             "DUPLICATE_OWNER_EVENT_MISSING")
        event = json.loads(event_file.read_text("utf-8"))
        need(event.get("action") == "created"
             and (event.get("repository") or {}).get("full_name") == REPOSITORY
             and (event.get("issue") or {}).get("number") == 200
             and not (event.get("issue") or {}).get("pull_request")
             and (event.get("sender") or {}).get("login") == "Ramzes2023"
             and (event.get("comment") or {}).get("body")
                 == "SERVICEIT_CRM_DEDUP_AUTO_LIVE_ONCE_V1",
             "DUPLICATE_OWNER_EVENT_INVALID")
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(f"https://{PORTAL}/rest/"),
         "DUPLICATE_PORTAL_INVALID")
    read_list = lambda m, p: list_only(client, base, m, p)
    calibrate_known_card(read_list)
    cards = discover(read_list, after_activation(env, now), now)
    need(len(cards) <= 200, "DUPLICATE_NEW_CARD_BUDGET")
    if env["GITHUB_EVENT_NAME"] == "repository_dispatch":
        event_file = Path(env.get("GITHUB_EVENT_PATH", ""))
        need(event_file.is_file() and 0 < event_file.stat().st_size <= 256000,
             "DUPLICATE_SIGNED_EVENT_MISSING")
        event = json.loads(event_file.read_text("utf-8"))
        card = verify_event(event, env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", ""))
        cards = [c for c in cards if c == card]
    report = {"status": "SERVICEIT_CRM_DUPLICATE_AUTO",
              "eligible_new_cards": len(cards), "checked": 0,
              "notes_verified": 0, "unknown_or_held": 0,
              "crm_global_scope_verified": False,
              "formal_score_authorized": False, "customer_contacted": False}
    if not cards:
        return report
    fence = DuplicateFence(client, env.get("GITHUB_TOKEN", ""),
                           env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    for card in cards[:MAX_CARDS]:
        try:
            outcome = inspect_one(client, env, base, card, fence, now=now)
        except Exception:
            outcome = "HOLD_ACCESS_OR_EVIDENCE_UNVERIFIED"
        report["checked"] += 1
        report["notes_verified"] += outcome == "EXACT_DUPLICATE_NOTE_READBACK"
        report["unknown_or_held"] += outcome != "EXACT_DUPLICATE_NOTE_READBACK"
        if outcome in ("DUPLICATE_NOTE_UNCERTAIN_NO_RETRY",
                       "DUPLICATE_READBACK_OK_LEDGER_UNCERTAIN"):
            break
    return report


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = run(client, dict(os.environ))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "SERVICEIT_CRM_DUPLICATE_AUTO_HOLD",
                          "crm_global_scope_verified": False,
                          "notes_verified": 0, "customer_contacted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
