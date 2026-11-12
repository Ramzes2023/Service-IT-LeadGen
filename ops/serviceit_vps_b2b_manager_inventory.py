"""Read-only inventory of real B2B card facts and manager schema."""
import hashlib
import os
from pathlib import Path

import httpx

from leadpilot.bitrix_1062_mapping import _is_valid_inn
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import _get
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, BADGE_FIELD, VERSION_FIELD
from leadpilot.serviceit_vps_pg_queue import db, secret_file

def emit(k,v):
    print(f"MANAGER_{k}={v}", flush=True)

def main():
    env = dict(os.environ)
    for name, key in (("serviceit_b2b_dossier_consistency_worker_v37.py","WORKER"),
                      ("serviceit_b2b_dossier_consistency_v37.py","FORM"),
                      ("serviceit_vps_b2b_dossier_v11_worker.py","V11")):
        p=Path("/app/leadpilot")/name
        emit("SHA_"+key, hashlib.sha256(p.read_bytes()).hexdigest().upper())
    with db(env) as conn, conn.transaction(), httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        fields=rest("crm.item.fields",{"entityTypeId":1062,"useOriginalUfNames":"Y"})["fields"]
        for label, key in (("SCORE",SCORE_FIELD),("BADGE",BADGE_FIELD),("VERSION",VERSION_FIELD),
            ("REVIEW_DECISION",PREFIX+"REVIEW_DECISION"),("AI_RECOMMENDATION",PREFIX+"AI_RECOMMENDATION"),
            ("BUYER",PREFIX+"COMPANY_NAME"),("INN",PREFIX+"COMPANY_INN"),("REGION",PREFIX+"REGION")):
            detail=fields.get(key,{})
            emit("FIELD_"+label, str(detail.get("type","MISSING")).upper())
        rows=conn.execute("""
            SELECT b.card_id,b.source_id,COALESCE(v.status,'NONE')
            FROM serviceit_vps_b2b_inbox b LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v
            ON v.card_id=b.card_id
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL ORDER BY b.card_id
        """).fetchall()
        emit("TOTAL",len(rows))
        for card, source, state in rows:
            emit(f"CARD_{card}_TENDER",source.split("-")[-1])
            emit(f"CARD_{card}_V11",state)
            try:
                item=_get(rest,card)
            except Exception:
                emit(f"CARD_{card}_CRM","READ_UNVERIFIED")
                continue
            values={
                "INN":_is_valid_inn(item.get(PREFIX+"COMPANY_INN")),
                "REGION":bool(item.get(PREFIX+"REGION")),
                "BRIEF":bool(item.get(PREFIX+"AI_MANAGER_BRIEF")),
                "REPORT":bool(item.get(PREFIX+"AI_FULL_REPORT")),
                "SCORE":item.get(SCORE_FIELD) not in (None,""),
                "VERSION":bool(item.get(VERSION_FIELD)),
                "BADGE":bool(item.get(BADGE_FIELD)),
                "DECISION":bool(item.get(PREFIX+"REVIEW_DECISION")),
            }
            for key,value in values.items():
                emit(f"CARD_{card}_{key}","YES" if value else "NO")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        emit("ERROR",type(e).__name__.upper())
        raise SystemExit(2) from None
