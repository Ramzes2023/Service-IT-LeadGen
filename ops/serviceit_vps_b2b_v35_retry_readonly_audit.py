#!/usr/bin/env python3
"""Read-only proof that V35 independent TinyFish retries are live and durable.

No network calls, no model, no Bitrix writes, no PG writes. Never print
customer fields, secrets, source URLs, tender IDs or procurement quotations.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys

NAMES=("serviceit_vps_worker.py","serviceit_vps_b2b_tinyfish_retry.py")
STATES=("RESERVED","VERIFIED_PUBLIC_PAGE","NO_VERIFIED_PUBLIC_PAGE",
        "PROVIDER_EXCEPTION","IDENTITY_CONFLICT","NONE")


def out(name, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,60}", name)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,5}|[A-Z0-9_]{4,60})", str(value))
    print("V35_RETRY_AUDIT_"+name+"="+str(value), flush=True)


def main():
    from leadpilot.serviceit_vps_worker import (
        live_permitted, exact_environment, INTERVAL_SECONDS)
    from leadpilot.serviceit_vps_pg_queue import db
    if len(sys.argv) != len(NAMES)+1 or any(
            re.fullmatch(r"[0-9a-f]{64}", x) is None
            for x in sys.argv[1:]):
        out("SHA_ARGUMENTS", "NO")
        return 2
    exact = all(
        (Path("/app/leadpilot")/n).is_file()
        and hashlib.sha256((Path("/app/leadpilot")/n).read_bytes()).hexdigest()==sha
        for n,sha in zip(NAMES,sys.argv[1:]))
    out("EXACT_MODULE_SHAS","YES" if exact else "NO")
    env=dict(os.environ)
    gates = (live_permitted(env)
             and INTERVAL_SECONDS == 300
             and exact_environment(env).get(
                 "SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED") == "YES")
    out("FIVE_MINUTE_RETRY_ENABLED","YES" if gates else "NO")
    if not exact or not gates:
        return 2
    with db(env) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)",(
                "public.serviceit_vps_b2b_tinyfish_retry_attempts",))
            ledger=cur.fetchone()[0] is not None
            out("LEDGER_EXISTS","YES" if ledger else "NO")
            cur.execute("""
                SELECT b.card_id
                FROM serviceit_vps_b2b_inbox b
                JOIN serviceit_vps_b2b_dossier_v11_receipts v
                  ON v.card_id=b.card_id AND v.status='VERIFIED'
                WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
                ORDER BY b.received_at,b.source_id LIMIT 4
            """)
            cards=[r[0] for r in cur.fetchall()]
            out("SIGNED_FOUR",len(cards))
            if ledger:
                cur.execute("""
                    SELECT card_id,status
                    FROM (
                      SELECT card_id,status,
                       ROW_NUMBER() OVER (
                         PARTITION BY card_id ORDER BY attempt_no DESC) rn
                      FROM serviceit_vps_b2b_tinyfish_retry_attempts
                    ) latest WHERE rn=1
                """)
                latest={int(card):status for card,status in cur.fetchall()}
                cur.execute("SELECT count(*) FROM serviceit_vps_b2b_tinyfish_retry_attempts")
                attempts=min(99999,int(cur.fetchone()[0]))
                out("ATTEMPTS_TOTAL",attempts)
                out("CARDS_ATTEMPTED",sum(int(c in latest) for c in cards))
                for i,card in enumerate(cards,1):
                    status=latest.get(card,"NONE")
                    out("CARD_"+str(i)+"_RETRY_STATUS",
                        status if status in STATES else "UNKNOWN")
            else:
                out("ATTEMPTS_TOTAL",0)
                out("CARDS_ATTEMPTED",0)
                for i in range(len(cards)):
                    out("CARD_"+str(i+1)+"_RETRY_STATUS","NONE")
    out("CRM_WRITES",0)
    return 0 if len(cards)==4 else 2


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_READONLY_FAILURE","YES")
        raise SystemExit(2)
