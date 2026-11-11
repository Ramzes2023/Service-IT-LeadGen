#!/usr/bin/env python3
"""V22 on four original signed B2B cards: real read-only content coverage.

No paid Brave/RouterAI calls, no browser or login, no CRM/PG writes.
Uses original signed PG B2B notices and Bitrix read-only nine-field snapshots;
fetches the B2B source through HTTPS/DNS/robots gate and exact tender ID.
Only counts new source-backed lines, never prints URLs or customer content.
"""
from __future__ import annotations
import hashlib,os,re,sys
from pathlib import Path

import httpx

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one,novel_evidence,prepare_addendum,REPORT
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice,host_allowlist
from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.serviceit_public_evidence_research import research_public

FILES=("serviceit_b2b_tender_index_v18.py","serviceit_b2b_v19_backfill.py",
       "serviceit_b2b_routerai_deep.py")

def say(name,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,80}",name)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,90})",str(value))
    print("V22_FOUR_"+name+"="+str(value),flush=True)


def inspect_card(client,rest,hosts,card,source,notice):
    first=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(first,card)
    url,title=safe_notice(notice,card,source,first)
    assert title and url==notice["source_url"]
    before=original_nine(first)
    findings=research_public(
        first,client,hosts,deep_search_enabled=False,
        deep_llm_context_enabled=False,browser_render=None,
        detail_excerpt_chars=4800)
    pages=findings.get("pages") or []
    exact=re.compile(r"(?<!\d)"+re.escape(notice["tender_id"])+r"(?!\d)")
    if findings.get("status")!="PUBLIC_PAGES_INSPECTED" or not any(
        isinstance(p,dict) and exact.search(p.get("excerpt","")) for p in pages):
        return dict(status="PUBLIC_ID_NOT_READABLE",facts=0,new=0,
                    draft=False,pko=False,deadline=False,budget=False,
                    payment=False,geo=False,eligibility=False,docs=False,gate=False)
    analysis=source_only_investigation(findings)
    facts=analysis.get("facts") or []
    lines=novel_evidence(findings,before[REPORT])
    draft=prepare_addendum(before,lines,notice["tender_id"])
    cats={x["category"] for x in facts if isinstance(x,dict)
          and isinstance(x.get("category"),str)}
    quotes=" ".join(x.get("quote","") for x in facts if isinstance(x,dict))
    return dict(status="PRIMARY_SOURCE_READ",facts=min(99,len(facts)),
                new=min(99,len(lines)),draft=bool(draft),
                pko="предварительный квалификационный отбор" in quotes.casefold(),
                deadline="DEADLINE" in cats,budget="BUDGET" in cats,
                payment="PAYMENT" in cats,geo="LOCATION" in cats,
                eligibility="ELIGIBILITY" in cats,
                docs="DOCUMENTS" in cats,
                gate="доступно зарегистрированным" in quotes.casefold())


def main():
    assert len(sys.argv)==1+len(FILES)
    assert all(re.fullmatch(r"[a-f0-9]{64}",s) for s in sys.argv[1:])
    hashes=[hashlib.sha256((Path("/app/leadpilot")/file).read_bytes()).hexdigest()
            for file in FILES]
    assert hashes==sys.argv[1:]
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    say("SOURCE_SHA_AND_LIVE_GATES","YES")
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          records=select_one(conn,preview=True)
          assert len(records)==4
          say("SIGNED_FOUR","YES")
          with httpx.Client(trust_env=False,follow_redirects=False) as client:
            rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
            hosts=host_allowlist(env)
            for i,(card,source,notice) in enumerate(records,1):
                try:
                    result=inspect_card(client,rest,hosts,card,source,notice)
                except Exception:
                    result=dict(status="SOURCE_OR_CARD_UNVERIFIED",facts=0,
                                new=0,draft=False,pko=False,deadline=False,
                                budget=False,payment=False,geo=False,
                                eligibility=False,docs=False,gate=False)
                say(f"CARD_{i}_STATUS",result["status"])
                for key in ("facts","new"):
                    say(f"CARD_{i}_"+key.upper(),result[key])
                for key in ("draft","pko","deadline","budget","payment","geo",
                            "eligibility","docs","gate"):
                    say(f"CARD_{i}_"+key.upper(),"YES" if result[key] else "NO")
    say("CRM_WRITES","NO")
    say("PG_WRITES","NO")
    say("BRAVE_ROUTERAI_CALLS","NO")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception:
        say("AUDIT_ABORT","YES")
        raise SystemExit(2)
