#!/usr/bin/env python3
"""No-write MKB #4675475 CRM #17 receipt and snapshot audit after one-use attempt.

Never retries or repairs a WRITE_INTENT. Prints only fixed statuses, no CRM text.
"""
import hashlib
import os
from pathlib import Path
import re

import httpx

EXPECTED_V16_SHA="126ce5d0f76544e5edc17e1c4a557258a069dbfd2558e5297fb39bb3012d05f5"
TABLE="serviceit_vps_b2b_mkb_4675475_manual_receipts"


def say(key,value):
    assert re.fullmatch(r"[A-Z0-9_]{3,56}",key)
    assert value in ("YES","NO","ERROR","NONE","RESERVED","WRITE_INTENT",
                     "VERIFIED","CONFLICT","UNCERTAIN")
    print("MKB_AUDIT_"+key+"="+value,flush=True)


def run():
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_b2b_dossier_v11 import original_nine
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
    from leadpilot.serviceit_vps_worker import live_permitted

    live=Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    safe=(live.is_file() and hashlib.sha256(live.read_bytes()).hexdigest()
          == EXPECTED_V16_SHA)
    say("LIVE_SHA", "YES" if safe else "NO")
    if not safe:
        return 2
    env=dict(os.environ)
    ok=live_permitted(env)
    say("LIVE_GATES","YES" if ok else "NO")
    if not ok:
        return 2

    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT to_regclass(%s)",("public."+TABLE,))
                exists=cur.fetchone()[0] is not None
                say("PG_RECEIPT_TABLE","YES" if exists else "NO")
                status="NONE"
                if exists:
                    cur.execute(f"SELECT status FROM {TABLE} WHERE card_id=17")
                    row=cur.fetchone()
                    status=str(row[0]) if row else "NONE"
                say("PG_RECEIPT_STATUS",status)

    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        rest=Rest(webhook,client)
        item=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":17,"useOriginalUfNames":"Y"
        })["item"])
        validate_identity(item,17)
        say("CARD_EXISTS","YES")
        source=str(item.get(PREFIX+"SIGNAL_URL") or "")
        source_ok=("b2b-center.ru" in source and "4675475" in source)
        say("ORIGINAL_LINK_MATCH","YES" if source_ok else "NO")
        base=original_nine(item)
        report=base[PREFIX+"AI_FULL_REPORT"]
        brief=base[PREFIX+"AI_MANAGER_BRIEF"]
        summary=base[PREFIX+"AI_SUMMARY"]
        id_ok="4675475" in report
        say("REPORT_TENDER_ID","YES" if id_ok else "NO")
        marker="12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475"
        present=marker in report
        say("FACTUAL_APPEND_PRESENT","YES" if present else "NO")
        say("SUMMARY_BUYER_PRESENT", "YES" if "7734202860" in summary else "NO")
        say("MANAGER_BRIEF_BUYER_PRESENT", "YES" if "7734202860" in brief else "NO")
        say("NINE_COMPLETE", "YES" if len(base)==9 and all(base.values()) else "NO")
        return 0


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("READBACK_STATUS","ERROR")
        raise SystemExit(2)
