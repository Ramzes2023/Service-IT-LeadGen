#!/usr/bin/env python3
"""One authorized V19 Bitrix additive write to the already-previewed 2nd card.

The review's LIVE four-card preview showed one old unrelated pending write in
row one, exactly four new leads for row two. This one-time script will write
ONLY if the row that production would choose is precisely that second
previously previewed signed card. Never change other CRM records or native
receipt states; no blind retry of uncertain changes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

MODULES=(
    "serviceit_b2b_tender_index_v18.py",
    "serviceit_b2b_dossier_v11.py",
    "serviceit_vps_worker.py",
    "serviceit_b2b_v19_backfill.py",
)


def say(key, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,58}", key)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{5,70})", str(value))
    print("B2B_V19_ONEUSE_"+key+"="+str(value),flush=True)


def run():
    from leadpilot.serviceit_vps_worker import (
        live_permitted, INTERVAL_SECONDS, exact_environment)
    from leadpilot.serviceit_vps_pg_queue import db
    from leadpilot.serviceit_b2b_v19_backfill import (
        run_once, select_one, active_card_write, TABLE, DDL)

    if len(sys.argv)!=len(MODULES)+1 or any(
            re.fullmatch(r"[a-f0-9]{64}",x) is None for x in sys.argv[1:]):
        say("SOURCE_PIN_OK","NO")
        return 2
    current=all((Path("/app/leadpilot")/file).is_file()
                and not (Path("/app/leadpilot")/file).is_symlink()
                and hashlib.sha256((Path("/app/leadpilot")/file).read_bytes()).hexdigest()
                    == expected
                for file,expected in zip(MODULES,sys.argv[1:]))
    say("SOURCE_PIN_OK","YES" if current else "NO")
    env=dict(os.environ)
    enabled=(env.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES")
    say("SINGLE_PROCESS_OPTIN","YES" if enabled else "NO")
    permitted=live_permitted(env) and INTERVAL_SECONDS==300
    say("EXISTING_LIVE_GATES","YES" if permitted else "NO")
    if not (current and enabled and permitted):
        return 2
    # Only a one-run opt-in: do NOT modify the coordinator's actual persistent
    # environment, Docker compose, five-minute policy or other services.
    exact_environment(env)
    with httpx.Client(trust_env=False,follow_redirects=False,
                      timeout=20.0) as client:
        with db(env) as conn:
            # The read-only earlier preview returned four signed V11 cards.
            preview=select_one(conn,preview=True)
            say("FOUR_SIGNED_SOURCE_CARDS","YES" if len(preview)==4 else "NO")
            if len(preview)!=4:
                return 2
            expected=preview[1]
            if not isinstance(expected[0],int) or expected[0]<=0:
                return 2
            # Safe DDL only, before any CRM writes. All durable PG intent
            # semantics implemented in the existing reviewed V19 worker.
            conn.execute(DDL)
            target=select_one(conn,preview=False)
            say("MATCHES_REVIEWED_SECOND_CARD","YES" if target is not None
                and target[0]==expected[0] and target[1]==expected[1] else "NO")
            if target is None or target[0]!=expected[0] or target[1]!=expected[1]:
                return 2
            pending=active_card_write(conn,target[0])
            say("TARGET_HAS_INFLIGHT_WRITER","YES" if pending else "NO")
            if pending:
                return 2
            result=run_once(env,client,conn,preview=False)
            status=str(result.get("status","V19_UNKNOWN"))
            say("WRITE_STATUS",status)
            say("ONE_REAL_CRM_WRITE","YES" if
                status=="V19_ADDITIVE_NINE_READBACK_VERIFIED"
                and result.get("crm_writes")==1 else "NO")
            return 0 if (status=="V19_ADDITIVE_NINE_READBACK_VERIFIED"
                         and result.get("crm_writes")==1) else 2


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("SAFE_ABORT","YES")
        raise SystemExit(2)
