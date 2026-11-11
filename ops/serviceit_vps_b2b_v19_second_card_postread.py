#!/usr/bin/env python3
"""Independently verify V19's existing second signed card in LIVE Bitrix.

Read PG receipt + live CRM item, verify exact nine AI fields with salted
digest, old report prefix, three narrative-field-only append and source URL.
No CRM writes, PG writes, search APIs, RouterAI or private data in logs.
"""
from __future__ import annotations

import os
import secrets
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_b2b_v19_backfill import select_one,TABLE,REPORT,SUMMARY,BRIEF
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
from leadpilot.serviceit_vps_worker import live_permitted
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
import httpx

def say(label, yes):
    print("V19_CARD2_POSTREAD_"+label+"="+("YES" if yes else "NO"),flush=True)


def run():
    env=dict(os.environ)
    say("PERMANENT_V19_AUTOWRITE",env.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES")
    if not live_permitted(env):
        say("GATES",False)
        return 2
    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                four=select_one(conn,preview=True)
                say("ORIGINAL_FOUR",len(four)==4)
                if len(four)!=4:
                    return 2
                card,source,notice=four[1]
                cur.execute("SELECT status,before_fields,after_fields,salt,digest "
                            "FROM "+TABLE+" WHERE card_id=%s",(card,))
                row=cur.fetchone()
                say("RECEIPT_EXISTS",row is not None)
                if row is None:
                    return 2
                status,before,expected,salt,digest=row
                say("RECEIPT_VERIFIED",status=="VERIFIED")
                if status!="VERIFIED" or type(before) is not dict or type(expected) is not dict:
                    return 2
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        item=normalized(Rest(webhook,client)("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
        validate_identity(item,card)
        signal=str(item.get(PREFIX+"SIGNAL_ID") or "")
        orig=str(item.get(PREFIX+"SIGNAL_URL") or "")
        identity=(signal==source and orig==notice["source_url"]
                  and notice.get("tender_id") in signal)
        say("ORIGINAL_TENDER_IDENTITY",identity)
        observed=original_nine(item)
        exact=(observed==expected and
               salt is not None and digest is not None
               and secrets.compare_digest(
                   compute_digest(observed,bytes(salt)),bytes(digest)))
        say("NINE_FIELDS_DIGEST_READBACK",exact)
        preserved=(observed[REPORT].startswith(before[REPORT])
                   and observed[SUMMARY].endswith(before[SUMMARY])
                   and observed[BRIEF].endswith(before[BRIEF]))
        say("ORIGINAL_TEXT_PRESERVED",preserved)
        diff={k for k in AI_FIELDS if expected[k]!=before[k]}
        only_three=(diff=={REPORT,SUMMARY,BRIEF}
                    and observed[PREFIX+"AI_STATUS"]==before[PREFIX+"AI_STATUS"])
        say("EXACT_THREE_NARRATIVE_FIELDS",only_three)
        additive=("09  ДОПОЛНЕНИЕ V19" in observed[REPORT]
                  and ("ИНДЕКС BRAVE" in observed[REPORT] or
                       "ПРОЧИТАННЫЙ ИСТОЧНИК" in observed[REPORT]))
        say("V19_SOURCED_ADDENDUM_PRESENT",additive)
        accuracy=("требуют проверки" in observed[REPORT]
                  and "не являются ФНС" in observed[REPORT])
        say("INDEX_AND_FNS_CAVEATS",accuracy)
        good=all((identity,exact,preserved,only_three,additive,accuracy))
        say("INDEPENDENT_ACCEPTANCE",good)
        say("CRM_WRITES_ZERO",True)
        return 0 if good else 2

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("AUDIT_ABORT",True)
        raise SystemExit(2)
