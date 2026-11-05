"""Auto-write an EXPLICITLY PROVISIONAL 0-100 signal score in new Bitrix cards.

Owner approved that unavailable FNS data does not block a *preliminary*
manager-side score. The score is a deterministic signal triage/routing rank,
NOT proof of a real buyer, company registration, full CRM dedupe, or permission
to contact. This job ONLY writes the three existing Score fields on fresh
1062/21 cards AFTER all nine AI manager fields exist. All three are tagged
as provisional; full 9-proof Stage4 formal score remains separately gated.

No edits to old cards 3/7, title, stage, task, contact, other pipelines,
source adapters or nine AI fields. A distinct encrypted one-use GitHub fence
prevents unsafe replay after an ambiguous REST response.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_mapping import _is_valid_inn
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_dispatch_worker import (
    EncryptedEventFence, REPOSITORY, PORTAL,
)
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, discover,
)
from .serviceit_enrichment_worker import (
    PREFIX, fingerprint, normalized, validate_identity, AI_FIELDS,
)
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD
from .serviceit_stage3_all_new_task_automation import CUTOVER, card_read
from .serviceit_stage4_all_new_signed_score_writer import (
    score_rest, score_schema_ok,
)
from .serviceit_stage4_fns_dedupe_watch import same_process_visible_duplicates
from .serviceit_qualification_proof_v2 import crm_inn_visibility_readonly
from .serviceit_corporate_stop_inn import precheck as stop_precheck, CorporateStopBlocked
from .serviceit_three_signal_triage import classify_signal, REVIEW
from .serviceit_preliminary_score_markers import VERSION, provisional_payload, is_exact_own_provisional

GATE = "YES_1062_21_PROVISIONAL_ONLY"
FOLDER = ".serviceit/provisional-score-once"
SCORE_KEYS = frozenset((SCORE_FIELD, VERSION_FIELD, BADGE_FIELD))
MAX_WRITES = 6

class ProvisionalBlocked(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise ProvisionalBlocked(code)


class PreliminaryFence(EncryptedEventFence):
    """Distinct from formal Score and nine-AI-field ledgers."""

    def url(self, card):
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "PROVISIONAL_CARD_SCOPE")
        digest = hmac.new(
            self.path_key, f"stage4-provisional-v1:1062:21:{card}".encode(),
            sha256,
        ).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f"{FOLDER}/{digest}.enc")


def existing_score_state(item):
    """Do not overwrite existing score or use a missing field as an empty one."""
    require(all(k in item for k in SCORE_KEYS), "SCORE_FIELDS_UNREADABLE")
    current = [item[k] for k in SCORE_KEYS]
    if all(x in (None, "") for x in current):
        return "EMPTY"
    if is_exact_own_provisional(item):
        return "ALREADY_PROVISIONAL"
    return "OTHER_SCORE_PRESENT"


def classify_preliminary(item, now):
    """CRM title/kind/source are UNVERIFIED, no AI confidence or false source."""
    values = {
        "title": item.get("title") or "",
        "signal_kind": item.get(PREFIX + "SIGNAL_KIND") or "",
        "source": item.get(PREFIX + "SIGNAL_SOURCE") or "",
        "region": item.get(PREFIX + "REGION") or "",
    }
    require(all(type(x) is str for x in values.values()),
            "PRELIMINARY_INPUT_TYPE_INVALID")
    triage = classify_signal({
        **values, "description": "", "company": "",
    }, now=now)
    number = triage.get("indicative_priority")
    require(type(number) is int and 0 <= number <= 100
            and triage.get("formal_lead_score") is None,
            "PRELIMINARY_SCORE_INVALID")
    return triage


def visible_duplicate_status(client, webhook, base, item, card):
    """Never treat absence in visible objects as global dedup clearance."""
    inn = item.get(PREFIX + "COMPANY_INN")
    if type(inn) is not str or not _is_valid_inn(inn):
        return "ИНН НЕТ"
    try:
        process = same_process_visible_duplicates(client, base, inn, card)
        requisites = crm_inn_visibility_readonly(client, webhook, inn)
        require(process.get("global_crm_clear") is False
                and requisites.get("global_dedup_verified") is False
                and requisites.get("global_crm_customer_clear") is False
                and process.get("visible_pages_complete") is True
                and requisites.get("visible_pages_complete") is True,
                "UNSUPPORTED_GLOBAL_DEDUP_ASSERTION")
        if (process.get("process_visible_duplicate") is True
                or requisites.get("visible_matches", 0) > 0):
            return "ДУБЛЬ НАЙДЕН"
        return "ТОЛЬКО ВИДИМЫЕ"
    except Exception:
        return "НЕ ПРОВЕРЕНЫ"


def evaluate_one(client, env, card, *, now=None):
    now = now or datetime.now(timezone.utc)
    require(type(now) is datetime and now.tzinfo is not None,
            "PROVISIONAL_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    require(type(card) is int and 8 <= card <= 2_000_000_000
            and card not in (3, 7), "PROVISIONAL_CARD_OUT_OF_SCOPE")
    require(env.get("SERVICEIT_STAGE4_PRELIMINARY_SCORE_ENABLED") == GATE
            and env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main",
            "PROVISIONAL_OWNER_SCOPE_INVALID")
    webhook = env.get("SERVICEIT_BITRIX_WEBHOOK", "")
    base = validate_webhook(webhook)
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "PROVISIONAL_PORTAL_INVALID")
    item = card_read(client, base, card, now=now)
    require(item.get("createdTime") not in (None, ""),
            "PROVISIONAL_CREATED_TIME_MISSING")
    from .serviceit_bitrix_poll_fallback import date_value
    require(date_value(item["createdTime"]) >= after_activation(env, now),
            "PROVISIONAL_CARD_BEFORE_ACTIVATION")
    state = existing_score_state(item)
    if state != "EMPTY":
        return ("ALREADY_PROVISIONAL_NO_REWRITE" if state == "ALREADY_PROVISIONAL"
                else "OTHER_SCORE_PRESENT_NO_REWRITE")
    # The original nine-field worker must complete first: scoring an empty
    # card earlier would violate its no-existing-qualification contract.
    # The prior check only required three populated fields; a partially
    # written nine-field report could still have received a provisional Score.
    # Distinguish a complete honest HOLD report from a half-written card.
    require(all(k in item for k in AI_FIELDS), "AI_FIELDS_NOT_READABLE")
    if (item.get(PREFIX + "AI_STATUS") not in (
            "NOT_RUN_SOURCE_UNVERIFIED", "HOLD", "NOT_RUN",
            "HYPOTHESIS_UNVERIFIED",
            "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED")
            or any(type(item[k]) is not str or not item[k].strip()
                   for k in AI_FIELDS)):
        return "WAIT_FOR_AI_MANAGER_FIELDS"
    # Do not create a marketing score on a synthetic fixture or explicit STOP.
    signal_id = item.get(PREFIX + "SIGNAL_ID") or ""
    kind = item.get(PREFIX + "SIGNAL_KIND") or ""
    source = item.get(PREFIX + "SIGNAL_SOURCE") or ""
    require(all(type(x) is str for x in (signal_id, kind, source)),
            "PROVISIONAL_SOURCE_VALUES_INVALID")
    if (signal_id == "DEMO-ITREQ-001"
            or kind == "demo-synthetic" or source == "synthetic-fixture"):
        return "SYNTHETIC_EXCLUDED"
    # A known corporate exclusion must block even a provisional numeric Score.
    # When no approved STOP list is connected, provisional scoring remains
    # possible, but it is explicitly NOT a contact or formal verification.
    inn = item.get(PREFIX + "COMPANY_INN")
    if type(inn) is str and _is_valid_inn(inn):
        try:
            stop_result = stop_precheck(inn, env)
        except CorporateStopBlocked:
            return "CORPORATE_STOP_CONFIG_HOLD_NO_SCORE"
        if stop_result == "HIT":
            return "CORPORATE_STOP_MATCH_HOLD_NO_SCORE"
    # STOP_REASON is a source/HOLD marker in current B2B cards; it is never
    # interpreted as a cleared corporate STOP list. All scores stay no-contact.
    triage = classify_preliminary(item, now)
    if triage.get("type") == REVIEW:
        return "UNCLASSIFIED_SIGNAL_NO_SCORE"
    duplicate = visible_duplicate_status(client, webhook, base, item, card)
    if duplicate == "ДУБЛЬ НАЙДЕН":
        return "VISIBLE_DUPLICATE_HOLD_NO_SCORE"
    fields = provisional_payload(triage["indicative_priority"], duplicate)
    score_schema_ok(client, base)
    latest = card_read(client, base, card, now=now)
    require(fingerprint(latest) == fingerprint(item),
            "PROVISIONAL_MANAGER_CHANGED_CARD")
    # Never compete with formal score or GigaChat's existing score.
    require(existing_score_state(latest) == "EMPTY",
            "PROVISIONAL_SCORE_CHANGED_DURING_CHECK")
    fence = PreliminaryFence(
        client, env.get("GITHUB_TOKEN", ""),
        env.get("SERVICEIT_GH_LEDGER_KEY", ""),
    )
    reserved = fence.reserve(card)
    if reserved != "RESERVED":
        return "ALREADY_RESERVED_NO_REPLAY"
    # Preserve exact readback target BEFORE sending a potentially ambiguous
    # CRM update. A failure cannot be retried automatically.
    fence.receipt = {"expected": fields}
    require(fence.finalize(card, "UNCERTAIN"),
            "PROVISIONAL_CHECKPOINT_UNVERIFIED")
    try:
        score_rest(client, base, "crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": fields,
        })
        result = score_rest(client, base, "crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
        })
        require(type(result.get("item")) is dict,
                "PROVISIONAL_READBACK_MISSING")
        after = normalized(result["item"])
        validate_identity(after, card)
        require(all(after.get(k) == v for k, v in fields.items()),
                "PROVISIONAL_FIELD_READBACK_MISMATCH")
        ignored = SCORE_KEYS | {
            "updatedTime", "updatedBy", "lastActivityTime", "lastActivityBy",
        }
        require(all(after.get(k) == v for k, v in latest.items()
                    if k not in ignored), "PROVISIONAL_OTHER_FIELD_CHANGED")
    except Exception:
        fence.finalize(card, "UNCERTAIN")
        return "PROVISIONAL_WRITE_UNCERTAIN_NO_RETRY"
    fence.finalize(card, "HOLD")
    return "PROVISIONAL_SCORE_EXACT_READBACK"


def scan(client, env, *, now=None):
    now = now or datetime.now(timezone.utc)
    require(env.get("SERVICEIT_STAGE4_PRELIMINARY_SCORE_ENABLED") == GATE,
            "PROVISIONAL_GATE_MISSING")
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "PROVISIONAL_PORTAL_INVALID")
    start = max(CUTOVER, after_activation(env, now))
    source = lambda method, args: list_only(client, base, method, args)
    calibrate_known_card(source)
    cards = discover(source, start, now)
    counters = Counter()
    # Skip existing-score cards without consuming the live write budget.
    attempted = 0
    for card in cards:
        if attempted >= MAX_WRITES:
            counters["PENDING_MORE"] += 1
            break
        try:
            status = evaluate_one(client, env, card, now=now)
        except Exception:
            status = "REVIEW_OR_WAIT_REQUIRED"
        counters[status] += 1
        if status in ("PROVISIONAL_SCORE_EXACT_READBACK",
                      "PROVISIONAL_WRITE_UNCERTAIN_NO_RETRY"):
            attempted += 1
        if status == "PROVISIONAL_WRITE_UNCERTAIN_NO_RETRY":
            # Stop on ambiguous mutation, do not mask potential side effects.
            break
    return {
        "status": "SERVICEIT_PROVISIONAL_SCORE_AUTOMATION",
        "new_cards_visible": len(cards),
        "score_written_and_verified": counters["PROVISIONAL_SCORE_EXACT_READBACK"],
        "already_scored": counters["ALREADY_PROVISIONAL_NO_REWRITE"]
                          + counters["OTHER_SCORE_PRESENT_NO_REWRITE"],
        "not_ready_or_held": sum(
            count for state, count in counters.items()
            if state not in ("PROVISIONAL_SCORE_EXACT_READBACK",
                             "ALREADY_PROVISIONAL_NO_REWRITE",
                             "OTHER_SCORE_PRESENT_NO_REWRITE")),
        "potentially_ambiguous": counters["PROVISIONAL_WRITE_UNCERTAIN_NO_RETRY"],
        "fns_verified": False,
        "global_crm_dedup_verified": False,
        "stop_list": "DEFERRED_NOT_CONFIGURED",
        "contact_authorized": False,
        "formal_lead_score_confirmed": False,
        "crm_writes_verified": counters["PROVISIONAL_SCORE_EXACT_READBACK"],
    }


def main():
    env = dict(os.environ)
    try:
        require(env.get("GITHUB_EVENT_NAME") in ("schedule", "issue_comment")
                and env.get("GITHUB_REPOSITORY") == REPOSITORY
                and env.get("GITHUB_REF") == "refs/heads/main",
                "PROVISIONAL_RUNNER_SCOPE_INVALID")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            report = scan(client, env)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if not report["potentially_ambiguous"] else 2
    except Exception:
        print(json.dumps({
            "status": "PROVISIONAL_SCORE_BLOCKED_OR_NEEDS_REVIEW",
            "crm_writes_verified": 0,
            "formal_lead_score_confirmed": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
