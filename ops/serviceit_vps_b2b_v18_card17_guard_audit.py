#!/usr/bin/env python3
"""V18 read-only WHY manual #17 improvement did not write. Fixed status only."""
import os
import re
from pathlib import Path
import hashlib
import httpx

TARGET=17
SOURCE="B2B-CENTER-4675475"
MANUAL_TABLE="serviceit_vps_b2b_mkb_4675475_manual_receipts"
MIRROR_SHA="126ce5d0f76544e5edc17e1c4a557258a069dbfd2558e5297fb39bb3012d05f5"

def out(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,65}",k)
    assert re.fullmatch(r"(YES|NO|NONE|RESERVED|WRITE_INTENT|VERIFIED|CONFLICT|UNCERTAIN|[0-9]{1,5}|[A-Z_]{5,70})",str(v))
    print("V18_17_GUARD_"+k+"="+str(v),flush=True)

def run():
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_worker import live_permitted
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_b2b_dossier_v11 import original_nine
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
    from leadpilot.bitrix_metadata_audit import validate_webhook

    env=dict(os.environ)
    out("LIVE_GATES","YES" if live_permitted(env) else "NO")
    live=Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    out("V18_MIRROR_ACTIVE","YES" if live.is_file() and
        hashlib.sha256(live.read_bytes()).hexdigest()!=MIRROR_SHA else "NO")
    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT source_id,state FROM serviceit_vps_b2b_inbox WHERE card_id=%s",(TARGET,))
                rows=cur.fetchall()
                out("SIGNED_ROWS",len(rows))
                out("SOURCE_ID_MATCH","YES" if len(rows)==1 and rows[0][0]==SOURCE else "NO")
                out("SOURCE_VERIFIED","YES" if len(rows)==1 and rows[0][1]=="VERIFIED" else "NO")
                cur.execute("SELECT to_regclass(%s)",("public."+MANUAL_TABLE,))
                receipt_exists=cur.fetchone()[0] is not None
                out("RECEIPT_TABLE","YES" if receipt_exists else "NO")
                state="NONE"
                if receipt_exists:
                    cur.execute(f"SELECT status FROM {MANUAL_TABLE} WHERE card_id=%s",(TARGET,))
                    status=cur.fetchone()
                    state=str(status[0]) if status else "NONE"
                out("RECEIPT_STATUS",state)
                cur.execute("SELECT count(*) FROM serviceit_vps_native_receipts WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')")
                out("NATIVE_INFLIGHT",min(int(cur.fetchone()[0]),99999))
                for table,key in (
                    ("serviceit_vps_b2b_dossier_v11_receipts","V11_INFLIGHT"),
                    ("serviceit_vps_b2b_tender_doc_receipts","TDI_INFLIGHT"),
                    ("serviceit_vps_b2b_public_v15_receipts","V15_INFLIGHT")):
                    cur.execute("SELECT to_regclass(%s)",("public."+table,))
                    if cur.fetchone()[0] is not None:
                        cur.execute("SELECT count(*) FROM "+table+" WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')")
                        out(key,min(int(cur.fetchone()[0]),99999))
                    else:
                        out(key,0)
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        item=normalized(Rest(webhook,client)("crm.item.get",{
            "entityTypeId":1062,"id":TARGET,"useOriginalUfNames":"Y"})["item"])
        validate_identity(item,TARGET)
        out("BITRIX_IDENTITY","YES")
        source=str(item.get(PREFIX+"SIGNAL_URL") or "")
        out("BITRIX_SOURCE_ID","YES" if
            "b2b-center.ru" in source and "4675475" in source else "NO")
        try:
            fields=original_nine(item)
            out("ALL_9_AI_FIELDS","YES")
            report=fields[PREFIX+"AI_FULL_REPORT"]
            brief=fields[PREFIX+"AI_MANAGER_BRIEF"]
            summ=fields[PREFIX+"AI_SUMMARY"]
            out("TENDER_NUMBER_IN_REPORT","YES" if "4675475" in report else "NO")
            out("REPORT_LENGTH",len(report))
            out("REPORT_FREE_SPACE",max(0,12000-len(report)))
            out("SUMMARY_FREE_SPACE",max(0,4096-len(summ)))
            out("BRIEF_FREE_SPACE",max(0,4096-len(brief)))
            out("SUPPLEMENT_EXISTS","YES" if "12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475" in report else "NO")
        except Exception:
            out("ALL_9_AI_FIELDS","NO")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        out("AUDIT_FAILURE","YES")
        raise SystemExit(2)
