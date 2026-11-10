"""VPS-native auxiliary processes for new Bitrix 1062/category21 cards.

Reuses reviewed ServiceIT STOP/dedup/Kanban/task business functions and
replaces the GitHub Fernet reservation with independent PostgreSQL receipts.
Does not connect any new sources, call an LLM, contact companies, create
companies/deals, or touch historical cards. Disabled by default. Each action
needs both global VPS cutover proof and its own enabled flag.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import time

import httpx

from .serviceit_vps_pg_queue import db, VpsBlocked, secret_file
from .serviceit_vps_native_fence import PostgresNativeFence, NAMESPACE
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, discover,
)
from .serviceit_bitrix_batch_worker import list_only
from .bitrix_metadata_audit import validate_webhook

INTERVAL_SECONDS = 300
MAX_CARDS = 6
FLAGS = {
    "own_stop": "SERVICEIT_VPS_OWN_STOP_ENABLED",
    "crm_duplicates": "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED",
    "kanban_title": "SERVICEIT_VPS_KANBAN_TITLE_ENABLED",
    "manager_task": "SERVICEIT_VPS_MANAGER_TASK_ENABLED",
    "fns_crm_result": "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED",
    "provisional_score": "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED",
    "free_msp_score": "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED",
}
NAMESPACES = {
    "own_stop": "OWN_STOP_V1",
    "crm_duplicates": "CRM_DUP_V1",
    "kanban_title": "KANBAN_TITLE_V1",
    "manager_task": "TASK_V1",
    "fns_crm_result": "FNS_CRM_V1",
    "provisional_score": "PROVISIONAL_SCORE_V1",
    "free_msp_score": "FREE_MSP_SCORE_V1",
}


def enabled(env: dict) -> dict:
    flags = {}
    for key, envname in FLAGS.items():
        raw = env.get(envname, "NO")
        if raw not in ("YES", "NO"):
            raise VpsBlocked("NATIVE_AUX_FLAG_INVALID")
        flags[key] = raw == "YES"
    if flags["provisional_score"] and flags["free_msp_score"]:
        raise VpsBlocked("NATIVE_CONFLICTING_SCORE_VERSIONS")
    return flags


def global_gate(env: dict) -> None:
    from .serviceit_vps_worker import live_permitted
    if not live_permitted(env):
        raise VpsBlocked("NATIVE_AUX_CUTOVER_NOT_APPROVED")
    if not env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"):
        raise VpsBlocked("NATIVE_AUX_CUTOVER_TIME_MISSING")


def calibrate_verified_live_card(client, base, connection):
    """Positive control from PostgreSQL VERIFIED real B2B -> exact Bitrix GET.

    The historical test ID 3 can be absent from a live portal. Do not
    substitute an arbitrary ID or trust an empty CRM list as success.
    This source is never enumerated as a new card or updated.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT card_id FROM serviceit_vps_b2b_inbox "
            "WHERE state='VERIFIED' AND card_id IS NOT NULL "
            "ORDER BY received_at,source_id LIMIT 1")
        row = cur.fetchone()
    if not row or type(row[0]) is not int or row[0] < 8:
        raise VpsBlocked("NATIVE_FNS_NO_VERIFIED_POSITIVE_CONTROL")
    from .serviceit_stage4_fns_dedupe_watch import read_item
    from .serviceit_bitrix_poll_fallback import date_value
    verified = read_item(client, base, row[0])
    # read_item verifies 1062 / category 21 and exact item identity.
    date_value(verified.get("createdTime"))
    return True


