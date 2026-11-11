#!/usr/bin/env python3
"""V38: forensic READ ONLY audit of V37 coordinator drift and uncertain Bitrix write.

No customer names, INNs, URLs, source text, contract details or secrets logged.
No mutable SQL, no Bitrix updates, no model or external procurement requests.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX

FILES = ("serviceit_vps_worker.py",
         "serviceit_b2b_dossier_consistency_v37.py",
         "serviceit_b2b_dossier_consistency_worker_v37.py")
TABLE = "serviceit_vps_b2b_dossier_consistency_v37_receipts"
VALID_STATES = frozenset((
    "RESERVED", "WRITE_INTENT", "VERIFIED", "NO_ATTESTED_NEW_FACTS",
    "INSUFFICIENT_CAPACITY", "NONSTANDARD_REPORT", "CONFLICT",
    "UNCERTAIN", "NO_RECEIPT", "NO_TABLE", "UNEXPECTED"))
TARGET_IDS = (11, 13, 15, 17)


def out(name, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,65}", name)
    assert re.fullmatch(r"(YES|NO|MISSING|NEW|OLD|OTHER|[0-9]{1,6}|[A-Z0-9_]{4,70})",
                        str(value))
    print("V38_FORENSIC_" + name + "=" + str(value), flush=True)


def digest_json(fields, salt):
    body = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(bytes(salt) + body).digest()


def read_status(conn, rest, card):
    row = conn.execute(f"""
        SELECT status,before_fields,after_fields,salt,digest
        FROM {TABLE}
        WHERE card_id=%s
        ORDER BY attempt_no DESC LIMIT 1
    """, (card,)).fetchone()
    if row is None:
        out(f"CARD_{card}_STATE", "NO_RECEIPT")
        return
    status, before, after, salt, proof = row
    status = status if status in VALID_STATES else "UNEXPECTED"
    out(f"CARD_{card}_STATE", status)
    if card != 11 or status not in ("UNCERTAIN", "VERIFIED", "WRITE_INTENT"):
        return
    if not isinstance(before, dict) or not isinstance(after, dict) or not after:
        out("CARD_11_EXPECTED_PATCH_PRESENT", "NO")
        return
    out("CARD_11_EXPECTED_PATCH_PRESENT", "YES")
    response = rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})
    item = normalized(response["item"])
    validate_identity(item, card)
    out("CARD_11_EXPECTED_FIELD_COUNT", min(999999, len(after)))
    out("CARD_11_EXPECTED_FIELDS_MATCH",
        sum(item.get(k) == v for k,v in after.items()))
    out("CARD_11_PRIOR_FIELDS_MATCH",
        sum(item.get(k) == v for k,v in before.items()))
    out("CARD_11_ALL_EXPECTED_MATCH",
        "YES" if all(item.get(k) == v for k,v in after.items()) else "NO")
    out("CARD_11_ALL_PRIOR_MATCH",
        "YES" if all(item.get(k) == v for k,v in before.items()) else "NO")
    if isinstance(salt,(memoryview,bytes)) and isinstance(proof,(memoryview,bytes)):
        out("CARD_11_INTENT_DIGEST_VALID",
            "YES" if secrets.compare_digest(
                digest_json(after,salt),bytes(proof)) else "NO")
    else:
        out("CARD_11_INTENT_DIGEST_VALID", "NO")
    for name, code in (
        ("REPORT",PREFIX+"AI_FULL_REPORT"),
        ("INN",PREFIX+"COMPANY_INN"),
        ("BUYER",PREFIX+"COMPANY_NAME"),
        ("REGION",PREFIX+"REGION"),
        ("SUMMARY",PREFIX+"AI_SUMMARY")):
        if code in after:
            out("CARD_11_"+name+"_MATCH",
                "YES" if item.get(code)==after[code] else "NO")


def main():
    # Strict reviewed SHA-256s for NEW and OLD worker/module sources.
    if len(sys.argv) != 1 + len(FILES)*2:
        out("ARGS_VALID", "NO")
        return 2
    expected_new = sys.argv[1:4]
    expected_old = sys.argv[4:7]
    if not all(re.fullmatch(r"[a-f0-9]{64}",h) for h in expected_new):
        out("ARGS_VALID", "NO")
        return 2
    if not re.fullmatch(r"[a-f0-9]{64}",expected_old[0]) or expected_old[1:]!=["MISSING","MISSING"]:
        out("ARGS_VALID","NO")
        return 2
    out("ARGS_VALID","YES")
    actual=[]
    for name in FILES:
        path=Path("/app/leadpilot")/name
        actual.append(hashlib.sha256(path.read_bytes()).hexdigest()
                      if path.is_file() and not path.is_symlink() else "MISSING")
    out("CURRENT_MODULES_ALL_NEW","YES" if actual==expected_new else "NO")
    out("CURRENT_MODULES_ALL_OLD","YES" if actual==expected_old else "NO")
    for i,h in enumerate(actual,1):
        out("MODULE_"+str(i)+"_MATCH",
            "NEW" if h==expected_new[i-1]
            else ("OLD" if h==expected_old[i-1] else
                  ("MISSING" if h=="MISSING" else "OTHER")))
    from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS,exact_environment
    out("COORDINATOR_300S","YES" if INTERVAL_SECONDS==300 else "NO")
    env=dict(os.environ)
    exact=exact_environment(env)
    out("CONSISTENCY_GATE_ACTIVE",
        "YES" if exact.get("SERVICEIT_VPS_B2B_DOSSIER_CONSISTENCY_ENABLED")=="YES" else "NO")
    out("B2B_DEEP_RESEARCH_ACTIVE",
        "YES" if exact.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES" else "NO")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn, httpx.Client(
            trust_env=False,follow_redirects=False,timeout=25) as client:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            exists=conn.execute(
                "SELECT to_regclass(%s)",("public."+TABLE,)
            ).fetchone()[0]
            out("LEDGER_EXISTS","YES" if exists else "NO")
            if exists is None:
                for card in TARGET_IDS:
                    out(f"CARD_{card}_STATE","NO_TABLE")
            else:
                count=conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
                out("TOTAL_RECEIPTS",min(999999,int(count)))
                pending=conn.execute(
                    f"SELECT count(*) FROM {TABLE} "
                    "WHERE status IN ('RESERVED','WRITE_INTENT')").fetchone()[0]
                out("PENDING_WRITES",min(999999,int(pending)))
                rest=Rest(webhook,client)
                for card in TARGET_IDS:
                    read_status(conn, rest, card)
    out("CRM_WRITES",0)
    out("POSTGRES_WRITES",0)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_READONLY_FAILURE","YES")
        raise SystemExit(2)
