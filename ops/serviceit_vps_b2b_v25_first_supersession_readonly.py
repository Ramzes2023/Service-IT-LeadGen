#!/usr/bin/env python3
"""V25: READ ONLY proof whether older AI_SUMMARY write was superseded by V11.

Only original signed first B2B card, exact legacy one-field WRITE_INTENT,
current Bitrix crm.item.get and V11 PG verified after_fields/digest. No
Postgres mutation, no Bitrix update, no source crawl or external AI.
All names/values redacted; emit deterministic booleans only.
"""
from __future__ import annotations
import hashlib,os,re,secrets,sys
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX,AI_FIELDS
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_vps_native_fence import digested
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.bitrix_metadata_audit import validate_webhook

FILES=("serviceit_vps_native_fence.py","serviceit_vps_b2b_dossier_v11_worker.py",
       "serviceit_b2b_v19_backfill.py","serviceit_vps_worker.py")
NS="AI_REPAIR_SUMMARY_V1"
KEY=PREFIX+"AI_SUMMARY"

def emit(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,75}",key)
    assert value in ("YES","NO")
    print("V25_FIRST_"+key+"="+value,flush=True)


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
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          originals=select_one(conn,preview=True)
          assert len(originals)==4
          card,source,notice=originals[0]
          assert type(card) is int and re.fullmatch(r"B2B-CENTER-\d{5,12}",source)
          assert notice["tender_id"]==source.removeprefix("B2B-CENTER-")
          emit("SIGNED_FIRST","YES")
          cur.execute("""
            SELECT status,field_names,digest_salt,expected_digest
            FROM serviceit_vps_native_receipts
            WHERE namespace=%s AND card_id=%s
          """,(NS,card))
          old=cur.fetchone()
          assert old is not None
          old_status,names,salt,digest=old
          exact_intent=(old_status=="WRITE_INTENT" and names==[KEY]
                        and isinstance(salt,bytes) and len(salt)==16
                        and isinstance(digest,bytes) and len(digest)==32)
          emit("LEGACY_ONE_FIELD_INTENT", "YES" if exact_intent else "NO")
          if not exact_intent: return 2
          with httpx.Client(trust_env=False,follow_redirects=False) as client:
              rest=Rest(webhook,client)
              current=normalized(rest("crm.item.get",{
                  "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
              validate_identity(current,card)
              url,title=safe_notice(notice,card,source,current)
              assert bool(title) and url==notice["source_url"]
              emit("BITRIX_ORIGINAL_IDENTITY","YES")
              legacy_same=secrets.compare_digest(
                  digested({KEY:current.get(KEY)},bytes(salt)),bytes(digest))
              emit("LEGACY_EXPECTED_VALUE_PRESENT","YES" if legacy_same else "NO")
              cur.execute("""
                SELECT status,source_hash,after_fields,salt,digest
                FROM serviceit_vps_b2b_dossier_v11_receipts
                WHERE card_id=%s
              """,(card,))
              newer=cur.fetchone()
              source_hash=hashlib.sha256(source.encode("ascii")).hexdigest()
              proven=False
              if newer:
                  status,v11hash,v11after,v11salt,v11digest=newer
                  proven=(status=="VERIFIED" and v11hash==source_hash
                          and type(v11after) is dict and
                          set(v11after)==AI_FIELDS and
                          original_nine(current)==v11after and
                          isinstance(v11salt,bytes) and
                          isinstance(v11digest,bytes) and
                          secrets.compare_digest(
                              compute_digest(v11after,v11salt),v11digest))
              emit("NEWER_V11_EXACT_SIGNED_NINE","YES" if proven else "NO")
              # A validated newer full nine-field dossier makes the older
              # one-field intent obsolete even if it did not take effect.
              emit("SUPERSESSION_PROOF","YES" if proven else "NO")
    emit("CRM_WRITES","NO")
    emit("PG_WRITES","NO")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception:
        emit("READONLY_ABORT","YES")
        raise SystemExit(2)
