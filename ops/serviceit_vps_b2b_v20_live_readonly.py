#!/usr/bin/env python3
"""No-write V20 live source hash, coordinator cadence and original B2B receipts."""
from __future__ import annotations
import hashlib,os,re,sys
from pathlib import Path
from leadpilot.serviceit_vps_worker import live_permitted,exact_environment,INTERVAL_SECONDS
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.serviceit_b2b_v19_backfill import TABLE as OLD_TABLE, select_one as v19_select
from leadpilot.serviceit_b2b_v20_versioned import TABLE as NEW_TABLE
from leadpilot.serviceit_vps_pg_queue import db

FILES = ("serviceit_vps_worker.py","serviceit_b2b_v20_versioned.py")
SAFE = frozenset(("NONE","VERIFIED","NO_NEW_EVIDENCE","INSUFFICIENT_CAPACITY",
                  "RESERVED","WRITE_INTENT","CONFLICT","UNCERTAIN"))

def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,64}",k)
    assert re.fullmatch(r"YES|NO|NONE|[0-9]{1,4}|[A-Z0-9_]{4,70}",str(v))
    print("V20_LIVE_"+k+"="+str(v),flush=True)

def main():
    if len(sys.argv)!=3 or not all(re.fullmatch(r"[a-f0-9]{64}",x) for x in sys.argv[1:]):
        say("SOURCE_ARGS","NO")
        return 2
    actual=[hashlib.sha256((Path("/app/leadpilot")/p).read_bytes()).hexdigest()
            if (Path("/app/leadpilot")/p).is_file() else "MISSING"
            for p in FILES]
    match=actual==sys.argv[1:]
    env=dict(os.environ)
    say("EXACT_CODE","YES" if match else "NO")
    say("GATES","YES" if live_permitted(env) else "NO")
    say("CADENCE_300SEC","YES" if INTERVAL_SECONDS==300 else "NO")
    say("V19C_AUTO_ENABLED","YES" if exact_environment(env).get(
        "SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES" else "NO")
    say("V20_OPTIN","YES" if env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED")=="YES" else "NO")
    if not match or not live_permitted(env) or INTERVAL_SECONDS!=300:
        return 2
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          originals=v19_select(conn,preview=True)
          say("SIGNED_ORIGINALS",len(originals))
          cur.execute("SELECT to_regclass(%s)",("public."+NEW_TABLE,))
          v20_exists=cur.fetchone()[0] is not None
          say("V20_RECEIPT_TABLE","YES" if v20_exists else "NO")
          pending=0
          for i,(card,source,notice) in enumerate(originals[:4],1):
            cur.execute("SELECT status FROM "+OLD_TABLE+" WHERE card_id=%s",(card,))
            z=cur.fetchone()
            old=str(z[0]) if z else "NONE"
            assert old in SAFE
            say("CARD_"+str(i)+"_V19",old)
            if v20_exists:
              cur.execute("SELECT status FROM "+NEW_TABLE+
                          " WHERE card_id=%s ORDER BY attempt_no DESC LIMIT 1",(card,))
              y=cur.fetchone()
              fresh=str(y[0]) if y else "NONE"
              assert fresh in SAFE
              say("CARD_"+str(i)+"_V20",fresh)
            cur.execute("SELECT count(*) FROM serviceit_vps_native_receipts "
                        "WHERE card_id=%s AND status IN ('RESERVED','WRITE_INTENT')",(card,))
            in_flight=int(cur.fetchone()[0])
            pending+=in_flight
            say("CARD_"+str(i)+"_NATIVE_PENDING","YES" if in_flight else "NO")
          say("ANY_NATIVE_PENDING","YES" if pending else "NO")
    say("CRM_WRITES","NO")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception:
        say("READONLY_ABORT","YES")
        raise SystemExit(2)
