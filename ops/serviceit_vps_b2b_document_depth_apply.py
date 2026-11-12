"""Attach document evidence to the already existing and signed card 27."""
import os
import re

import httpx

from leadpilot.serviceit_b2b_dossier_consistency_v37 import BRIEF
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import _get, run_once as refresh_brief
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_vps_b2b_document_worker import run_once
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment

CARD = 27
SOURCE = "B2B-CENTER-4677160"


def emit(key,value):
    if re.fullmatch(r"[A-Z0-9_]+",str(value)):
        print(f"DOCS_{key}={value}",flush=True)


def main():
    env = exact_environment(dict(os.environ))
    with db(env) as conn, httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        row = conn.execute("""
            SELECT source_id FROM serviceit_vps_b2b_inbox
            WHERE card_id=%s AND source_id=%s AND state='VERIFIED'
        """,(CARD,SOURCE)).fetchone()
        if row is None:
            emit("APPLY","SIGNED_CARD_NOT_FOUND")
            return 2
        result = run_once(env,client,conn,target_card=CARD)
        for key in ("status","documents_readable","attachments_verified","eis_links"):
            if key in result:
                emit(key.upper(),result[key])
        receipt = conn.execute("""
            SELECT status,scanned_count FROM serviceit_vps_b2b_tender_doc_receipts
            WHERE card_id=%s
        """,(CARD,)).fetchone()
        if not receipt or receipt[0] != "VERIFIED":
            emit("READBACK","NOT_CONFIRMED")
            return 2
        emit("READBACK","VERIFIED")
        emit("FILES_IN_ANALYSIS",receipt[1])
        status = refresh_brief(env,client,conn,target_ids={CARD},refresh=True)
        emit("BRIEF_REFRESH",status["status"])
        rest = Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        item = _get(rest,CARD)
        ready = "прочитано файлов: "+str(receipt[1]) in item.get(BRIEF,"")
        emit("BRIEF_READBACK","YES" if ready else "NO")
        return 0 if ready else 2


if __name__=="__main__":
    raise SystemExit(main())
