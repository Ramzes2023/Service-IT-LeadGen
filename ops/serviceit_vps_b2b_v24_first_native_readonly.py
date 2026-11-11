#!/usr/bin/env python3
"""No-write exact first-original B2B native pending receipt readback.

A native WRITE_INTENT does not prove CRM was written. Reconcile its
pre-committed salted expected digest by independently GETting the same
Bitrix card. Never rewrite CRM, clear the fence, retry AI or mutate PG.
Only safe fixed status codes reach CI logs.
"""
from __future__ import annotations
import hashlib,os,re,sys
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one
from leadpilot.serviceit_vps_native_fence import (
    PostgresNativeFence,NATIVE_NAMESPACES,
)
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.bitrix_metadata_audit import validate_webhook

FILES=("serviceit_vps_native_fence.py","serviceit_vps_worker.py",
       "serviceit_b2b_v19_backfill.py")
ALLOWED=frozenset(("EXACT_TARGET_READBACK","UNCERTAIN_REMAINS",
                   "ALREADY_FENCED_NO_RETRY","READBACK_ERROR"))

def emit(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,75}",key)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,80})",str(value))
    print("V24_FIRST_"+key+"="+str(value),flush=True)


def main():
    assert len(sys.argv)==1+len(FILES)
    assert all(re.fullmatch(r"[a-f0-9]{64}",s) for s in sys.argv[1:])
    actual=[hashlib.sha256((Path("/app/leadpilot")/p).read_bytes()).hexdigest()
            for p in FILES]
    assert actual==sys.argv[1:]
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    emit("GATES_AND_HASHES","YES")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          originals=select_one(conn,preview=True)
          assert len(originals)==4
          card,source,notice=originals[0]
          assert isinstance(source,str) and source.startswith("B2B-CENTER-")
          assert notice["tender_id"]==source.removeprefix("B2B-CENTER-")
          emit("SIGNED_FIRST","YES")
          cur.execute("""
            SELECT namespace,status,
                   cardinality(field_names),digest_salt IS NOT NULL,
                   expected_digest IS NOT NULL,
                   EXTRACT(EPOCH FROM(now()-updated_at))/60
            FROM serviceit_vps_native_receipts
            WHERE card_id=%s
              AND status IN ('RESERVED','WRITE_INTENT')
            ORDER BY namespace
          """,(card,))
          rows=cur.fetchall()
          emit("PENDING_COUNT",min(99,len(rows)))
          with httpx.Client(trust_env=False,follow_redirects=False) as client:
            rest=Rest(webhook,client)
            for i,(namespace,status,nfields,salt,digest,age) in enumerate(rows[:8],1):
              assert namespace in NATIVE_NAMESPACES
              emit("ROW_"+str(i)+"_NAMESPACE",namespace)
              emit("ROW_"+str(i)+"_STATUS",status)
              emit("ROW_"+str(i)+"_FIELD_COUNT",min(99,int(nfields or 0)))
              emit("ROW_"+str(i)+"_DIGEST_READY",
                   "YES" if salt and digest else "NO")
              emit("ROW_"+str(i)+"_OLDER_60MIN",
                   "YES" if float(age)>60 else "NO")
              try:
                  result=PostgresNativeFence(
                      conn,namespace=namespace).reconcile(card,rest)
              except Exception:
                  result="READBACK_ERROR"
              assert result in ALLOWED
              emit("ROW_"+str(i)+"_RECONCILE",result)
    emit("CRM_WRITES","NO")
    emit("PG_WRITES","NO")
    return 0

if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        emit("AUDIT_BLOCKED","YES")
        raise SystemExit(2)
