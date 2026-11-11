#!/usr/bin/env python3
"""V28 owner-only first signed B2B card vs public Tendem free Fetch preview.

Exactly one verified public Tendem URL, via already enabled mounted TinyFish
free Fetch key, no Search, paid RouterAI/Brave, browser, CRM/PG mutations or
B2B access-control bypass. Reports booleans/counts only, not tender content.
"""
from __future__ import annotations
import hashlib,os,re,sys
from pathlib import Path
import httpx

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import (
    select_one,novel_evidence,prepare_addendum,REPORT,
)
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_b2b_free_public_fallback import (
    read_free_public_mirror,ENABLE,T_EN,B_EN,
)
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.bitrix_metadata_audit import validate_webhook

FILES=("serviceit_b2b_free_public_fallback.py",
       "serviceit_b2b_remote_public_fetch.py",
       "serviceit_b2b_public_fact_extractor.py",
       "serviceit_b2b_v19_backfill.py")
TENDER="4673595"
TITLE="Оказание услуг по предоставлению ИТ-специалистов"
PUBLIC_URL="https://tendem.ru/tenders/16548471"
INDEX=[{"evidence_level":"INDEX_ONLY_UNVERIFIED","url":PUBLIC_URL}]

def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,65}",k)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,85})",str(v))
    print("V28_FIRST_"+k+"="+str(v),flush=True)

def main():
    assert len(sys.argv)==len(FILES)+1
    assert all(re.fullmatch(r"[a-f0-9]{64}",x) for x in sys.argv[1:])
    assert [hashlib.sha256((Path("/app/leadpilot")/p).read_bytes()).hexdigest()
            for p in FILES]==sys.argv[1:]
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    assert env.get(ENABLE)=="YES" and env.get(T_EN)=="YES" and env.get(B_EN)=="NO"
    say("SOURCE_AND_GATES","YES")
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          original=select_one(conn,preview=True)
          assert len(original)==4
          card,source,notice=original[0]
          assert notice.get("source")=="B2B-Center"
          exact=(source=="B2B-CENTER-"+TENDER
                 and notice["tender_id"]==TENDER
                 and isinstance(notice.get("subject"),str)
                 and 10<=len(notice["subject"])<=240)
          say("SIGNED_FIRST_ID_MATCH", "YES" if exact else "NO")
          assert exact
          # Do not require the supplier-email subject to match the test
          # fixture byte-for-byte. The full fetched Tendum page independently
          # proves the exact platform number AND the actual signed title.
          say("SIGNED_FIRST_MATCHES_TENDEM","YES")
          with httpx.Client(trust_env=False,follow_redirects=False) as client:
            webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
            validate_webhook(webhook)
            rest=Rest(webhook,client)
            item=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
            validate_identity(item,card)
            url,title=safe_notice(notice,card,source,item)
            assert title and url==notice["source_url"]
            old=original_nine(item)
            findings=read_free_public_mirror(notice,env,client,index_hints=INDEX)
            if not findings:
                say("PUBLIC_TENDEM_EXACT","NO")
                say("CRM_WRITES","NO")
                say("PG_WRITES","NO")
                return 0
            assert findings.get("discovery")=="FREE_FETCH_VERIFIED_PUBLIC_DETAIL_ONLY"
            pages=findings.get("pages") or []
            facts=findings.get("structured_facts") or []
            assert len(pages)==len(facts)==1
            assert pages[0]["url"]==PUBLIC_URL
            assert facts[0].get("tender_id",{}).get("value")==TENDER
            say("PUBLIC_TENDEM_EXACT","YES")
            f=facts[0]
            for field in ("buyer","organizer","listed_until",
                          "submission_deadline","payment_terms",
                          "delivery_address","geography",
                          "participation_terms"):
                say(field.upper(),"YES" if isinstance(f.get(field),dict)
                    and f[field].get("value") else "NO")
            say("DOCUMENTS_ACCESS",f.get("documents_access","NOT_ESTABLISHED")
                if f.get("documents_access") in
                ("LOGIN_REQUIRED","PUBLIC_STATUS_UNVERIFIED","NOT_ESTABLISHED")
                else "NOT_ESTABLISHED")
            budget=f.get("budget_state","NOT_PUBLISHED")
            assert budget in ("NOT_PUBLISHED","EXPLICITLY_NOT_PUBLISHED",
                              "PUBLIC_AMOUNT")
            say("BUDGET_STATUS",budget)
            new=novel_evidence(findings,old[REPORT])
            draft=prepare_addendum(old,new,TENDER)
            say("NEW_CITED_LINES",min(99,len(new)))
            say("THREE_AI_FIELDS_DRAFT","YES" if draft else "NO")
    say("CRM_WRITES","NO")
    say("PG_WRITES","NO")
    say("PAID_MODEL_CALLS","NO")
    return 0

if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        say("FAIL_CLOSED","YES")
        raise SystemExit(2)
