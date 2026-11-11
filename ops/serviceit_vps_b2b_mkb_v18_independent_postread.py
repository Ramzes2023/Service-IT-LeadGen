#!/usr/bin/env python3
"""Independent V18 MKB #17 CRM readback against durable PG one-use receipt.

READ-ONLY. No RouterAI/search/model, CRM updates, PG DDL, secrets or raw data
written to logs. Source-only factual addendum must exactly match PG WRITE_INTENT
and be independent from the writer's earlier inline readback.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys
import httpx

TABLE="serviceit_vps_b2b_mkb_4675475_manual_receipts"
CARD=17
TENDER="4675475"
VERSION="MKB_4675475_PUBLIC_OPERATOR_20261111_V1"

def out(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,62}",key)
    assert value in ("YES","NO","NONE","VERIFIED","UNCERTAIN",
                     "CONFLICT","RESERVED","WRITE_INTENT")
    print("MKB_V18_POSTREAD_"+key+"="+value,flush=True)

def run():
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_worker import live_permitted
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
    from leadpilot.serviceit_b2b_dossier_v11 import original_nine
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest

    if len(sys.argv)!=2 or re.fullmatch(r"[a-f0-9]{64}",sys.argv[1]) is None:
        out("SHA_ARGUMENT","NO")
        return 2
    path=Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    source_ok=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==sys.argv[1]
    out("PINNED_V18_LIVE","YES" if source_ok else "NO")
    env=dict(os.environ)
    out("FIVE_MINUTE_GATES","YES" if live_permitted(env) else "NO")
    if not source_ok or not live_permitted(env):
        return 2

    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"""
                    SELECT version,status,before_fields,after_fields,salt,digest
                    FROM {TABLE} WHERE card_id=%s
                """,(CARD,))
                row=cur.fetchone()
                out("RECEIPT_EXISTS","YES" if row is not None else "NO")
                if row is None:
                    return 2
                version,status,before,after,salt,digest=row
                out("RECEIPT_VERSION","YES" if version==VERSION else "NO")
                out("RECEIPT_STATUS",str(status))
                out("DIFFERENT_BEFORE","YES" if before!=after else "NO")
                if version!=VERSION or status!="VERIFIED" or type(after) is not dict:
                    return 2

    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        item=normalized(Rest(webhook,client)("crm.item.get",{
            "entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y"})["item"])
        validate_identity(item,CARD)
        out("EXACT_CRM_ENTITY","YES")
        link=str(item.get(PREFIX+"SIGNAL_URL") or "")
        out("TENDER_LINK_MATCH","YES" if "b2b-center.ru" in link and TENDER in link else "NO")
        nine=original_nine(item)
        out("EXACT_NINE_FIELD_READBACK","YES" if nine==after else "NO")
        out("DURABLE_DIGEST","YES" if salt is not None and digest is not None
            and compute_digest(nine,bytes(salt))==bytes(digest) else "NO")
        full=nine[PREFIX+"AI_FULL_REPORT"]
        brief=nine[PREFIX+"AI_MANAGER_BRIEF"]
        summary=nine[PREFIX+"AI_SUMMARY"]
        out("REPORT_SOURCE_ADDED","YES" if
            "12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475" in full
            and "https://zakupki.kontur.ru/4675475_40" in full else "NO")
        out("BRIEF_INN_PRESENT","YES" if
            "7734202860" in brief and "16.11.2026" in brief else "NO")
        out("SUMMARY_INN_PRESENT","YES" if
            "7734202860" in summary and "16.11.2026" in summary else "NO")
        out("ORIGINAL_REPORT_PRESERVED","YES" if
            full.startswith(before[PREFIX+"AI_FULL_REPORT"]) else "NO")
        out("EXACT_THREE_FIELDS_CHANGED","YES" if
            len([k for k in nine if nine[k]!=before[k]])==3 else "NO")
        ok=(nine==after and compute_digest(nine,bytes(salt))==bytes(digest)
            and "7734202860" in summary and "7734202860" in brief
            and "12  РЕАЛЬНЫЕ СВЕДЕНИЯ" in full
            and len([k for k in nine if nine[k]!=before[k]])==3)
        out("INDEPENDENT_ACCEPTANCE","YES" if ok else "NO")
        return 0 if ok else 2

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        out("EXCEPTION_SUPPRESSED","YES")
        raise SystemExit(2)
