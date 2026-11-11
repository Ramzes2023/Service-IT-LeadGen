#!/usr/bin/env python3
"""Guarded four signed tender passport reconciliation: preview/apply/readback.

Previews never write Bitrix; apply uses V37 source-hash PG write-intent fence;
readback checks the exact Bitrix fields against verified PG write receipts.
No private text, INNs, URLs, customer names or webhook tokens printed.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import (
    ENABLED, TABLE, run_once, _get)
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import exact_environment,live_permitted,INTERVAL_SECONDS
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_enrichment_worker import Rest

FILES = (
    "serviceit_vps_worker.py",
    "serviceit_b2b_dossier_consistency_v37.py",
    "serviceit_b2b_dossier_consistency_worker_v37.py",
)
IDS = (11,13,15,17)


def out(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,70}",key)
    assert re.fullmatch(r"(?:YES|NO|[0-9]{1,5}|[A-Z0-9_]{3,70})",
                        str(value))
    print("V37_FOUR_"+key+"="+str(value),flush=True)


def readback(conn,rest,card):
    row=conn.execute(f"""
        SELECT status,after_fields FROM {TABLE}
        WHERE card_id=%s ORDER BY attempt_no DESC LIMIT 1
    """,(card,)).fetchone()
    if row is None:
        return "NO_RECEIPT",0
    status,patch=row
    if status!="VERIFIED" or not isinstance(patch,dict):
        return status if re.fullmatch(r"[A-Z0-9_]{3,70}",status) else "UNKNOWN",0
    item=_get(rest,card)
    return ("MATCH" if all(item.get(k)==v for k,v in patch.items())
            else "MISMATCH"),len(patch)


def main():
    if (len(sys.argv)!=len(FILES)+2
            or sys.argv[1] not in ("preview","apply","readback")):
        out("INVALID_ARGS","YES")
        return 2
    mode=sys.argv[1]
    expected=sys.argv[2:]
    if any(re.fullmatch(r"[a-f0-9]{64}",s) is None for s in expected):
        out("SOURCE_ATTESTATION_INVALID","YES")
        return 2
    sha=[hashlib.sha256((Path("/app/leadpilot")/f).read_bytes()).hexdigest()
         for f in FILES]
    if sha!=expected:
        out("RUNTIME_SHAS_MATCH","NO")
        return 2
    out("RUNTIME_SHAS_MATCH","YES")
    env=dict(os.environ)
    active=live_permitted(env) and INTERVAL_SECONDS==300
    exact=exact_environment(env)
    if not active or exact.get(ENABLED)!="YES":
        out("LIVE_CONSISTENCY_ENABLED","NO")
        return 2
    out("LIVE_CONSISTENCY_ENABLED","YES")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn,httpx.Client(
            trust_env=False,follow_redirects=False,timeout=25) as client:
        rest=Rest(webhook,client)
        good=0;writes=0
        for card in IDS:
            if mode=="readback":
                status,fields=readback(conn,rest,card)
                out("CARD_"+str(card)+"_STATUS",status)
                out("CARD_"+str(card)+"_FIELDS",fields)
                good+=status=="MATCH"
                continue
            result=run_once(exact,client,conn,target_ids={card},
                            preview=mode=="preview")
            status=result.get("status","UNKNOWN")
            if not re.fullmatch(r"[A-Z0-9_]{3,70}",status):
                status="INVALID"
            out("CARD_"+str(card)+"_STATUS",status)
            if mode=="preview":
                out("CARD_"+str(card)+"_DRAFT","YES" if result.get("draft_ready") else "NO")
                out("CARD_"+str(card)+"_STRUCTURED",result.get("structured_candidates",0))
                good+=status=="V37_READONLY_PREVIEW"
            else:
                writes+=int(result.get("crm_writes")==1)
                good+=status in (
                    "V37_CRM_RECONCILED_VERIFIED",
                    "V37_NO_ATTESTED_NEW_FACTS",
                )
        out("CARDS_CHECKED",len(IDS))
        out("SUCCESSFUL",good)
        out("CRM_WRITES",writes)
        out("COMPLETED","YES")
        return 0 if mode=="preview" and good==4 or (
            mode=="readback" and good==4) or (
            mode=="apply" and good==4) else 2


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_FAILURE","YES")
        raise SystemExit(2)
