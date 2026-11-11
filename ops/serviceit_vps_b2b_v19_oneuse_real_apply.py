#!/usr/bin/env python3
"""One explicit V19 post-preview additive write (a real signed B2B card).

Owner-triggered VPS 300s coordinator; no fake demo. Exactly one old V11-
verified Bitrix 1062/21 B2B card, sources independently verified or marked as
INDEX ONLY. No leads, outreach, STOP/HOLD/score/task edits, no automatic replay.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys
import httpx

FILES=("serviceit_b2b_v19_backfill.py","serviceit_vps_worker.py")

def say(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,64}",key)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,2}|[A-Z0-9_]{4,90})",str(value))
    print("V19_ONCE_"+key+"="+str(value),flush=True)


def run():
    from leadpilot.serviceit_vps_worker import live_permitted, INTERVAL_SECONDS
    from leadpilot.serviceit_vps_pg_queue import db
    from leadpilot.serviceit_b2b_v19_backfill import run_once
    if len(sys.argv)!=3 or any(re.fullmatch(r"[a-f0-9]{64}",s) is None
                               for s in sys.argv[1:]):
        say("SOURCE_SHA_ARG","NO")
        return 2
    shaok=all((Path("/app/leadpilot")/n).is_file() and hashlib.sha256(
        (Path("/app/leadpilot")/n).read_bytes()).hexdigest()==s
        for n,s in zip(FILES,sys.argv[1:]))
    say("LIVE_SOURCE_SHA","YES" if shaok else "NO")
    env=dict(os.environ)
    permit=live_permitted(env) and INTERVAL_SECONDS==300
    say("LIVE_300S_GATES","YES" if permit else "NO")
    # A separate guarded activation may later enable 5-min auto writer.
    # This one-use pilot refuses to race with that auto writer.
    auto=env.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED","NO")=="YES"
    say("AUTOMATIC_V19_OFF","NO" if auto else "YES")
    if not shaok or not permit or auto:
        return 2
    with httpx.Client(trust_env=False, follow_redirects=False,timeout=20) as client:
        with db(env) as conn:
            result=run_once(env,client,conn)
    status=result.get("status","V19_NO_STATUS")
    say("STATUS",status)
    writes=result.get("crm_writes",0)
    say("ONE_CRM_WRITE_VERIFIED","YES" if status=="V19_ADDITIVE_NINE_READBACK_VERIFIED"
        and writes==1 else "NO")
    say("NEW_LINES",min(int(result.get("source_lines",0)),99))
    return 0 if status=="V19_ADDITIVE_NINE_READBACK_VERIFIED" and writes==1 else 2


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("OUTCOME_UNVERIFIED","YES")
        raise SystemExit(2)
