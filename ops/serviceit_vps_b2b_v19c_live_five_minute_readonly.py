#!/usr/bin/env python3
"""V19C read-only five-minute follow-up evidence: live SHA, gates and PG states.

No search API, model, Bitrix mutation, private CRM field contents or contacts.
"""
import hashlib
import os
from pathlib import Path
import re
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_worker import (
    exact_environment, live_permitted,INTERVAL_SECONDS,
)
from leadpilot.serviceit_b2b_v19_backfill import TABLE,select_one

FILES=("serviceit_b2b_v19_backfill.py","serviceit_vps_worker.py")
KINDS={"NONE","VERIFIED","NO_NEW_EVIDENCE","INSUFFICIENT_CAPACITY",
       "RESERVED","WRITE_INTENT","CONFLICT","UNCERTAIN"}

def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,65}",k)
    assert re.fullmatch(r"(YES|NO|NONE|[0-9]{1,4}|[A-Z0-9_]{4,70})",str(v))
    print("V19C_LIVE_"+k+"="+str(v),flush=True)


def run():
    import sys
    if len(sys.argv)!=3 or any(
            re.fullmatch(r"[a-f0-9]{64}",x) is None for x in sys.argv[1:]):
        say("SOURCE_SHA_ARGS","NO")
        return 2
    hashes=[hashlib.sha256((Path("/app/leadpilot")/x).read_bytes()).hexdigest()
            if (Path("/app/leadpilot")/x).is_file() else "MISSING"
            for x in FILES]
    say("TWO_MODULES_PINNED","YES" if hashes==sys.argv[1:] else "NO")
    env=dict(os.environ)
    say("ORIGINAL_LIVE_GATES","YES" if live_permitted(env) else "NO")
    say("FIVE_MINUTE_INTERVAL","YES" if INTERVAL_SECONDS==300 else "NO")
    exact=exact_environment(env)
    say("FOLLOWUP_ACTIVE_FOR_CYCLE","YES" if exact.get(
        "SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES" else "NO")
    if hashes!=sys.argv[1:] or not live_permitted(env) or INTERVAL_SECONDS!=300:
        return 2
    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                original=select_one(conn,preview=True)
                say("SIGNED_FOUR_PRESENT","YES" if len(original)==4 else "NO")
                if len(original)!=4:
                    return 2
                cur.execute("SELECT to_regclass(%s)",("public."+TABLE,))
                exists=cur.fetchone()[0] is not None
                say("RECEIPT_TABLE_EXISTS","YES" if exists else "NO")
                if not exists:
                    return 2
                statuses=[]
                for i,(card,source,notice) in enumerate(original,1):
                    cur.execute("SELECT status,updated_at FROM "+TABLE+" WHERE card_id=%s",(card,))
                    row=cur.fetchone()
                    state=str(row[0]) if row else "NONE"
                    say("CARD_"+str(i)+"_RECEIPT",state)
                    statuses.append(state)
                cur.execute("SELECT status,count(*) FROM "+TABLE+" GROUP BY status")
                counts={str(s):int(v) for s,v in cur.fetchall()}
                say("ALL_RECEIPTS",sum(counts.values()))
                say("NEW_NO_DATA_RETRY_STATE","YES" if any(
                    s=="NO_NEW_EVIDENCE" for s in statuses[2:]) else "NO")
                choice=select_one(conn,preview=False)
                p=next((i for i,(card,_,_) in enumerate(original,1)
                        if choice and card==choice[0]),0)
                say("NEXT_SCHEDULABLE_ORIGINAL_POSITION",p)
    say("CRM_WRITES","NO")
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("READONLY_ABORT","YES")
        raise SystemExit(2)
