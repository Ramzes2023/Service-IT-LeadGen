#!/usr/bin/env python3
"""V43: prove signed B2B buyer INN and set only existing Bitrix COMPANY_INN.

Preview has no changes. Apply uses durable source-bound PostgreSQL intent,
strict customer context and prewrite checks, one HTTP update, and readback.
No blind retry; uncertain status freezes the card. No other CRM field edits.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import sys

import httpx

from leadpilot.bitrix_1062_mapping import _is_valid_inn
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_v19_backfill import active_card_write
from leadpilot.serviceit_b2b_v20_versioned import (
    LOCK_ID, TABLE as V20_TABLE, compute_digest, safe_notice)
from leadpilot.serviceit_enrichment_worker import Rest, fingerprint, normalized, validate_identity
from leadpilot.serviceit_vps_b2b_document_worker import LOCK_ID as DOCUMENT_LOCK
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import (
    scoped_subset_update, verify_patch_schema, _non_target_unchanged)
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted, exact_environment

SOURCE = {
    19: ("4676478", "АО «Тандер»", "2310031475",
         "https://www.b2b-center.ru/app/market-next/servernoe-oborudovanie-dlia-data/tender-4676478/",
         "https://www.b2b-center.ru/firms/ao-tander/260966/"),
    21: ("4676643", "АО «Издательство „Просвещение“»", "7715995942",
         "https://zakupki.kontur.ru/4676643_458",
         "https://zakupki.kontur.ru/4676643_458"),
    23: ("4677404", "ООО УК «Промышленно-металлургический холдинг»", "0411075740",
         "https://zakupki.kontur.ru/4677404_40",
         "https://zakupki.kontur.ru/4677404_40"),
}
INN_FIELD = PREFIX+"COMPANY_INN"
COMPANY_FIELD = PREFIX+"COMPANY_NAME"
REPORT_FIELD = PREFIX+"AI_FULL_REPORT"
SIGNAL_FIELD = PREFIX+"SIGNAL_ID"
TABLE = "serviceit_vps_b2b_inn_field_v43_receipts"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_inn_field_v43_receipts (
 card_id BIGINT PRIMARY KEY CHECK (card_id IN (19,21,23)),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('WRITE_INTENT','VERIFIED','UNCERTAIN')),
 before_inn TEXT,
 after_inn TEXT NOT NULL,
 salt BYTEA NOT NULL CHECK(octet_length(salt)=16),
 digest BYTEA NOT NULL CHECK(octet_length(digest)=32),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
SAFE = re.compile(r"[A-Z0-9_]{1,90}\Z")
MODES = frozenset(("preview","apply","readback"))
RUNTIME_MODULES = (
    "serviceit_enrichment_worker.py",
    "serviceit_b2b_v20_versioned.py",
    "serviceit_b2b_dossier_consistency_worker_v37.py",
    "serviceit_vps_worker.py",
)


def out(key,value):
    assert SAFE.fullmatch(key) and SAFE.fullmatch(str(value))
    print("V43_INN_"+key+"="+str(value),flush=True)


def need(condition,reason):
    if not condition:
        raise ValueError(reason)


def normal(value):
    return re.sub(r"[^а-яa-z0-9]","",str(value).casefold().replace("ё","е"))


def company_class(value,buyer):
    if not isinstance(value,str) or not value.strip():
        return "EMPTY"
    actual,expected=normal(value),normal(buyer)
    if actual==expected:
        return "MATCH"
    # The B2B-Center public registry abbreviates the exact legal buyer
    # ООО УК «Промышленно-металлургический холдинг» as ООО УК «ПМХ».
    # This alias is approved ONLY for this reviewed buyer, not a generic
    # abbreviation rule that might join unrelated legal entities.
    if (expected==normal(SOURCE[23][1])
            and actual in {normal("ООО УК «ПМХ»"),
                           normal("ООО «УК ПМХ»")}):
        return "PUBLIC_VERIFIED_ALIAS"
    if actual.startswith(expected) and actual[len(expected):] in (
            "пооткрытымисточникам","проверен"):
        return "MATCH"
    if actual in ("b2bcenter","неустановлен","неустановлено",
                  "неизвестно","нетданных","неподтвержден",
                  "заказчикнеустановлен","неуказан"):
        return "PLACEHOLDER"
    if "b2bcenter" in actual and len(actual)<=45:
        return "SOURCE_PLATFORM"
    if re.search(r"(?iu)(?:^|[\s«„])(ПАО|АО|ООО|ОАО|ИП)(?:[\s»“]|$)",
                 value.strip()):
        return "DIFFERENT_LEGAL_ENTITY"
    return "OTHER_COMPANY_TEXT"


def passport(report):
    if not isinstance(report,str):
        return ""
    start=report.find("01  ПАСПОРТ ЗАКУПКИ")
    if start<0:
        return ""
    end=report.find("02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ",start)
    return report[start:end] if end>start else ""


def read(rest,card):
    result=rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})
    item=normalized(result["item"])
    validate_identity(item,card)
    return item


def digest_of(patch,salt):
    raw=json.dumps(patch,ensure_ascii=False,sort_keys=True,
                   separators=(",",":")).encode("utf-8")
    return hashlib.sha256(salt+raw).digest()


def signed_evidence(conn,card,tid,buyer,inn,buyer_url,inn_url):
    source="B2B-CENTER-"+tid
    source_hash=hashlib.sha256(source.encode("ascii")).hexdigest()
    row=conn.execute("""
       SELECT b.notice_json FROM serviceit_vps_b2b_inbox b
       JOIN serviceit_vps_b2b_dossier_v11_receipts v
         ON v.card_id=b.card_id AND v.status='VERIFIED'
       WHERE b.state='VERIFIED' AND b.card_id=%s
         AND b.source_id=%s AND v.source_hash=%s
       LIMIT 1
    """,(card,source,source_hash)).fetchone()
    need(row and isinstance(row[0],dict),"NO_SIGNED_V11_NOTICE")
    matches=conn.execute(f"""
       SELECT after_fields,salt,digest FROM {V20_TABLE}
       WHERE card_id=%s AND source_hash=%s AND status='VERIFIED'
       ORDER BY attempt_no DESC LIMIT 20
    """,(card,source_hash)).fetchall()
    ok=False
    for fields,salt,digest in matches:
        if (not isinstance(fields,dict) or set(fields)!=AI_FIELDS
                or not isinstance(salt,(bytes,memoryview))
                or not isinstance(digest,(bytes,memoryview))):
            continue
        full=fields.get(REPORT_FIELD)
        section=passport(full)
        if (section and buyer in section and inn in section
                and buyer_url in full and inn_url in full
                and secrets.compare_digest(
                    compute_digest(fields,bytes(salt)),bytes(digest))):
            ok=True
            break
    need(ok,"NO_ATTESTED_V20_BUYER_INN")
    return row[0],source,source_hash


def check_source(conn,rest,card):
    tid,buyer,inn,buyer_url,inn_url=SOURCE[card]
    need(re.fullmatch(r"\d{10}|\d{12}",inn)
         and _is_valid_inn(inn),"INN_CHECKSUM_BAD")
    notice,source,source_hash=signed_evidence(
        conn,card,tid,buyer,inn,buyer_url,inn_url)
    item=read(rest,card)
    need(item.get(SIGNAL_FIELD)==source,"CARD_SIGNED_ID_MISMATCH")
    url,title=safe_notice(notice,card,source,item)
    need(url==notice.get("source_url") and bool(title),
         "CARD_TENDER_IDENTITY_MISMATCH")
    section=passport(item.get(REPORT_FIELD))
    need(section and buyer in section and inn in section
         and buyer_url in item[REPORT_FIELD] and inn_url in item[REPORT_FIELD],
         "CARD_BUYER_INN_PROOF_NOT_VISIBLE")
    return item,inn,source_hash,company_class(item.get(COMPANY_FIELD),buyer)


def ensure_receipt_table(conn):
    with conn.transaction():
        conn.execute(DDL)


def reserve(conn,card,source_hash,before,target):
    salt=secrets.token_bytes(16)
    proof=digest_of({INN_FIELD:target},salt)
    with conn.transaction():
        row=conn.execute(f"""
           INSERT INTO {TABLE}
           (card_id,source_hash,status,before_inn,after_inn,salt,digest)
           VALUES (%s,%s,'WRITE_INTENT',%s,%s,%s,%s)
           ON CONFLICT(card_id) DO NOTHING RETURNING card_id
        """,(card,source_hash,before,target,salt,proof)).fetchone()
        need(row and row[0]==card,"EXISTING_RECEIPT_NO_REPLAY")
    return salt,proof


def mark(conn,card,after):
    with conn.transaction():
        row=conn.execute(f"""
           UPDATE {TABLE} SET status=%s,updated_at=now()
           WHERE card_id=%s AND status='WRITE_INTENT' RETURNING card_id
        """,(after,card)).fetchone()
        need(row and row[0]==card,"RECEIPT_STATUS_DRIFT")


def check_one(conn,rest,card,mode):
    item,target,source_hash,context=check_source(conn,rest,card)
    out(f"CARD_{card}_SIGNED_BUYER","YES")
    out(f"CARD_{card}_COMPANY_CONTEXT",context)
    before=item.get(INN_FIELD)
    out(f"CARD_{card}_INN_BEFORE",
        "MATCH" if before==target else ("EMPTY" if before in (None,"") else "CONFLICT"))
    if mode=="readback":
        row=conn.execute(f"""
           SELECT status,source_hash,after_inn,salt,digest
           FROM {TABLE} WHERE card_id=%s
        """,(card,)).fetchone()
        verified=(row and row[0]=="VERIFIED" and row[1]==source_hash
                  and row[2]==target and before==target
                  and secrets.compare_digest(
                      digest_of({INN_FIELD:target},bytes(row[3])),bytes(row[4])))
        out(f"CARD_{card}_RESULT","MATCH" if verified else "MISMATCH")
        return bool(verified)
    if before==target:
        out(f"CARD_{card}_RESULT","ALREADY_PRESENT")
        return True
    allowed=context in (
        "MATCH","PUBLIC_VERIFIED_ALIAS","EMPTY","PLACEHOLDER",
        "SOURCE_PLATFORM")
    if before not in (None,"") or not allowed:
        out(f"CARD_{card}_RESULT",
            "COMPANY_REVIEW_REQUIRED" if not allowed else "EXISTING_INN_CONFLICT")
        return False
    verify_patch_schema(rest,{INN_FIELD:target})
    if mode=="preview":
        out(f"CARD_{card}_RESULT","READY")
        return True
    if active_card_write(conn,card):
        out(f"CARD_{card}_RESULT","OTHER_WRITE_IN_FLIGHT")
        return False
    fresh=read(rest,card)
    if fingerprint(fresh)!=fingerprint(item):
        out(f"CARD_{card}_RESULT","CARD_CHANGED")
        return False
    salt,proof=reserve(conn,card,source_hash,before,target)
    try:
        scoped_subset_update(rest,card,{INN_FIELD:target})
        after=read(rest,card)
        need(after.get(INN_FIELD)==target
             and _non_target_unchanged(fresh,after,{INN_FIELD:target})
             and secrets.compare_digest(
                 digest_of({INN_FIELD:target},salt),proof),
             "CRM_READBACK_FAILED")
        mark(conn,card,"VERIFIED")
    except Exception:
        try:
            mark(conn,card,"UNCERTAIN")
        except Exception:
            pass
        out(f"CARD_{card}_RESULT","UNCERTAIN_NO_REPLAY")
        return False
    out(f"CARD_{card}_RESULT","WRITTEN_VERIFIED")
    return True


def main():
    if len(sys.argv)!=2+len(RUNTIME_MODULES) or sys.argv[1] not in MODES:
        out("INVALID_ARGS","YES")
        return 2
    mode=sys.argv[1]
    expected=sys.argv[2:]
    if any(re.fullmatch(r"[a-f0-9]{64}",h) is None for h in expected):
        out("INVALID_MODULE_HASH","YES")
        return 2
    from pathlib import Path
    actual=[
        hashlib.sha256((Path("/app/leadpilot")/name).read_bytes()).hexdigest()
        for name in RUNTIME_MODULES
    ]
    if actual!=expected:
        out("RUNTIME_MODULE_SHA_MATCH","NO")
        return 2
    out("RUNTIME_MODULE_SHA_MATCH","YES")
    env=dict(os.environ)
    need(live_permitted(env),"VPS_NOT_LIVE")
    exact=exact_environment(env)
    need(exact.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and exact.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "B2B_APPROVAL_MISSING")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn,httpx.Client(
            trust_env=False,follow_redirects=False,timeout=25) as client:
        locks=sorted({LOCK_ID,DOCUMENT_LOCK})
        held=[]
        for lock in locks:
            acquired=bool(conn.execute(
                "SELECT pg_try_advisory_lock(%s)",(lock,)).fetchone()[0])
            if not acquired:
                out("LOCK_BUSY","YES")
                for old in reversed(held):
                    conn.execute("SELECT pg_advisory_unlock(%s)",(old,))
                return 2
            held.append(lock)
        try:
            if mode=="apply":
                ensure_receipt_table(conn)
            rest=Rest(webhook,client)
            good=0
            for card in sorted(SOURCE):
                try:
                    good+=bool(check_one(conn,rest,card,mode))
                except Exception:
                    out(f"CARD_{card}_RESULT","BLOCKED_SAFE")
            out("CARDS_CHECKED",len(SOURCE))
            out("SUCCESSFUL",good)
            out("MODE",mode.upper())
            out("CRM_WRITES_POSSIBLE","YES" if mode=="apply" else "NO")
            return 0 if good==len(SOURCE) else 2
        finally:
            for lock in reversed(held):
                conn.execute("SELECT pg_advisory_unlock(%s)",(lock,))


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_FAILURE","YES")
        raise SystemExit(2)
