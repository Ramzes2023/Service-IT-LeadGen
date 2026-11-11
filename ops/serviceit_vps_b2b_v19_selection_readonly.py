#!/usr/bin/env python3
"""V19 exact four signed original cards selection diagnostics, PostgreSQL READ ONLY.

No CRM REST, no Brave calls, no model, no updates. Only fixed yes/no/status
categories, never customer names, tender texts, secrets or other IDs.
"""
from __future__ import annotations
import os
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_b2b_v19_backfill import select_one,TABLE

ALLOWED={"NONE","RESERVED","WRITE_INTENT","VERIFIED","NO_NEW_EVIDENCE",
         "INSUFFICIENT_CAPACITY","CONFLICT","UNCERTAIN"}
def say(k,v):
    assert v in ALLOWED or v in ("YES","NO","ONE","TWO","THREE","FOUR","OTHER","MISSING")
    print("V19_SELECT_"+k+"="+v,flush=True)

def run():
    with db(dict(os.environ)) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                original=select_one(conn,preview=True)
                chosen=select_one(conn,preview=False)
                say("FOUR_ORIGINAL","YES" if len(original)==4 else "NO")
                if len(original)!=4:
                    return 2
                selection=next((x for x,entry in enumerate(original,1)
                                if chosen is not None and chosen[:2]==entry[:2]),0)
                say("CURRENT_SELECTION",{1:"ONE",2:"TWO",3:"THREE",4:"FOUR"}.get(selection,
                      "MISSING" if chosen is None else "OTHER"))
                cur.execute("SELECT to_regclass(%s)",("public."+TABLE,))
                table_exists=cur.fetchone()[0] is not None
                say("V19_RECEIPT_TABLE","YES" if table_exists else "NO")
                for i,(card,source,notice) in enumerate(original,1):
                    cur.execute("""
                        SELECT count(*) FROM serviceit_vps_native_receipts
                        WHERE card_id=%s AND status IN ('RESERVED','WRITE_INTENT')
                    """,(card,))
                    native=int(cur.fetchone()[0])
                    say("CARD_"+str(i)+"_NATIVE_INFLIGHT","YES" if native else "NO")
                    if table_exists:
                        cur.execute("SELECT status FROM "+TABLE+" WHERE card_id=%s",(card,))
                        row=cur.fetchone()
                        state=str(row[0]) if row else "NONE"
                    else:
                        state="NONE"
                    say("CARD_"+str(i)+"_V19_RECEIPT",state)
                return 0
if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("AUDIT_ERROR","YES")
        raise SystemExit(2)
