#!/usr/bin/env python3
"""Independent no-write reconciliation of V23 real CRM updates and PG receipts.

Prove all three V23 receipt rows, source hashes and current Bitrix original
nine fields independently, including preservation of six untouched AI fields,
and report the older native in-flight fence blocking the first of four cards.
No public fetch, no RouterAI/Brave and no CRM/PG mutations.
"""
from __future__ import annotations
import hashlib,json,os,re,sys
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one, REPORT,SUMMARY,BRIEF
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.bitrix_metadata_audit import validate_webhook

TABLE="serviceit_vps_b2b_v23_primary_source_receipts"
FILES=("serviceit_b2b_v19_backfill.py","serviceit_b2b_routerai_deep.py",
       "serviceit_vps_worker.py")
AFFECTED=frozenset((REPORT,SUMMARY,BRIEF))


def emit(name,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,75}",name)
    assert re.fullmatch(r"(YES|NO|NONE|[0-9]{1,3}|[A-Z0-9_]{4,75})",str(value))
    print("V23_AUDIT_"+name+"="+str(value),flush=True)


def main():
    assert len(sys.argv)==1+len(FILES)
    assert all(re.fullmatch(r"[a-f0-9]{64}",x) for x in sys.argv[1:])
    actual=[hashlib.sha256((Path("/app/leadpilot")/file).read_bytes()).hexdigest()
            for file in FILES]
    assert actual==sys.argv[1:]
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    emit("EXACT_CODE_AND_GATES","YES")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    verified=0
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          originals=select_one(conn,preview=True)
          assert len(originals)==4
          emit("SIGNED_FOUR","YES")
          cur.execute("SELECT to_regclass(%s)",("public."+TABLE,))
          assert cur.fetchone()[0] is not None
          with httpx.Client(trust_env=False,follow_redirects=False) as client:
            rest=Rest(webhook,client)
            for i,(card,source,notice) in enumerate(originals,1):
                src=hashlib.sha256(source.encode("ascii")).hexdigest()
                cur.execute(
                    "SELECT source_hash,status,before_fields,after_fields,salt,digest "
                    "FROM "+TABLE+" WHERE card_id=%s",(card,))
                row=cur.fetchone()
                if i==1:
                    emit("CARD_1_V23_RECEIPT","PRESENT" if row else "NONE")
                    cur.execute("""
                      SELECT count(*) FROM serviceit_vps_native_receipts
                      WHERE card_id=%s AND status IN ('RESERVED','WRITE_INTENT')
                    """,(card,))
                    emit("CARD_1_NATIVE_PENDING","YES" if int(cur.fetchone()[0])>0 else "NO")
                    continue
                assert row is not None, "V23_MISSING_EXPECTED_RECEIPT"
                src_hash,status,before,after,salt,digest=row
                assert status=="VERIFIED" and src_hash==src
                assert type(before) is dict and type(after) is dict
                assert isinstance(salt,bytes) and isinstance(digest,bytes)
                changed={k for k in before if before.get(k)!=after.get(k)}
                assert changed==AFFECTED and set(before)==set(after)
                assert after[REPORT].startswith(before[REPORT])
                assert after[SUMMARY].endswith(before[SUMMARY])
                assert after[BRIEF].endswith(before[BRIEF])
                assert compute_digest(after,salt)==digest
                current=normalized(rest("crm.item.get",{
                    "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                validate_identity(current,card)
                assert original_nine(current)==after
                # Verify CRM's own immutable B2B original notice URL and ID.
                from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
                url,title=safe_notice(notice,card,source,current)
                assert url==notice["source_url"] and bool(title)
                emit("CARD_"+str(i)+"_V23_RECEIPT","VERIFIED")
                emit("CARD_"+str(i)+"_BITRIX_EXACT_NINE","YES")
                emit("CARD_"+str(i)+"_ONLY_THREE_AI_CHANGED","YES")
                verified+=1
    emit("ALL_THREE_INDEPENDENTLY_VERIFIED","YES" if verified==3 else "NO")
    emit("CRM_WRITES","NO")
    emit("PG_WRITES","NO")
    return 0 if verified==3 else 2

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        emit("INDEPENDENT_AUDIT_ABORT","YES")
        raise SystemExit(2)
