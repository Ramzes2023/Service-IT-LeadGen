"""VPS-only post-call and internal-finance jobs, independent of GitHub.

OFF by default; both call the existing evidence-first Bitrix workers with
PostgreSQL action receipts. No external messages, source intake, payouts
or unrestricted CRM updates. Post-call requires a manager-confirmed
completed call; finance covers only new LeadGen won deals, never support.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import httpx

from .serviceit_vps_pg_queue import db, secret_file, VpsBlocked

INTERVAL_SECONDS = 300
FLAGS = {
    "postcall": "SERVICEIT_VPS_POSTCALL_ENABLED",
    "finance": "SERVICEIT_VPS_FINANCE_ENABLED",
}


def enabled(env):
    state = {}
    for name, variable in FLAGS.items():
        value = env.get(variable, "NO")
        if value not in ("YES", "NO"):
            raise VpsBlocked("NATIVE_BUSINESS_FLAG_INVALID")
        state[name] = value == "YES"
    return state


def gate(env):
    from .serviceit_vps_worker import live_permitted
    if not live_permitted(env):
        raise VpsBlocked("NATIVE_BUSINESS_GLOBAL_CUTOVER_REQUIRED")
    if not env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"):
        raise VpsBlocked("NATIVE_BUSINESS_ACTIVATION_MISSING")


def run_once(env, *, client: httpx.Client, connection):
    state = enabled(env)
    if not any(state.values()):
        return {
            "status": "NATIVE_BUSINESS_DISABLED", "enabled": 0,
            "crm_writes": 0, "tasks_created": 0, "payments": 0,
        }
    gate(env)
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    runtime = {**env, "SERVICEIT_BITRIX_WEBHOOK": webhook}
    report = {
        "status": "NATIVE_BUSINESS_CYCLE",
        "postcall_processed": 0,
        "postcall_needs_review": 0,
        "finance_deals_scanned": 0,
        "finance_needs_review": 0,
        "payments": 0,
        "outbound_contacts": 0,
    }
    if state["postcall"]:
        from .serviceit_postcall_live_worker import (
            run as postcall_run, MODE_WRITE,
        )
        result = postcall_run(
            client, {**runtime, "SERVICEIT_POSTCALL_AUTO_ENABLED": MODE_WRITE},
            write=True, native_connection=connection,
        )
        report["postcall_processed"] = result["processed"]
        report["postcall_needs_review"] = result["needs_review"]
    if state["finance"]:
        from .serviceit_finance_hybrid_live_worker import (
            run as finance_run, ENABLED,
        )
        result = finance_run(
            client, {**runtime, "SERVICEIT_FINANCE_MANAGER_TASK_ENABLED": ENABLED},
            native_connection=connection,
        )
        report["finance_deals_scanned"] = result["new_won_deals_visible"]
        report["finance_needs_review"] = result["blocked"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    mode.add_argument("--flags-only", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.flags_only:
        try:
            state = enabled(env)
            print(json.dumps({"status": "NATIVE_BUSINESS_FLAGS_VALID",
                              "enabled": sum(state.values()),
                              "crm_writes": 0}))
            return 0
        except VpsBlocked:
            print(json.dumps({"status": "NATIVE_BUSINESS_FLAGS_INVALID"}))
            return 2
    while True:
        cycle_started = time.monotonic()
        try:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                with db(env) as connection:
                    result = run_once(env, client=client, connection=connection)
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0
        except Exception:
            print(json.dumps({"status": "NATIVE_BUSINESS_HOLD",
                              "crm_writes": "UNVERIFIED",
                              "payments": 0, "no_blind_retry": True}),
                  flush=True)
            if args.once:
                return 2
        # Five-minute start-to-start with a single sequential loop.
        time.sleep(max(0.0, INTERVAL_SECONDS - (time.monotonic() - cycle_started)))


if __name__ == "__main__":
    raise SystemExit(main())
