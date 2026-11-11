"""VPS coordinator: PostgreSQL-scoped future-only AI cards, no GitHub API.

No new source ingestion, no CRM company/contact creation and no historical
backfill. Uses PostgreSQL one-use receipts ONLY after all cloud writers have
been stopped and a fresh cutover timestamp has been independently confirmed.
GitHub ledger remains untouched. Normal mode is strictly READONLY.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import time

import httpx

from .serviceit_vps_pg_queue import (
    VpsBlocked, db, migrate, insert_new, claim_one, finalize,
    metrics, reconcile_stale, secret_file,
)
from .serviceit_enrichment_worker import Rest
from .serviceit_bitrix_dispatch_worker import run_vps_native
from .serviceit_bitrix_poll_fallback import (
    after_activation, discover, calibrate_known_card,
)
from .serviceit_vps_native_fence import migrate_native
from .serviceit_vps_native_actions import migrate_actions
from .serviceit_vps_native_msp import migrate as migrate_msp
from .serviceit_vps_poll_checkpoint import migrate_poll, check_gap, mark_success
from .serviceit_vps_poll_readonly_probe import inspect_api
from .serviceit_bitrix_batch_worker import list_only
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS

MAX_ITEMS = 6
INTERVAL_SECONDS = 300


def safe_log(payload):
    # Fixed status dictionary only; never customer text, URLs, secret or IDs.
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def live_permitted(env: dict) -> bool:
    return (
        env.get("SERVICEIT_VPS_WRITER_MODE") == "LIVE"
        and env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED") == "YES"
        and env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") == "YES"
        # Incoming event intake is forbidden in this REST-poll deployment.
        and env.get("SERVICEIT_VPS_INTAKE_ENABLED", "NO") == "NO"
        and env.get("SERVICEIT_VPS_POLL_ENABLED") == "YES"
        and env.get("SERVICEIT_AI_PROVIDER") == "routerai"
        and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
        and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21"
    )


def exact_environment(env: dict) -> dict:
    """Server-only AI environment; never read legacy GitHub secrets."""
    if env.get("SERVICEIT_AI_PROVIDER", "routerai") != "routerai":
        raise VpsBlocked("ROUTERAI_ONLY_VPS_CONFIGURATION_REQUIRED")
    result = dict(env)
    result["SERVICEIT_BITRIX_WEBHOOK"] = secret_file(
        env, "SERVICEIT_BITRIX_WEBHOOK")
    result["SERVICEIT_BRAVE_SEARCH_API_KEY"] = secret_file(
        env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True)
    result["ROUTERAI_API_KEY"] = secret_file(
        env, "SERVICEIT_ROUTERAI_API_KEY")
    result["AI_PROVIDER"] = "routerai"
    result["GIGACHAT_CREDENTIALS"] = ""
    result["SERVICEIT_CORPORATE_APPROVALS_JSON"] = "{}"
    result["AI_ENABLE"] = "1"
    result["SERVICEIT_PUBLIC_RESEARCH_ENABLED"] = "YES"
    result["SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"] = "YES"
    result["SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED"] = "YES"
    result["SERVICEIT_B2B_V18_SEARCH_ENABLED"] = "YES"
    result["SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED"] = "YES"
    result["SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT"] = "NO"
    return result

def readonly_preflight(env: dict, *, client: httpx.Client):
    rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
    data = rest("crm.item.fields", {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
    })
    found = data.get("fields", {})
    if not isinstance(found, dict) or not AI_FIELDS.issubset(found):
        raise VpsBlocked("BITRIX_NINE_FIELDS_NOT_FOUND")
    return {
        "status": "READONLY_CRM_FIELD_METADATA_CONFIRMED",
        "crm_writes": 0, "ai_calls": 0, "sources_connected": 0,
    }


def poll_new(env: dict, *, client: httpx.Client, connection,
             preview_only=False) -> dict:
    """Strict existing new-only 1062/21 discovery; no historical import."""
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    base = validate_webhook(webhook)
    now = datetime.now(timezone.utc)
    native_start = env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
    if not native_start:
        raise VpsBlocked("NATIVE_ACTIVATION_UTC_REQUIRED")
    # Do not reuse the old cloud activation date: that could replay its cards.
    since = after_activation({**env, "SERVICEIT_BITRIX_POLL_START_UTC": native_start}, now)
    if not preview_only:
        check_gap(connection, native_start, now)
    def rest(method, args):
        return list_only(client, base, method, args)
    calibrate_known_card(rest)
    cards = discover(rest, since, now)
    added = 0
    if not preview_only:
        for card in cards:
            added += int(insert_new(connection, card))
        mark_success(connection, native_start, now)
    return {"status": "POLL_READONLY" if preview_only else "POLL_QUEUED",
            "candidates": len(cards), "queued": added, "crm_writes": 0}


def run_once(env: dict, *, client: httpx.Client) -> dict:
    with db(env) as connection:
        if not live_permitted(env):
            # A successful empty list alone is insufficient. Validate the
            # known Bitrix card #3 and the last 10 minutes on EVERY readonly
            # cycle, with zero queue inserts and zero CRM writes.
            result = readonly_preflight(env, client=client)
            probe = inspect_api(env, client=client)
            result["poll_positive_control"] = probe["positive_control_card3"]
            result["new_card_candidates_10m"] = probe["new_card_candidates"]
            result["queue_counts"] = metrics(connection)
            result["migration_cutover"] = "NOT_AUTHORIZED"
            return result
        exact_env = exact_environment(env)
        # The native worker verifies the independent cutover/privacy gates
        # again immediately before reserving its PostgreSQL one-use receipt.
        # No GitHub token/HMAC/Fernet key is needed on this VPS.
        reconcile_stale(connection)
        polled = {"status": "POLL_OFF", "candidates": 0, "queued": 0}
        if env.get("SERVICEIT_VPS_POLL_ENABLED") == "YES":
            polled = poll_new(env, client=client, connection=connection)
        handled = 0
        review = 0
        for _ in range(MAX_ITEMS):
            card = claim_one(connection)
            if card is None:
                break
            # There is deliberately NO automatic replay after any exception.
            # A claim that crashes stays RUNNING, then becomes UNCERTAIN.
            try:
                outcome = run_vps_native(card, exact_env, client=client,
                                         connection=connection)
                ok = outcome.get("status") in (
                    "SERVICEIT_NEW_CARD_EVENT_PROCESSED",
                    "EXACT_TARGET_READBACK",
                )
                result_status = "DONE" if ok else "REVIEW"
                safe_reason = "EXACT_WORKER_ACK" if ok else "EXACT_WORKER_REVIEW"
                finalize(connection, card, result_status, safe_reason)
                handled += 1
                review += int(not ok)
            except Exception:
                try:
                    finalize(connection, card, "UNCERTAIN", "EXTERNAL_OUTCOME_UNKNOWN")
                except Exception:
                    # Leave the reservation RUNNING: stale-state fence prevents
                    # blind re-invocation and requires manual recovery.
                    pass
                review += 1
                break
        research = {"status": "B2B_RESEARCH_DISABLED", "research": 0}
        if env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED", "NO") == "YES":
            # Separate versioned PG receipt upgrades verified B2B HOLD cards.
            # This is not a replay of an already completed AI_NINE_V1 write.
            # Public research failures must not stall future source intake.
            try:
                from .serviceit_vps_b2b_deep_research import run_once as deep_once
                research = deep_once(exact_env, client, connection)
            except Exception:
                research = {"status": "B2B_RESEARCH_CHECK_FAILED", "research": 0}
        public_inn = {"status": "B2B_PUBLIC_INN_DISABLED", "writes": 0}
        if env.get("SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED", "NO") == "YES":
            try:
                from .serviceit_vps_b2b_public_inn import run_once as public_inn_once
                public_inn = public_inn_once(exact_env, client, connection)
            except Exception:
                public_inn = {"status": "B2B_PUBLIC_INN_CHECK_FAILED", "writes": 0}
        # V11 supersedes V10's automatic writer (do not call both).
        # The four historic V10-verified CRM cards get one independent V11
        # receipt and archived prior fields; new source-backed cards enter
        # V11 directly after the public V4 research gate. Both are single
        # pass, PG-fenced, with NO external outreach, score or task changes.
        dossier = {"status": "B2B_DOSSIER_V11_DISABLED", "crm_writes": 0}
        if env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED", "NO") == "YES":
            try:
                from .serviceit_vps_b2b_dossier_v11_worker import worker_once
                dossier = worker_once(env, client, connection)
            except Exception:
                dossier = {"status": "B2B_DOSSIER_V11_CHECK_FAILED",
                           "crm_writes": 0}
        # Completely separate document step: never replaces V11 nine fields,
        # public INN, score, stage or task. OFF until explicitly activated.
        tender_docs = {"status": "TDI_DISABLED", "crm_writes": 0}
        if env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED", "NO") == "YES":
            try:
                from .serviceit_vps_b2b_document_worker import run_once as docs_once
                tender_docs = docs_once(exact_env, client, connection)
            except Exception:
                tender_docs = {"status": "TDI_CHECK_FAILED", "crm_writes": 0}
        # V19 additive, one-use supplement for prior V11-signed cards.
        # Disabled until exact four-card no-write preview and guarded release.
        # Shared V11 advisory lock, independent PG receipt, no blind replay.
        v19 = {"status": "B2B_V19_ADDITIVE_DISABLED", "crm_writes": 0}
        if exact_env.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED") == "YES":
            try:
                from .serviceit_b2b_v19_backfill import run_once as backfill_once
                v19 = backfill_once(exact_env, client, connection)
            except Exception:
                v19 = {"status": "B2B_V19_ADDITIVE_CHECK_FAILED", "crm_writes": 0}
        return {
            "b2b_v19_additive": v19,
            "b2b_tender_documents": tender_docs,
            "b2b_dossier_v11": dossier,
            "b2b_public_inn": public_inn,
            "b2b_research": research,
            "status": "VPS_LIVE_CYCLE_COMPLETE",
            "handled": handled, "review": review,
            "poll": polled["status"],
            "queue_counts": metrics(connection),
            "max_new_cards_per_cycle": MAX_ITEMS,
            "historical_cards_touched": v19.get("crm_writes") == 1,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--migrate", action="store_true")
    group.add_argument("--once", action="store_true")
    group.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.migrate:
        try:
            with db(env) as connection:
                migrate(connection)
                migrate_native(connection)
                migrate_actions(connection)
                migrate_msp(connection)
                migrate_poll(connection)
            safe_log({"status": "PG_NATIVE_ALL_SCHEMAS_READY", "crm_writes": 0})
            return 0
        except Exception:
            safe_log({"status": "PG_QUEUE_MIGRATION_FAILED", "crm_writes": 0})
            return 2
    while True:
        cycle_started = time.monotonic()
        try:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                result = run_once(env, client=client)
            safe_log(result)
            if not args.loop:
                return 0
        except Exception:
            safe_log({"status": "VPS_CYCLE_BLOCKED_OR_UNVERIFIED",
                      "crm_writes": "UNVERIFIED",
                      "no_blind_retry": True})
            if not args.loop:
                return 2
        # 5 min start-to-start; never overlap cycles or retry uncertain cards.
        time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - cycle_started)))


if __name__ == "__main__":
    raise SystemExit(main())
