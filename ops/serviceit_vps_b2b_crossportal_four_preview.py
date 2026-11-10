#!/usr/bin/env python3
"""No-write real 4-card B2B public cross-portal evidence coverage audit.

Source URLs and titles come ONLY from four VERIFIED HMAC-ingested original
notices in PostgreSQL. Independently fetch public sources/official buyer
pages under robots, DNS, HTTPS, bounded Brave policy. No RouterAI call, CRM
update, PG receipt, document download, emails or model data leakage.
Output fixed ordinal coverage only, never IDs/buyer/email/phone/URLs.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import sys

import httpx


def say(name, value):
    if not re.fullmatch(r"[A-Z0-9_]{3,64}", name):
        raise ValueError("UNSAFE_OUTPUT_KEY")
    v = str(value)
    if not re.fullmatch(r"(YES|NO|ERROR|[0-9]{1,4})", v):
        raise ValueError("UNSAFE_OUTPUT_VALUE")
    print("CROSSPORTAL_FOUR_" + name + "=" + v, flush=True)


def assess(env, client):
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_worker import live_permitted, exact_environment
    from leadpilot.serviceit_vps_b2b_deep_research import (
        safe_notice, source_findings, public_buyer_candidate,
        published_buyer_inn_evidence,
    )
    from leadpilot.serviceit_b2b_routerai_deep import (
        public_documents, source_only_investigation,
    )
    from leadpilot.serviceit_b2b_dossier_v11 import (
        checked_public_contacts, evidence_rows,
    )
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    from leadpilot.serviceit_enrichment_worker import (
        Rest, normalized, validate_identity,
    )
    from leadpilot.bitrix_metadata_audit import validate_webhook

    eligible = (
        live_permitted(env) and
        env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES" and
        env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED") == "YES" and
        env.get("SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED") == "YES" and
        env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"
    )
    say("LIVE_GATES", "YES" if eligible else "NO")
    if not eligible:
        return False
    exact = exact_environment(env)
    say("BRAVE_KEY_READY", "YES" if bool(exact.get("SERVICEIT_BRAVE_SEARCH_API_KEY")) else "NO")
    if not exact.get("SERVICEIT_BRAVE_SEARCH_API_KEY"):
        return False
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    rest = Rest(webhook, client)

    with db(env) as connection:
        with connection.transaction():
            with connection.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                  SELECT b.card_id,b.source_id,b.notice_json
                  FROM serviceit_vps_b2b_inbox b
                  JOIN serviceit_vps_b2b_dossier_v11_receipts v
                    ON v.card_id=b.card_id AND v.status='VERIFIED'
                  WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
                  ORDER BY b.received_at,b.source_id
                """)
                records = cur.fetchall()
                cur.execute("""
                  SELECT count(*) FROM serviceit_vps_b2b_dossier_v11_receipts
                  WHERE status IN ('RESERVED','WRITE_INTENT')
                """)
                concurrent = int(cur.fetchone()[0])
    say("SIGNED_MATCHING_CARDS", min(len(records), 100))
    say("V11_WRITES_INFLIGHT", min(concurrent, 100))
    if len(records) != 4 or concurrent != 0:
        return False

    all_good = True
    for ordinal, row in enumerate(records, 1):
        card, source_id, notice = row
        prefix = "CARD_" + str(ordinal) + "_"
        try:
            item = normalized(rest("crm.item.get", {
                "entityTypeId":1062, "id":card, "useOriginalUfNames":"Y",
            })["item"])
            validate_identity(item, card)
            safe_notice(notice, card, source_id, item)
            ai_ok = all(isinstance(item.get(k), str) and
                        item[k].strip() for k in AI_FIELDS)
            if not ai_ok:
                raise ValueError("BASE_AI_FIELDS_UNVERIFIED")

            findings = source_findings(item, notice, exact, client)
            docs = public_documents(findings)
            report = source_only_investigation(findings)
            _, facts = evidence_rows(findings, report)
            buyer = public_buyer_candidate(findings)
            inn = published_buyer_inn_evidence(findings) if buyer else None
            contacts = checked_public_contacts(findings,buyer,inn)
            categories = {fact.get("category") for fact in facts}
            categories.update(fact.get("category") for fact in
                              (report.get("facts") or [])
                              if type(fact) is dict)
            same_company = (isinstance(findings.get("independent_company_research"),dict)
                and isinstance(findings["independent_company_research"].get("public_inn"),dict)
                and inn is not None
                and findings["independent_company_research"]["public_inn"].get("inn") == inn.get("inn"))
            say(prefix+"PUBLIC_PAGES", len(docs))
            say(prefix+"CITED_FACTS", len(facts))
            say(prefix+"BUYER", "YES" if buyer else "NO")
            say(prefix+"BUYER_INN", "YES" if inn else "NO")
            say(prefix+"OFFICIAL_COMPANY_ID", "YES" if same_company else "NO")
            say(prefix+"CONTACTS", len(contacts))
            for cat in ("DEADLINE","BUDGET","ELIGIBILITY","PAYMENT",
                        "EVALUATION","LOCATION","DOCUMENTS","TECHNOLOGY"):
                say(prefix+cat, "YES" if cat in categories else "NO")
            say(prefix+"READY_TO_APPEND", "YES" if
                (docs and (facts or inn or contacts)) else "NO")
        except Exception:
            all_good = False
            say(prefix+"SOURCE_CHECK", "ERROR")
            # Never print raw exception (possibly vendor/URL/secret).
            continue
        say(prefix+"SOURCE_CHECK", "YES")
    return all_good


def main():
    env = dict(os.environ)
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
            ok = assess(env,client)
        say("READONLY_FINISHED", "YES" if ok else "NO")
        return 0 if ok else 2
    except Exception:
        say("READONLY_FINISHED","NO")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