def eligible_cards(client, env, base, now, connection=None):
    activated = env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"]
    start = after_activation({
        **env, "SERVICEIT_BITRIX_POLL_START_UTC": activated,
    }, now)
    reader = lambda method, params: list_only(client, base, method, params)
    # Discover strictly validates every returned ID, category and timestamp.
    # Always independently check one previously VERIFIED source-backed CRM item;
    # never use missing historical card #3 as a permanent production gate.
    if connection is None:
        calibrate_known_card(reader)  # legacy offline callers, fail closed
    else:
        calibrate_verified_live_card(client, base, connection)
    cards = discover(reader, start, now)
    windows = max(1, (len(cards) + MAX_CARDS - 1) // MAX_CARDS)
    slot = (int(now.timestamp()) // INTERVAL_SECONDS) % windows
    return cards[slot*MAX_CARDS:(slot+1)*MAX_CARDS]


def ai_receipt_allows_task(connection, card):
    """Authorize an internal review task only after a proven exact nine-field readback.

    B2B 2026-11-10 recovery intentionally retained the original failed
    non-BMP AI_NINE_V1 write as NEEDS_REVIEW, with the replacement nine
    fields independently verified under AI_BMP_REPAIR_V1. This is valid
    evidence of *manager HOLD fields*, not of qualified leads or model work.
    Never treat an arbitrary failed receipt alone as task authorization.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT namespace,status FROM serviceit_vps_native_receipts "
            "WHERE card_id=%s AND namespace IN (%s,%s)",
            (card, NAMESPACE, "AI_BMP_REPAIR_V1"))
        receipts = dict(cur.fetchall())
    original = receipts.get(NAMESPACE)
    if original in ("HOLD_9_WRITTEN", "HOLD_9_PRESENT", "VERIFIED"):
        return True
    return (original == "NEEDS_REVIEW"
            and receipts.get("AI_BMP_REPAIR_V1") == "VERIFIED")


def run_once(env: dict, *, client: httpx.Client, connection,
             now: datetime | None = None) -> dict:
    flags = enabled(env)
    if not any(flags.values()):
        return {"status": "NATIVE_AUX_DISABLED", "crm_writes": 0,
                "new_card_candidates": 0, "checks": 0, "review": 0}
    global_gate(env)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    base = validate_webhook(webhook)
    cards = eligible_cards(client, env, base, now, connection)
    runtime_env = {
        **env, "SERVICEIT_BITRIX_WEBHOOK": webhook,
        # Reuse reviewed future-only validators, but NEVER the old cloud
        # activation date. Every adapter sees the native cutover timestamp.
        "SERVICEIT_BITRIX_POLL_START_UTC":
            env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"],
        "SERVICEIT_STAGE3_TASK_WRITE_ENABLED": (
            "YES_1062_21_INTERNAL_REVIEW"
            if flags["manager_task"] else "NO"),
        "SERVICEIT_STAGE4_PRELIMINARY_SCORE_ENABLED": (
            "YES_1062_21_PROVISIONAL_ONLY"
            if flags["provisional_score"] else "NO"),
        "SERVICEIT_LEADGEN_FREE_SCORE_ENABLED": (
            "YES_1062_21_FREE_MSP_INTERNAL"
            if flags["free_msp_score"] else "NO"),
    }
    # The explicit real employee fallback is kept unset unless separately
    # approved by the company and present as a genuine valid ID.
    totals = {"status": "NATIVE_AUX_CYCLE",
              "new_card_candidates": len(cards),
              "checks": 0, "review": 0,
              "own_stop_held": 0, "duplicate_notes_verified": 0,
              "kanban_titles_verified": 0, "tasks_verified": 0,
              "fns_results_verified": 0, "score_verified": 0,
              "crm_global_stop_clear": False,
              "external_contacts": 0, "payments": 0}
    for card in cards:
        stopped = False
        if flags["own_stop"]:
            from .serviceit_own_signal_stop_auto import (
                process_one as own_process, read_rules,
            )
            try:
                rules = read_rules()
                if rules:
                    fence = PostgresNativeFence(connection, NAMESPACES["own_stop"])
                    status = own_process(client, runtime_env, base, card, fence, rules,
                                         now=now)
                    stopped = status in (
                        "OWN_STOP_NOTE_READBACK_VERIFIED",
                        "ALREADY_EXACT_OWN_STOP_NOTE",
                        "OWN_STOP_ALREADY_RESERVED_NO_RETRY",
                        "OWN_STOP_NOTE_UNCERTAIN_NO_RETRY",
                        "OWN_STOP_NOTE_VERIFIED_LEDGER_UNCERTAIN",
                    )
                    totals["own_stop_held"] += int(stopped)
                    totals["checks"] += 1
                    totals["review"] += int("UNCERTAIN" in status)
            except Exception:
                stopped = True
                totals["review"] += 1
        if stopped:
            continue
        if flags["crm_duplicates"]:
            from .serviceit_crm_duplicate_auto import inspect_one
            try:
                fence = PostgresNativeFence(connection, NAMESPACES["crm_duplicates"])
                status = inspect_one(client, runtime_env, base, card, fence, now=now)
                totals["duplicate_notes_verified"] += int(
                    status == "EXACT_DUPLICATE_NOTE_READBACK")
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in status)
            except Exception:
                totals["review"] += 1
                # Do not create downstream task after uncertain dedup.
                continue
        if flags["kanban_title"]:
            from .serviceit_kanban_live_title_auto import process_one as title_process
            try:
                fence = PostgresNativeFence(connection, NAMESPACES["kanban_title"])
                status = title_process(client, runtime_env, base, card, fence,
                                       now=now)
                totals["kanban_titles_verified"] += int(
                    status in ("TITLE_EXACT_READBACK_VERIFIED",
                               "ALREADY_CORRECT_NATIVE_TITLE"))
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in status)
            except Exception:
                totals["review"] += 1
                continue
        if flags["fns_crm_result"]:
            from .serviceit_vps_native_msp_result import process_one as fns_write
            try:
                status = fns_write(client, runtime_env, connection, base,
                                   card, now=now)
                totals["fns_results_verified"] += int(
                    status in ("NATIVE_FNS_CRM_READBACK_VERIFIED",
                               "NATIVE_FNS_RESULT_ALREADY_PRESENT"))
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in status)
            except Exception:
                totals["review"] += 1
                # A missing or unverified FNS result never grants Score/contact.
        if flags["provisional_score"]:
            from .serviceit_stage4_provisional_score_live import (
                evaluate_one as score_once)
            try:
                fence = PostgresNativeFence(
                    connection, NAMESPACES["provisional_score"])
                state = score_once(
                    client, runtime_env, card, now=now, native_fence=fence)
                totals["score_verified"] += int(
                    state in ("PROVISIONAL_SCORE_EXACT_READBACK",
                              "ALREADY_PROVISIONAL_NO_REWRITE"))
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in state)
            except Exception:
                totals["review"] += 1
        if flags["free_msp_score"]:
            from .serviceit_leadgen_free_msp_score import (
                evaluate_one as free_msp_score_once)
            try:
                fence = PostgresNativeFence(
                    connection, NAMESPACES["free_msp_score"])
                state = free_msp_score_once(
                    client, runtime_env, card, now=now,
                    native_fence=fence, native_connection=connection)
                totals["score_verified"] += int(
                    state in ("INTERNAL_SCORE_EXACT_READBACK",
                              "ALREADY_INTERNAL_SCORE_NO_REWRITE"))
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in state)
            except Exception:
                totals["review"] += 1
        if flags["manager_task"]:
            from .serviceit_stage3_all_new_task_automation import process_card
            if not ai_receipt_allows_task(connection, card):
                totals["review"] += 1
                continue
            try:
                fence = PostgresNativeFence(connection, NAMESPACES["manager_task"])
                status = process_card(client, runtime_env, card, now=now,
                                      native_fence=fence)
                totals["tasks_verified"] += int(
                    status in ("INTERNAL_REVIEW_TASK_READBACK_VERIFIED",
                               "VISIBLE_TASK_EXISTS_NO_WRITE"))
                totals["checks"] += 1
                totals["review"] += int("UNCERTAIN" in status)
            except Exception:
                totals["review"] += 1
    return totals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--loop", action="store_true")
    modes.add_argument("--flags-only", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        if args.flags_only:
            state = enabled(env)
            print(json.dumps({"status": "NATIVE_AUX_FLAGS_VALID",
                              "enabled_modules": sum(state.values()),
                              "crm_writes": 0}, sort_keys=True))
            return 0
        while True:
            cycle_started = time.monotonic()
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                with db(env) as connection:
                    result = run_once(env, client=client, connection=connection)
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0
            # Aim for 5-minute start-to-start, without parallel workers.
            time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - cycle_started)))
    except Exception:
        print(json.dumps({"status": "NATIVE_AUX_BLOCKED",
                          "crm_writes": "UNVERIFIED",
                          "no_blind_retry": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
