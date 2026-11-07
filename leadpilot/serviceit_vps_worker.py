"""VPS coordinator: Postgres queue -> existing signed exact-card worker.

No new source ingestion, no CRM company/contact creation and no historical
backfill. Reuses the SAME GitHub encrypted one-use ledger as cloud workers to
fence cross-host duplicates during migration. Nothing writes CRM unless a
separate operator LIVE gate is explicitly enabled after cutover.
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
from .serviceit_bitrix_dispatch_worker import (
    EVENT, REPOSITORY, run as exact_run,
)
from .serviceit_bitrix_poll_fallback import (
    signed_payload, after_activation, discover, calibrate_known_card,
)
from .serviceit_bitrix_batch_worker import list_only
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS

MAX_ITEMS = 6
INTERVAL_SECONDS = 600


def safe_log(payload):
    # Fixed status dictionary only; never customer text, URLs, secret or IDs.
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def live_permitted(env: dict) -> bool:
    return (
        env.get("SERVICEIT_VPS_WRITER_MODE") == "LIVE"
        and env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED") == "YES"
        and env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") == "YES"
        and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
        and (env.get("SERVICEIT_AI_PROVIDER") != "routerai"
             or env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21")
    )


def exact_environment(env: dict) -> dict:
    """Convert server secret files into the legacy exact-worker API boundary."""
    result = dict(env)
    for name in (
        "SERVICEIT_BITRIX_WEBHOOK",
        "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY",
        "SERVICEIT_GH_LEDGER_KEY",
        "GITHUB_TOKEN",
        "SERVICEIT_BRAVE_SEARCH_API_KEY",
    ):
        result[name] = secret_file(env, name)
    if env.get("SERVICEIT_AI_PROVIDER", "") == "routerai":
        result["AI_PROVIDER"] = "routerai"
        result["ROUTERAI_API_KEY"] = secret_file(env, "SERVICEIT_ROUTERAI_API_KEY")
    else:
        result["AI_PROVIDER"] = "gigachat"
    result["GIGACHAT_CREDENTIALS"] = secret_file(
        env, "SERVICEIT_GIGACHAT_CREDENTIALS", optional=True)
    result["SERVICEIT_CORPORATE_APPROVALS_JSON"] = "{}"
    result["GITHUB_REPOSITORY"] = REPOSITORY
    result["GITHUB_REF"] = "refs/heads/main"
    result["GITHUB_EVENT_NAME"] = "repository_dispatch"
    result["SERVICEIT_BITRIX_EVENT_AI_ENABLED"] = "YES_1062_21_CLOUD"
    result["AI_ENABLE"] = "1"
    result["SERVICEIT_PUBLIC_RESEARCH_ENABLED"] = "YES"
    result["SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"] = "YES"
    result["SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED"] = "YES"
    result["SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED"] = "YES"
    result["SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT"] = (
        "YES" if result["GIGACHAT_CREDENTIALS"] else "NO")
    # The remote one-use reservation + before image + 9-field readback are
    # inside exact_run; this layer cannot bypass those gates.
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
    since = after_activation(env, now)
    def rest(method, args):
        return list_only(client, base, method, args)
    calibrate_known_card(rest)
    cards = discover(rest, since, now)
    added = 0
    if not preview_only:
        for card in cards:
            added += int(insert_new(connection, card))
    return {"status": "POLL_READONLY" if preview_only else "POLL_QUEUED",
            "candidates": len(cards), "queued": added, "crm_writes": 0}


def run_once(env: dict, *, client: httpx.Client) -> dict:
    with db(env) as connection:
        if not live_permitted(env):
            result = readonly_preflight(env, client=client)
            result["queue_counts"] = metrics(connection)
            result["migration_cutover"] = "NOT_AUTHORIZED"
            return result
        exact_env = exact_environment(env)
        # Validate source hosts, provider privacy, remote ledger config etc
        # before reserving any item in Postgres or starting side effects.
        from .serviceit_bitrix_dispatch_worker import gate
        if gate(exact_env) != "READY":
            raise VpsBlocked("EXACT_GATE_NOT_READY")
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
                event = {
                    "action": EVENT,
                    "repository": {"full_name": REPOSITORY},
                    "client_payload": signed_payload(
                        card, exact_env["SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"],
                        int(time.time())),
                }
                outcome = exact_run(event, exact_env, client=client)
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
        return {
            "status": "VPS_LIVE_CYCLE_COMPLETE",
            "handled": handled, "review": review,
            "poll": polled["status"],
            "queue_counts": metrics(connection),
            "max_new_cards_per_cycle": MAX_ITEMS,
            "historical_cards_touched": False,
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
            safe_log({"status": "PG_QUEUE_SCHEMA_READY", "crm_writes": 0})
            return 0
        except Exception:
            safe_log({"status": "PG_QUEUE_MIGRATION_FAILED", "crm_writes": 0})
            return 2
    while True:
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
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
