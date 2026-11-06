"""Free official MSP based INTERNAL score for new LeadGen cards ONLY.

Does not certify company activity, company-wide CRM dedup, or contact rights.
Never overwrites third-party/manual scores. Exact own STOP and in-funnel
duplicates are mandatory; outside CRM requisites can only veto matches.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import hmac
import json
import os
import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, PORTAL, REPOSITORY, verify_event
from .serviceit_bitrix_poll_fallback import after_activation, calibrate_known_card, date_value, discover
from .serviceit_enrichment_worker import AI_FIELDS, PREFIX, fingerprint, normalized, validate_identity
from .serviceit_fns_msp_official_live import OfficialMspFence
from .serviceit_fns_msp_crm_result_writer import verified_official_workflow_run
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, score_label
from .serviceit_own_signal_stop import read_rules, check as own_stop_check
from .serviceit_preliminary_score_markers import is_score_empty_or_ours
from .serviceit_qualification_proof_v2 import crm_inn_visibility_readonly
from .serviceit_stage3_all_new_task_automation import CUTOVER, card_read
from .serviceit_stage4_all_new_signed_score_writer import score_rest, score_schema_ok
from .serviceit_stage4_fns_dedupe_watch import same_process_visible_duplicates
from .serviceit_stage4_provisional_score_live import classify_preliminary
from .serviceit_three_signal_triage import REVIEW

VERSION = "svc-leadgen-free-msp-v1"
GATE = "YES_1062_21_FREE_MSP_INTERNAL"
KEYS = frozenset((SCORE_FIELD, VERSION_FIELD, BADGE_FIELD))
BONUS = {"MICRO": 0, "SMALL": 4, "MEDIUM": 8, None: 0}
SIZES = {"MICRO": "микро", "SMALL": "малое",
         "MEDIUM": "среднее", None: "размер неизвестен"}
MAX_WRITES = 12
OWNER_MARKER = "SERVICEIT_LEADGEN_FREE_MSP_SCORE_LIVE_ONCE_V1"


class InternalScoreBlocked(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise InternalScoreBlocked(reason)


class InternalScoreFence(EncryptedEventFence):
    def url(self, card):
        need(type(card) is int and 8 <= card <= 2_000_000_000
             and card not in (3, 7), "INTERNAL_SCORE_CARD_INVALID")
        digest = hmac.new(
            self.path_key, f"leadgen-free-msp-v1:1062:21:{card}".encode(),
            sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/leadgen-free-msp-once/{digest}.enc")


def recent_receipt(client, env, card, inn, *, today):
    need(type(today) is date and _is_valid_inn(inn), "MSP_INN_INVALID")
    first = today.replace(day=1)
    prior = (first - timedelta(days=1)).replace(day=10)
    for publication in (first.replace(day=10), prior):
        if not timedelta(0) <= today-publication <= timedelta(days=45):
            continue
        result = OfficialMspFence(
            client, env.get("GITHUB_TOKEN", ""),
            env.get("SERVICEIT_GH_LEDGER_KEY", ""),
            publication).read_checked_receipt(card, inn)
        if result is None:
            continue
        need(result.get("published") == publication.isoformat()
             and result.get("msp_category") in BONUS
             and result.get("fns_egrul_verified") is False
             and result.get("formal_score_authorized") is False,
             "MSP_RECEIPT_CANNOT_CONFIRM_REGISTRATION")
        return result
    return None


def internal_payload(score, receipt):
    need(type(score) is int and 0 <= score <= 100,
         "INTERNAL_SCORE_INVALID")
    need(type(receipt) is dict
         and receipt.get("msp_category") in BONUS
         and receipt.get("fns_egrul_verified") is False
         and receipt.get("formal_score_authorized") is False,
         "MSP_EVIDENCE_INVALID")
    published = receipt.get("published")
    need(type(published) is str and len(published) == 10
         and date.fromisoformat(published).isoformat() == published,
         "MSP_DATE_INVALID")
    cat = receipt["msp_category"]
    final = min(100, score + BONUS[cat])
    badge = (score_label(final) + f" · МСП: {SIZES[cat]}"
             " · Дубли: наша воронка · STOP: свой"
             " · ВНУТРЕННИЙ ИТОГ · Контакт: нет")
    need(len(badge) <= 200, "INTERNAL_BADGE_TOO_LONG")
    return {SCORE_FIELD: final, VERSION_FIELD: VERSION, BADGE_FIELD: badge}


def source_metadata_present(row, now):
    url = row.get(PREFIX + "SIGNAL_URL")
    stamp = row.get(PREFIX + "SIGNAL_DATE")
    need(type(url) is str and 12 <= len(url) <= 1024, "SOURCE_URL_MISSING")
    parsed = urlsplit(url)
    need(parsed.scheme == "https" and parsed.hostname not in (None, "example.invalid")
         and parsed.username is None and parsed.password is None
         and parsed.port in (None, 443), "SOURCE_URL_INVALID")
    need(type(stamp) is str and len(stamp) >= 10, "SOURCE_DATE_MISSING")
    try:
        day = date.fromisoformat(stamp[:10])
    except ValueError:
        raise InternalScoreBlocked("SOURCE_DATE_INVALID") from None
    need(timedelta(0) <= now.date() - day <= timedelta(days=30),
         "SOURCE_DATE_NOT_RECENT")
    return True


def local_duplicate_status(client, base, inn, card):
    result = same_process_visible_duplicates(client, base, inn, card)
    need(type(result) is dict
         and result.get("visible_pages_complete") is True
         and result.get("global_crm_clear") is False
         and type(result.get("process_visible_matches")) is int
         and result["process_visible_matches"] >= 1,
         "LEADGEN_DUPLICATE_PAGINATION_NOT_VERIFIED")
    if result.get("process_visible_duplicate") is True:
        return "HOLD_OWN_FUNNEL_DUPLICATE"
    need(result.get("process_visible_duplicate") is False,
         "LEADGEN_DUPLICATE_UNKNOWN")
    return "NO_MATCH_IN_OWN_VISIBLE_FUNNEL"


def visible_corporate_duplicate_veto(client, webhook, inn):
    try:
        result = crm_inn_visibility_readonly(client, webhook, inn)
        if (result.get("visible_pages_complete") is True
                and type(result.get("visible_matches")) is int
                and result["visible_matches"] > 0):
            return "HOLD_VISIBLE_CORPORATE_MATCH"
    except Exception:
        pass
    return "GLOBAL_CRM_UNVERIFIED"


def evaluate_one(client, env, card, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(type(card) is int and 8 <= card <= 2_000_000_000 and card not in (3, 7)
         and env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("SERVICEIT_LEADGEN_FREE_SCORE_ENABLED") == GATE,
         "INTERNAL_SCORE_SCOPE_OR_OWNER_INVALID")
    webhook = env.get("SERVICEIT_BITRIX_WEBHOOK", "")
    base = validate_webhook(webhook)
    need(base.startswith(f"https://{PORTAL}/rest/"), "INTERNAL_SCORE_PORTAL_INVALID")
    before = card_read(client, base, card, now=now)
    need(date_value(before.get("createdTime")) >= after_activation(env, now),
         "HISTORICAL_CARD_FORBIDDEN")
    need(all(k in before for k in KEYS), "SCORE_FIELDS_UNREADABLE")
    inn = before.get(PREFIX + "COMPANY_INN")
    stop, _ = own_stop_check(inn, read_rules(), card_id=card)
    if stop == "HOLD_OWN_SIGNAL_STOP":
        return "HOLD_OWN_STOP"
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_COMPANY_INN_INVALID"
    need(stop in ("OWN_SIGNAL_STOP_EMPTY", "NOT_IN_OWN_SIGNAL_STOP"),
         "OWN_STOP_UNVERIFIED")
    if before.get(VERSION_FIELD) == VERSION:
        return "ALREADY_INTERNAL_SCORE_NO_REWRITE"
    if not is_score_empty_or_ours(before):
        return "MANUAL_OR_OTHER_SCORE_PROTECTED"
    if (before.get(PREFIX + "SIGNAL_ID") == "DEMO-ITREQ-001"
            or before.get(PREFIX + "SIGNAL_KIND") == "demo-synthetic"
            or before.get(PREFIX + "SIGNAL_SOURCE") == "synthetic-fixture"):
        return "SYNTHETIC_EXCLUDED"
    need(all(k in before and type(before[k]) is str and before[k].strip()
             for k in AI_FIELDS), "AI_FIELDS_NOT_READY")
    need(before.get(PREFIX + "AI_STATUS") in (
         "NOT_RUN_SOURCE_UNVERIFIED", "HOLD", "NOT_RUN",
         "HYPOTHESIS_UNVERIFIED", "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"),
         "AI_STATUS_UNKNOWN")
    source_metadata_present(before, now)
    triage = classify_preliminary(before, now)
    if triage.get("type") == REVIEW:
        return "HOLD_UNCLASSIFIED"
    if local_duplicate_status(client, base, inn, card) != "NO_MATCH_IN_OWN_VISIBLE_FUNNEL":
        return "HOLD_OWN_FUNNEL_DUPLICATE"
    if visible_corporate_duplicate_veto(client, webhook, inn) == "HOLD_VISIBLE_CORPORATE_MATCH":
        return "HOLD_VISIBLE_CORPORATE_MATCH"
    receipt = recent_receipt(client, env, card, inn, today=now.date())
    if receipt is None:
        return "WAIT_FOR_OFFICIAL_MSP_RECEIPT"
    output = internal_payload(triage["indicative_priority"], receipt)
    score_schema_ok(client, base)
    latest = card_read(client, base, card, now=now)
    need(fingerprint(latest) == fingerprint(before)
         and is_score_empty_or_ours(latest),
         "CARD_CHANGED_BEFORE_SCORE")
    fence = InternalScoreFence(
        client, env.get("GITHUB_TOKEN", ""),
        env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    if fence.reserve(card) != "RESERVED":
        return "ALREADY_RESERVED_NO_RETRY"
    fence.receipt = {"expected": output}
    need(fence.finalize(card, "UNCERTAIN"), "PREWRITE_CHECKPOINT_FAILED")
    try:
        score_rest(client, base, "crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": output})
        result = score_rest(client, base, "crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})
        need(type(result.get("item")) is dict, "SCORE_READBACK_MISSING")
        after = normalized(result["item"])
        validate_identity(after, card)
        need(all(after.get(k) == v for k, v in output.items()),
             "SCORE_READBACK_MISMATCH")
        ignore = KEYS | {"updatedTime", "updatedBy", "lastActivityTime", "lastActivityBy"}
        need(all(after.get(k) == v for k, v in latest.items() if k not in ignore),
             "SCORE_OTHER_FIELD_CHANGED")
    except Exception:
        return "SCORE_WRITE_UNCERTAIN_NO_REPLAY"
    return ("INTERNAL_SCORE_EXACT_READBACK" if fence.finalize(card, "VERIFIED")
            else "SCORE_READBACK_OK_LEDGER_UNCERTAIN")


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("SERVICEIT_LEADGEN_FREE_SCORE_ENABLED") == GATE
         and env.get("GITHUB_EVENT_NAME") in ("schedule", "workflow_run", "repository_dispatch", "issue_comment"),
         "SCORE_EVENT_SCOPE_INVALID")
    event_card = None
    if env["GITHUB_EVENT_NAME"] in ("workflow_run", "repository_dispatch", "issue_comment"):
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        need(path.is_file() and 0 < path.stat().st_size <= 256_000,
             "SCORE_EVENT_FILE_INVALID")
        event = json.loads(path.read_text("utf-8"))
        if env["GITHUB_EVENT_NAME"] == "workflow_run":
            verified_official_workflow_run(event)
        elif env["GITHUB_EVENT_NAME"] == "issue_comment":
            need(event.get("action") == "created"
                 and (event.get("repository") or {}).get("full_name") == REPOSITORY
                 and (event.get("issue") or {}).get("number") == 200
                 and not (event.get("issue") or {}).get("pull_request")
                 and (event.get("sender") or {}).get("login") == "Ramzes2023"
                 and (event.get("comment") or {}).get("body") == OWNER_MARKER,
                 "UNTRUSTED_OWNER_MANUAL_EVENT")
        else:
            event_card = verify_event(event, env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", ""))
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(f"https://{PORTAL}/rest/"), "SCORE_PORTAL_INVALID")
    list_rest = lambda method, args: list_only(client, base, method, args)
    calibrate_known_card(list_rest)
    cards = discover(list_rest, max(CUTOVER, after_activation(env, now)), now)
    if event_card is not None:
        cards = [card for card in cards if card == event_card]
    report = {"status": "FREE_MSP_INTERNAL_LEADGEN_SCORE",
              "new_cards_visible": len(cards), "readbacks_verified": 0,
              "waiting_or_hold": 0, "uncertain": 0,
              "paid_egrul_required": False, "global_crm_clear": False,
              "outreach_authorized": False}
    for card in cards[:MAX_WRITES]:
        try:
            state = evaluate_one(client, env, card, now=now)
        except Exception:
            state = "HOLD_EVIDENCE_OR_ACCESS_UNVERIFIED"
        if state == "INTERNAL_SCORE_EXACT_READBACK":
            report["readbacks_verified"] += 1
        elif state in ("SCORE_WRITE_UNCERTAIN_NO_REPLAY", "SCORE_READBACK_OK_LEDGER_UNCERTAIN"):
            report["uncertain"] += 1
            break
        else:
            report["waiting_or_hold"] += 1
    report["pending_next_batch"] = max(0, len(cards) - MAX_WRITES)
    return report


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            report = run(client, dict(os.environ))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2 if report["uncertain"] else 0
    except Exception:
        print(json.dumps({"status": "FREE_MSP_INTERNAL_SCORE_HOLD",
                          "readbacks_verified": 0, "outreach_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
