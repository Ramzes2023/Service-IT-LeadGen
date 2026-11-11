#!/usr/bin/env python3
"""Independent zero-write V19 check: real Bitrix 1062/21 and durable receipt."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys
import httpx

TABLE="serviceit_vps_b2b_v19_research_receipts"

def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,60}",k)
    assert re.fullmatch(r"(YES|NO|NONE|VERIFIED|WRITE_INTENT|UNCERTAIN|CONFLICT|NO_NEW_EVIDENCE|INSUFFICIENT_CAPACITY|RESERVED|[0-9]{1,3})",str(v))
    print("V19_POSTREAD_"+k+"="+str(v),flush=True)


def run():
    from leadpilot.serviceit_vps_pg_queue import db,secret_file
    from leadpilot.serviceit_vps_worker import live_permitted
    from leadpilot.serviceit_b2b_dossier_v11 import original_nine
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
    from leadpilot.bitrix_metadata_audit import validate_webhook

    if len(sys.argv)!=2 or re.fullmatch(r"[a-f0-9]{64}",sys.argv[1]) is None:
        say("SOURCE_SHA_ARG","NO")
        return 2
    p=Path("/app/leadpilot/serviceit_b2b_v19_backfill.py")
    sha_ok=p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[1]
    env=dict(os.environ)
    say("EXACT_SOURCE_SHA","YES" if sha_ok else "NO")
    say("LIVE_GATES","YES" if live_permitted(env) else "NO")
    if not sha_ok or not live_permitted(env):
        return 2

    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT to_regclass(%s)",("public."+TABLE,))
                exists=cur.fetchone()[0] is not None
                say("RECEIPT_TABLE","YES" if exists else "NO")
                if not exists:
                    return 2
                cur.execute(f"""
                    SELECT card_id,source_hash,status,before_fields,
                           after_fields,salt,digest
                    FROM {TABLE}
                    WHERE status='VERIFIED'
                    ORDER BY updated_at DESC LIMIT 1
                """)
                row=cur.fetchone()
                say("VERIFIED_RECEIPT","YES" if row else "NO")
                if row is None:
                    return 2
                card,hash_,status,before,after,salt,digest=row
                cur.execute("""
                    SELECT b.source_id,b.state,b.notice_json
                    FROM serviceit_vps_b2b_inbox b WHERE b.card_id=%s
                """,(card,))
                origin=cur.fetchone()
    if not origin or origin[1]!="VERIFIED" or not origin[0].startswith("B2B-CENTER-"):
        say("SIGNED_ORIGIN","NO")
        return 2
    signed_id=origin[0]
    expect=hashlib.sha256(signed_id.encode("ascii")).hexdigest()
    say("SIGNED_ORIGIN","YES" if expect==hash_ else "NO")
    if expect!=hash_:
        return 2

    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        item=normalized(Rest(webhook,client)("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(item,int(card))
    nine=original_nine(item)
    summary=nine[PREFIX+"AI_SUMMARY"]
    brief=nine[PREFIX+"AI_MANAGER_BRIEF"]
    full=nine[PREFIX+"AI_FULL_REPORT"]
    original_ok=(item.get(PREFIX+"SIGNAL_ID")==signed_id
                 and item.get(PREFIX+"SIGNAL_URL")==origin[2].get("source_url"))
    say("EXACT_BITRIX_SOURCE","YES" if original_ok else "NO")
    nine_ok=nine==after and type(before) is dict and set(before)==AI_FIELDS
    say("EXACT_NINE_READBACK","YES" if nine_ok else "NO")
    digest_ok=(salt is not None and digest is not None
               and compute_digest(nine,bytes(salt))==bytes(digest))
    say("DURABLE_PG_DIGEST","YES" if digest_ok else "NO")
    changed={k for k in AI_FIELDS if nine[k]!=before.get(k)}
    expected={PREFIX+"AI_SUMMARY",PREFIX+"AI_MANAGER_BRIEF",
              PREFIX+"AI_FULL_REPORT"}
    say("ONLY_THREE_NARRATIVES_CHANGED","YES" if changed==expected else "NO")
    say("OLD_REPORT_UNCHANGED","YES" if full.startswith(before.get(PREFIX+"AI_FULL_REPORT","")) else "NO")
    say("INDEX_WARNING_PRESENT","YES" if
        "Индексные выписки требуют проверки" in full or
        "Прочитанные страницы и находки Brave" in full else "NO")
    say("MANAGER_DELTA_PRESENT","YES" if
        "ОБНОВЛЕНИЕ ПО ТЕНДЕРУ" in brief and
        "ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ" in summary else "NO")
    accepted=all((nine_ok,digest_ok,changed==expected,original_ok,
                  full.startswith(before[PREFIX+"AI_FULL_REPORT"]),
                  "ОБНОВЛЕНИЕ ПО ТЕНДЕРУ" in brief))
    say("INDEPENDENT_ACCEPTANCE","YES" if accepted else "NO")
    return 0 if accepted else 2

if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("ERROR_SUPPRESSED","YES")
        raise SystemExit(2)
