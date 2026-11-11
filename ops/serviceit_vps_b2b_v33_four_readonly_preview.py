#!/usr/bin/env python3
"""V33 nine-module post-deploy read-only audit of four real Bitrix 1062/21 cards, no changes or model calls.

The preview reads the signed PostgreSQL source, uses the deployed Brave LLM
Context and currently readable public pages, makes a nine-AI-field ADDITIVE
proposal in memory. Output is bounded counts only, not private data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

FILES=(
    "serviceit_b2b_tender_index_v18.py",
    "serviceit_b2b_dossier_v11.py",
    "serviceit_vps_worker.py",
    "serviceit_b2b_v19_backfill.py",
    "serviceit_vps_b2b_deep_research.py",
    "serviceit_b2b_free_public_fallback.py",
    "serviceit_b2b_remote_public_fetch.py",
    "serviceit_b2b_routerai_deep.py",
    "serviceit_vps_b2b_document_worker.py",
)

def out(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,62}",key)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,75})",str(value))
    print("V33_REAL_FOUR_"+key+"="+str(value),flush=True)

def run():
    from leadpilot.serviceit_vps_pg_queue import db
    from leadpilot.serviceit_vps_worker import (
        live_permitted,INTERVAL_SECONDS,exact_environment)
    from leadpilot import serviceit_b2b_v19_backfill as v19
    # Intercept source-return status only inside this read-only subprocess.
    # Do not change the coordinator worker or any PostgreSQL receipts.
    stages = []
    source_findings = v19.source_findings
    def observe_stage(*args, **kwargs):
        try:
            findings = source_findings(*args, **kwargs)
        except Exception:
            stages.append("ERROR")
            raise
        stages.append(findings.get(
            "tinyfish_independent_stage", "NOT_REPORTED"))
        return findings
    v19.source_findings = observe_stage

    if len(sys.argv)!=len(FILES)+1 or any(
            re.fullmatch(r"[a-f0-9]{64}",x) is None for x in sys.argv[1:]):
        out("SHA_ARGUMENT","NO")
        return 2
    match=all((Path("/app/leadpilot")/x).is_file() and hashlib.sha256(
        (Path("/app/leadpilot")/x).read_bytes()).hexdigest()==sha
        for x,sha in zip(FILES,sys.argv[1:]))
    out("EXACT_NINE_MODULE_SHAS","YES" if match else "NO")
    env=dict(os.environ)
    out("FIVE_MINUTE_LIVE","YES" if live_permitted(env)
        and INTERVAL_SECONDS==300 else "NO")
    out("V19_AUTOWRITE_ENABLED","YES" if env.get(
        "SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES" else "NO")
    flags = (
        env.get("SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED") == "YES"
        and env.get("SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED") == "YES"
        and env.get("SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED") == "NO"
    )
    out("V33_PUBLIC_ONLY_FLAGS","YES" if flags else "NO")
    if not match or not flags or not live_permitted(env) or INTERVAL_SECONDS!=300:
        return 2
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=20.0) as client:
        with db(env) as conn:
            data=v19.run_once(env,client,conn,preview=True)
    out("PREVIEW_STATUS",data.get("status","UNKNOWN"))
    out("PREVIEWED",min(int(data.get("previewed",0)),99))
    results=data.get("results") or []
    for i,row in enumerate(results[:4],1):
        prefix="CARD_"+str(i)+"_"
        out(prefix+"STATUS",row.get("status","UNKNOWN"))
        out(prefix+"CITED_NOVEL",min(int(row.get("new_cited_lines",0)),99))
        out(prefix+"ADDENDUM_READY","YES" if row.get("new_3_field_draft") else "NO")
        out(prefix+"PUBLIC_PAGES",min(int(row.get("verified_public_pages",0)),99))
    out("TINYFISH_STAGE_CALLS",len(stages))
    out("TINYFISH_STAGE_VERIFIED",sum(x=="VERIFIED" for x in stages))
    out("TINYFISH_STAGE_RETRYABLE",sum(
        x=="NO_VERIFIED_PUBLIC_PAGE_RETRYABLE" for x in stages))
    out("TINYFISH_STAGE_DISABLED",sum(
        x=="DISABLED_BY_SOURCE_POLICY" for x in stages))
    out("TINYFISH_STAGE_ERROR",sum(
        x in ("ERROR","NOT_REPORTED") for x in stages))
    out("CRM_WRITES",0)
    return 0 if (data.get("status")=="V19_FOUR_READONLY_PREVIEW"
                 and len(results)==4 and len(stages)==4
                 and all(x in ("VERIFIED","NO_VERIFIED_PUBLIC_PAGE_RETRYABLE")
                         for x in stages)) else 2

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        out("SAFE_READONLY_FAILURE","YES")
        raise SystemExit(2)
