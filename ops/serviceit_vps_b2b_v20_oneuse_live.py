#!/usr/bin/env python3
"""Owner-only one-card V20 live evidence pilot: durable exact CRM readback.

No scheduled activation, no new cards, no task/score/contact writes. Uses the
same source identity, advisory lock and receipt gates as production V20.
"""
from __future__ import annotations
import hashlib,os,re,sys
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_worker import (
    live_permitted,exact_environment,INTERVAL_SECONDS,
)
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_b2b_v20_versioned import run_once

FILES=("serviceit_vps_worker.py","serviceit_b2b_v20_versioned.py")
SAFE=frozenset(("V20_NO_NEW_EVIDENCE_OR_CAPACITY","V20_NO_ELIGIBLE_VERIFIED_CARD",
                "V20_OTHER_RESEARCH_ACTIVE","V20_NATIVE_WRITE_IN_FLIGHT",
                "V20_PREWRITE_CRM_CHANGED","V20_VERSIONED_ADDITIVE_VERIFIED"))

def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,64}",k)
    assert re.fullmatch(r"YES|NO|UNVERIFIED|[A-Z0-9_]{4,78}",str(v))
    print("V20_PILOT_"+k+"="+str(v),flush=True)

def main():
    assert len(sys.argv)==3
    assert all(re.fullmatch(r"[a-f0-9]{64}",s) for s in sys.argv[1:])
    actual=[hashlib.sha256((Path("/app/leadpilot")/n).read_bytes()).hexdigest()
            for n in FILES]
    assert actual==sys.argv[1:]
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    assert exact_environment(env).get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES"
    assert env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED","NO")=="NO"
    assert env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")=="YES"
    env["SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED"]="YES"
    say("SOURCE_AND_GATES","YES")
    with httpx.Client(trust_env=False,follow_redirects=False) as client:
        with db(env) as conn:
            result=run_once(env,client,conn)
    state=result.get("status","")
    if state not in SAFE:
        say("UNCERTAIN_STOP","YES")
        return 2
    writes=result.get("crm_writes")
    assert writes in (0,1)
    say("STATE",state)
    say("CRM_WRITES","YES" if writes==1 else "NO")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception:
        say("UNVERIFIED_ABORT","YES")
        raise SystemExit(2)
