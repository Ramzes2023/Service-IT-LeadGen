"""Native hourly read-only Bitrix AI/task/Score and manager-funnel observer.

No GITHUB_TOKEN, no AI, no lead source and absolutely no CRM writes.
Reuses the project's reviewed per-card readback and funnel summary routines,
with the VPS-native cutover date when set. Unknown revenue stays null.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import time

import httpx

from .serviceit_vps_pg_queue import secret_file
from .serviceit_live_reconciliation_readonly import check
from .serviceit_supervisor_funnel_readonly import calculate

INTERVAL_SECONDS = 3600


def run_once(env: dict, *, client: httpx.Client,
             now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Before cutover, observe only the last ten minutes. Never use the
    # older GitHub activation date as a fake VPS readiness proof.
    start = env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
    if not start:
        start = (now-timedelta(minutes=10)).isoformat()
    runtime = {
        **env,
        "SERVICEIT_BITRIX_WEBHOOK": secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"),
        "SERVICEIT_BITRIX_POLL_START_UTC": start,
    }
    evidence = check(client, runtime, now=now)
    funnel = calculate(client, runtime, now=now)
    return {
        "status": "NATIVE_OBSERVER_READONLY",
        "ai_nine_complete": evidence["ai_nine_complete"],
        "ai_partial": evidence["ai_partial"],
        "ai_absent": evidence["ai_absent"],
        "linked_task_visible": evidence["linked_task_visible"],
        "task_visibility_unknown": evidence["task_visibility_unknown"],
        "visible_new_cards": evidence["new_cards_visible"],
        "provisional_score_fields_visible":
            evidence["provisional_or_other_score_fields_visible"],
        "separate_sales_funnel_visible": funnel["separate_deal_funnel_ready"],
        "qualified_leads": funnel["qualified_leads"],
        "actual_contacts": funnel["actual_contacts"],
        "paid_revenue": funnel["paid_revenue"],
        "mrr": funnel["mrr"],
        "crm_writes": 0,
        "ai_calls": 0,
        "external_contacts": 0,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--once",action="store_true")
    g.add_argument("--loop",action="store_true")
    args=p.parse_args(argv)
    env=dict(os.environ)
    while True:
        try:
            with httpx.Client(trust_env=False,follow_redirects=False) as client:
                result=run_once(env,client=client)
            print(json.dumps(result,sort_keys=True),flush=True)
            if args.once:
                return 0
        except Exception:
            print(json.dumps({"status":"NATIVE_OBSERVER_UNVERIFIED",
                              "crm_writes":0,"revenue_verified":False}),flush=True)
            if args.once:
                return 2
        time.sleep(INTERVAL_SECONDS)


if __name__=="__main__":
    raise SystemExit(main())
