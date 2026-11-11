#!/usr/bin/env python3
"""Read-only diagnosis of single historical native receipt blocking #17."""
import os
import re
from leadpilot.serviceit_vps_pg_queue import db

def out(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,45}",k)
    assert re.fullmatch(r"[A-Z0-9_]{1,55}",str(v))
    print("V18_NATIVE_"+k+"="+str(v),flush=True)

def run():
    with db(dict(os.environ)) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    SELECT namespace,card_id,status,
                           EXTRACT(EPOCH FROM(now()-updated_at))/60,
                           field_names IS NOT NULL,
                           expected_digest IS NOT NULL
                    FROM serviceit_vps_native_receipts
                    WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')
                    ORDER BY updated_at LIMIT 20
                """)
                rows=cur.fetchall()
                out("ROWS",len(rows))
                for i,(ns,card,state,age,fields,digest) in enumerate(rows,1):
                    p="ROW_"+str(i)+"_"
                    out(p+"NAMESPACE",re.sub("[^A-Z0-9_]","_",str(ns))[:48])
                    out(p+"SAME_CARD_17","YES" if card==17 else "NO")
                    out(p+"STATUS",str(state))
                    out(p+"AGE_OVER_60MIN","YES" if float(age)>60 else "NO")
                    out(p+"AGE_OVER_24H","YES" if float(age)>1440 else "NO")
                    out(p+"DIGEST_PRESENT","YES" if digest else "NO")
                    out(p+"FIELDS_LIST_PRESENT","YES" if fields else "NO")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        out("ERROR","YES")
        raise SystemExit(2)
