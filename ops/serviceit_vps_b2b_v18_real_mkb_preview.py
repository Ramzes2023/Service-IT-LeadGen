#!/usr/bin/env python3
"""Read-only LIVE B2B #4675475 V18 pipeline preview, no RouterAI bill/CRM write.

Uses the exact stored B2B source and actual Bitrix card 17. Separately
classifies original-page proof vs Brave indexed claims. Never logs raw page
text, buyer, INN, URLs, token, email, private fields or exception text.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

FILES=(
    "serviceit_b2b_public_mirrors.py",
    "serviceit_b2b_tender_index_v18.py",
    "serviceit_vps_b2b_deep_research.py",
    "serviceit_b2b_dossier_v11.py",
    "serviceit_vps_worker.py",
    "serviceit_vps_b2b_dossier_v11_worker.py",
)
CARD=17
TENDER="4675475"


def say(label,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,62}",label)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{5,75})",str(value))
    print("V18_REAL_MKB_"+label+"="+str(value),flush=True)


def run():
    from leadpilot.serviceit_vps_pg_queue import db,secret_file
    from leadpilot.serviceit_vps_worker import (
        live_permitted, exact_environment, INTERVAL_SECONDS)
    from leadpilot.serviceit_vps_b2b_deep_research import (
        safe_notice, source_findings, public_buyer_candidate,
        published_buyer_inn_evidence,
    )
    from leadpilot.serviceit_b2b_routerai_deep import (
        public_documents,source_only_investigation)
    from leadpilot.serviceit_b2b_dossier_v11 import compose
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX

    if len(sys.argv)!=len(FILES)+1 or not all(re.fullmatch(
            r"[0-9a-f]{64}",x) for x in sys.argv[1:]):
        say("LIVE_SHA","NO")
        return 2
    sha_ok=all((Path("/app/leadpilot")/f).is_file() and hashlib.sha256(
        (Path("/app/leadpilot")/f).read_bytes()).hexdigest()==sha
        for f,sha in zip(FILES,sys.argv[1:]))
    say("LIVE_SHA","YES" if sha_ok else "NO")
    if not sha_ok:
        return 2
    env=dict(os.environ)
    gates=live_permitted(env) and INTERVAL_SECONDS==300
    say("FIVE_MINUTE_GATES","YES" if gates else "NO")
    if not gates:
        return 2
    exact=exact_environment(env)
    say("V18_ENABLED","YES" if exact.get(
        "SERVICEIT_B2B_V18_SEARCH_ENABLED")=="YES" else "NO")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,
                      timeout=20.0) as client:
        rest=Rest(webhook,client)
        with db(env) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(
                        "SELECT source_id,notice_json,state FROM "
                        "serviceit_vps_b2b_inbox WHERE card_id=%s",
                        (CARD,))
                    rows=cur.fetchall()
        say("SIGNED_SOURCE_EXISTS","YES" if len(rows)==1 else "NO")
        if len(rows)!=1:
            return 2
        source,notice,state=rows[0]
        say("SOURCE_VERIFIED","YES" if state=="VERIFIED" else "NO")
        say("EXACT_SOURCE_NUMBER","YES" if source=="B2B-CENTER-"+TENDER else "NO")
        if state!="VERIFIED" or source!="B2B-CENTER-"+TENDER:
            return 2
        item=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y"})["item"])
        validate_identity(item,CARD)
        origin,title=safe_notice(notice,CARD,source,item)
        say("CRM_IDENTITY","YES" if TENDER in origin and bool(title) else "NO")
        if not TENDER in origin:
            return 2

        # Only read-only searches. RouterAI is intentionally not called; the
        # full 9-field proposed draft is computed in memory for diagnostics.
        findings=source_findings(item,notice,exact,client,enable_v18=True)
        idx=findings.get("indexed_candidates") or []
        docs=public_documents(findings)
        report=source_only_investigation(findings)
        proposed, coverage=compose(notice,item,findings,report)
        summary=proposed[PREFIX+"AI_SUMMARY"]
        brief=proposed[PREFIX+"AI_MANAGER_BRIEF"]
        full=proposed[PREFIX+"AI_FULL_REPORT"]
        labels={x.get("category") for hint in idx if isinstance(hint,dict)
                for x in hint.get("claims",[]) if isinstance(x,dict)}
        say("V18_INDEX_STATUS",findings.get("v18_search_status","INDEX_UNKNOWN"))
        say("INDEX_CANDIDATES",min(len(idx),99))
        say("INDEX_BUYER_LABEL","YES" if "BUYER" in labels else "NO")
        say("INDEX_INN_LABEL","YES" if "INN" in labels else "NO")
        say("INDEX_DEADLINE_LABEL","YES" if "DEADLINE" in labels else "NO")
        say("INDEX_LOCATION_LABEL","YES" if "LOCATION" in labels else "NO")
        say("INDEX_BUDGET_LABEL","YES" if "BUDGET" in labels else "NO")
        say("INDEPENDENT_PUBLIC_PAGES",min(len(docs),99))
        say("QUOTED_PUBLIC_FACTS",min(len(report.get("facts") or []),99))
        say("BUYER_FROM_PUBLIC_PAGE","YES" if public_buyer_candidate(findings) else "NO")
        say("INN_FROM_PUBLIC_PAGE","YES" if published_buyer_inn_evidence(findings) else "NO")
        say("INDEX_INCLUDED_IN_DRAFT","YES" if "ИНДЕКС BRAVE" in brief else "NO")
        say("NINE_FIELD_PREVIEW","YES" if len(proposed)==9 else "NO")
        say("OLD_REPORT_PRESERVED","YES" if item.get(
            PREFIX+"AI_FULL_REPORT") and proposed.get(PREFIX+"AI_FULL_REPORT")
            else "NO")
        say("CRM_WRITES",0)
        return 0


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("READONLY_FAILURE","YES")
        raise SystemExit(2)
