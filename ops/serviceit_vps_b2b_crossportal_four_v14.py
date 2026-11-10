#!/usr/bin/env python3
"""One-use PUBLIC sourced V14 addenda; verified recovery from V13 intent for exactly four original V12 B2B cards.

No synthetic leads, attachment changes, AI replay, FNS, score/stage/STOP,
customer contact creation or outreach. Preserve ALL existing V11/V12 AI text.
A durable PostgreSQL WRITE_INTENT precedes each bounded 1062/21 CRM update.
Exactly nine AI fields must be passed to the bounded REST adapter; only\nthree explanatory AI text VALUES may change, with exact readback.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import sys

import httpx

VERSION = "B2B_PUBLIC_EVIDENCE_V14"
LOCK_ID = 6754819001
MAX_REPORT = 12000
MAX_TEXT = 4096
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_public_v14_receipts(
 card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN
  ('RESERVED','WRITE_INTENT','VERIFIED','NO_NEW_EVIDENCE','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,after_fields JSONB,
 salt BYTEA,digest BYTEA,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""
EXPECTED_LIVE_SHAS = {
    "serviceit_vps_b2b_deep_research.py":
        "168cc67ecef993e75a0dfd65c16e79ee7a841e9b051fe1eb1bc18cd8d8b9655c",
    "serviceit_b2b_public_mirrors.py":
        "e11fb8f76618371e80938516c569fd5aea8757baac8254e02ffa3ec9716ab593",
    "serviceit_b2b_dossier_v11.py":
        "233c211f75e75d4bfeddbedd9e3b163ddf1e05ee39372bb6010b119f8a88009a",
    "serviceit_vps_b2b_dossier_v11_worker.py":
        "c204fcc99f4db9821fa3e9e1209ee0211117c3e04aaa68561c2325965c1f1ffe",
}

class V13Blocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise V13Blocked(code)


def status(key, value):
    require(bool(re.fullmatch(r"[A-Z0-9_]{3,68}", key)), "OUTPUT_KEY_INVALID")
    raw = str(value)
    require(bool(re.fullmatch(
        r"(YES|NO|VERIFIED|NO_NEW_EVIDENCE|CONFLICT|UNCERTAIN|[0-9]{1,3})",
        raw)), "OUTPUT_VALUE_INVALID")
    print("B2B_PUBLIC_V14_" + key + "=" + raw,flush=True)


def _line(value):
    # Public content is untrusted. Never let a web page inject a fake header,
    # an uncontrolled layout line, or Bitrix supplementary emoji.
    if type(value) is not str:
        return ""
    return "".join("•" if ord(ch)>0xFFFF else ch for ch in
                   " ".join(value.split())
                   if 32 <= ord(ch) < 0x10FFFF
                   and ord(ch) not in (0xFE0F,0x200D,0x20E3,0x7F))


def draft_addendum(old, facts, buyer, inn, contacts, *, source_id=""):
    """Pure additive three-field update; no inference beyond verified quotes.

    Recognize published INN as a public claim, NEVER official FNS.
    Corporate contacts are already cross-checked by checked_public_contacts,
    and must never be called the tender procurement contact.
    """
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
    from leadpilot.serviceit_b2b_dossier_v11 import LABELS,REPORT,BRIEF
    require(type(old) is dict and set(old)==AI_FIELDS
            and all(type(v) is str and v.strip()
                    and len(v) <= (MAX_REPORT if k==REPORT else MAX_TEXT)
                    for k,v in old.items()),"BASE_NINE_INVALID")
    require(type(facts) is list and type(contacts) is list,
            "FACTS_CONTACTS_INVALID")
    old_report=old[REPORT]
    require("10  ПУБЛИЧНАЯ ПРОВЕРКА V14" not in old_report,
            "ADDENDUM_ALREADY_PRESENT")
    rows=[]
    if buyer and buyer not in old_report:
        rows.append("Заказчик по опубликованной странице (не ФНС): " + _line(buyer))
    if type(inn) is dict and type(inn.get("inn")) is str:
        if inn["inn"] not in old_report:
            rows.append("ИНН заказчика из открытой публикации (НЕ ФНС): "
                        + _line(inn["inn"]) + " | " + _line(inn.get("public_source","")))
    for kind,val,url in contacts[:5]:
        if type(kind) is not str or type(val) is not str or type(url) is not str:
            continue
        if val not in old_report:
            kind_text="телефон" if kind=="телефон" else "email"
            rows.append("Общий " + kind_text + " компании (не ответственного за тендер): "
                        + _line(val) + " | " + _line(url))
    # Prefer buyer/deadline/terms and source citations over generic technology.
    priority={name:i for i,name in enumerate((
        "DEADLINE","ELIGIBILITY","SUBMISSION","PAYMENT","BUDGET",
        "EVALUATION","GUARANTEE","LOCATION","TECHNOLOGY","DOCUMENTS",
        "DELIVERY","SCOPE","BUYER","OTHER"
    ))}
    records=sorted((x for x in facts if type(x) is dict),
                   key=lambda x:priority.get(x.get("category"),99))
    for fact in records[:16]:
        quote,link= fact.get("quote"),fact.get("url")
        if (type(quote) is not str or type(link) is not str
            or not link.startswith("https://")
            or not 12 <= len(quote) <= 300 or len(link)>950
            or fact.get("category") not in LABELS):
            continue
        if quote in old_report:
            continue
        rows.append(_line(LABELS[fact["category"]]) + ": «"
                    + _line(quote) + "» | " + _line(link))
    if not rows:
        return None,{"new_rows":0}
    header=[
        "",
        "10  ПУБЛИЧНАЯ ПРОВЕРКА V14",
        "Сведения прочитаны с публичных страниц; не заменяют ТЗ и"
        " не подтверждают актуальную редакцию закупки.",
        "Корпоративные телефоны/email НЕ являются контактами тендерного специалиста.",
    ]
    # Never truncate or delete any historical V11/V12 quote: keep only
    # as many new proof rows as the remaining Bitrix field allows.
    available=MAX_REPORT-len(old_report)-2
    selected=[]
    for row in rows:
        candidate="\n".join(header+selected+[row])
        if len(candidate) <= available and len(candidate) <= 3500:
            selected.append(row)
    if not selected:
        return None,{"new_rows":0}
    addon="\n".join(header+selected)
    result=dict(old)
    result[REPORT]=old_report+"\n\n"+addon
    brief=old[PREFIX+"AI_MANAGER_BRIEF"]
    notice="Публичное дополнение V14: "+ " | ".join(selected[:2])
    notice=_line(notice)[:560]
    if len(notice)+len(brief)+2<=MAX_TEXT:
        result[PREFIX+"AI_MANAGER_BRIEF"]=notice+"\n"+brief
    summary=old[PREFIX+"AI_SUMMARY"]
    short=_line("Уточнения из открытых источников: "+" | ".join(selected[:1]))[:370]
    if len(short)+len(summary)+2<=MAX_TEXT:
        result[PREFIX+"AI_SUMMARY"]=short+"\n"+summary
    require(set(result)==AI_FIELDS
            and all(type(v) is str and v.strip()
                    and len(v) <= (MAX_REPORT if k==REPORT else MAX_TEXT)
                    for k,v in result.items()),"DRAFT_LIMIT_INVALID")
    require(all(result[k]==old[k] for k in AI_FIELDS if k not in (
        REPORT,PREFIX+"AI_MANAGER_BRIEF",PREFIX+"AI_SUMMARY")),
        "UNRELATED_AI_FIELD_CHANGED")
    return result,{"new_rows":len(selected)}


def update_status(conn, card, before, after):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_b2b_public_v14_receipts
                SET status=%s,updated_at=now()
                WHERE card_id=%s AND status=%s RETURNING card_id
            """,(after,card,before))
            require(cur.fetchone() is not None,"RECEIPT_STATE_CONFLICT")


def reserve(conn, card, source, old):
    from leadpilot.serviceit_vps_native_fence import PostgresNativeFence
    PostgresNativeFence.validate_card(card)
    require(type(source) is str and bool(re.fullmatch(
        r"B2B-CENTER-\d{5,12}",source)),"SOURCE_ID_INVALID")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO serviceit_vps_b2b_public_v14_receipts
                    (card_id,source_hash,status,before_fields)
                VALUES(%s,%s,'RESERVED',%s::jsonb)
                ON CONFLICT(card_id) DO NOTHING RETURNING card_id
            """,(card,hashlib.sha256(source.encode("ascii")).hexdigest(),
                 json.dumps(old,ensure_ascii=False)))
            return cur.fetchone() is not None


def intention(conn,card,fields):
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    salt=secrets.token_bytes(16)
    digest=compute_digest(fields,salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_public_v14_receipts
              SET status='WRITE_INTENT',after_fields=%s::jsonb,
                  salt=%s,digest=%s,updated_at=now()
              WHERE card_id=%s AND status='RESERVED'
              RETURNING card_id
            """,(json.dumps(fields,ensure_ascii=False),salt,digest,card))
            require(cur.fetchone() is not None,"WRITE_INTENT_NOT_DURABLE")
    return salt,digest


def live_sources_pinned():
    from pathlib import Path
    return all((Path("/app/leadpilot")/name).is_file()
               and hashlib.sha256((Path("/app/leadpilot")/name).read_bytes()).hexdigest()==sha
               for name,sha in EXPECTED_LIVE_SHAS.items())


def pending_other_writes(conn):
    with conn.cursor() as cur:
        cur.execute("""
          SELECT count(*) FROM serviceit_vps_native_receipts
          WHERE namespace IN ('AI_NINE_V1','B2B_RESEARCH_V4',
                              'B2B_PUBLIC_INN_V1','B2B_PUBLIC_INN_V2',
                              'FNS_CRM_V1','TASK_V1')
          AND status IN ('RESERVED','WRITE_INTENT')
        """)
        count=int(cur.fetchone()[0])
        for table in ("serviceit_vps_b2b_dossier_v10_receipts",
                      "serviceit_vps_b2b_dossier_v11_receipts",
                      "serviceit_vps_b2b_dossier_v12_continuity_receipts",
                      "serviceit_vps_b2b_tender_doc_receipts"):
            cur.execute("SELECT to_regclass(%s)",("public."+table,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute("SELECT count(*) FROM "+table
                        +" WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')")
            count+=int(cur.fetchone()[0])
    return count


def historical_four(conn):
    with conn.cursor() as cur:
        cur.execute("""
          SELECT b.card_id,b.source_id,b.notice_json,
                 v11.after_fields,v12.status,v12.after_fields,
                 prior13.status,prior13.before_fields,prior13.after_fields,
                 prior13.salt,prior13.digest,prior13.source_hash
          FROM serviceit_vps_b2b_dossier_v12_continuity_receipts v12
          JOIN serviceit_vps_b2b_dossier_v11_receipts v11
            ON v11.card_id=v12.card_id AND v11.status='VERIFIED'
          JOIN serviceit_vps_b2b_inbox b
            ON b.card_id=v11.card_id AND b.state='VERIFIED'
          LEFT JOIN serviceit_vps_b2b_public_v13_receipts prior13
            ON prior13.card_id=b.card_id
          WHERE v12.status IN ('VERIFIED','NO_CHANGE')
          ORDER BY b.received_at,b.source_id
        """)
        return cur.fetchall()


def run():
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_vps_worker import live_permitted,exact_environment,INTERVAL_SECONDS
    from leadpilot.serviceit_vps_pg_queue import db,secret_file
    from leadpilot.serviceit_vps_b2b_deep_research import (
        safe_notice,source_findings,public_buyer_candidate,
        published_buyer_inn_evidence,
    )
    from leadpilot.serviceit_b2b_routerai_deep import (
        source_only_investigation,
    )
    from leadpilot.serviceit_b2b_dossier_v11 import (
        original_nine,evidence_rows,checked_public_contacts,
    )
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX,AI_FIELDS
    from leadpilot.serviceit_enrichment_worker import (
        Rest,normalized,validate_identity,fingerprint,
    )
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import (
        compute_digest,non_ai_unchanged,
    )

    env=dict(os.environ)
    require(live_sources_pinned(),"LIVE_CODE_SHA_CHANGED")
    require(live_permitted(env) and INTERVAL_SECONDS==300
            and all(env.get(key)=="YES" for key in (
                "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED",
                "SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED",
                "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED",
                "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED",
                "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED",
                "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")),
            "RESEARCH_TDI_POLL_GATES_REQUIRED")
    exact=exact_environment(env)
    require(bool(exact.get("SERVICEIT_BRAVE_SEARCH_API_KEY")),
            "BRAVE_SECRET_UNAVAILABLE")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        rest=Rest(webhook,client)
        with db(env) as conn:
            conn.execute(DDL)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
                locked=cur.fetchone()[0]
            require(bool(locked),"OTHER_V11_WORKER_ACTIVE")
            try:
                require(pending_other_writes(conn)==0,"ACTIVE_WRITER_PRESENT")
                four=historical_four(conn)
                require(len(four)==4,"EXACT_FOUR_HISTORICAL_V12_REQUIRED")
                status("HISTORICAL_FOUR",len(four))
                write_count=0
                no_data_count=0
                for i, (card,source,notice,v11fields,v12status,v12fields,
                        v13status,v13before,v13after,v13salt,v13digest,
                        v13sourcehash) in enumerate(four,1):
                    code="CARD_"+str(i)+"_"
                    from leadpilot.serviceit_vps_native_fence import PostgresNativeFence
                    PostgresNativeFence.validate_card(card)
                    first=normalized(rest("crm.item.get",{
                        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                    })["item"])
                    validate_identity(first,card)
                    old=original_nine(first)
                    expected=v12fields if v12status=="VERIFIED" else v11fields
                    require(type(expected) is dict and old==expected,
                            "V12_OR_MANUAL_CRM_NINE_FIELDS_DRIFT")
                    safe_notice(notice,card,source,first)
                    if not reserve(conn,card,source,old):
                        status(code+"RESULT","CONFLICT")
                        continue
                    phase="RESERVED"
                    try:
                        # The historical V13 incident was a LOCAL REST scope
                        # rejection BEFORE network: Rest allows a full nine
                        # AI fields, not a partial three-field dict. A separate
                        # owner READONLY CRM audit #42923699913 proved exact
                        # old nine persisted, and full target is durable in PG.
                        # Reuse that already validated public evidence rather
                        # than spend Brave calls or blindly replay the mistake.
                        if v13status == "NO_NEW_EVIDENCE":
                            require(old==v13before,
                                    "PREVIOUS_NO_DATA_BASE_DRIFT")
                            update,counts=None,{"new_rows":0}
                            status(code+"REUSED_V13_INTENT","NO")
                        elif v13status == "UNCERTAIN":
                            require(type(v13before) is dict and old==v13before,
                                    "PREVIOUS_UNCERTAIN_CRM_NOT_OLD")
                            require(type(v13after) is dict and
                                    set(v13after)==AI_FIELDS and
                                    all(type(v) is str and v.strip()
                                        for v in v13after.values()),
                                    "PREVIOUS_UNCERTAIN_TARGET_INVALID")
                            require(v13sourcehash ==
                                    hashlib.sha256(source.encode("ascii")).hexdigest(),
                                    "PREVIOUS_UNCERTAIN_SOURCE_CONFLICT")
                            require(v13salt is not None and
                                    v13digest is not None and
                                    secrets.compare_digest(
                                        compute_digest(v13after,bytes(v13salt)),
                                        bytes(v13digest)),
                                    "PREVIOUS_UNCERTAIN_INTENT_HASH_INVALID")
                            require(v13after[PREFIX+"AI_FULL_REPORT"].startswith(
                                        old[PREFIX+"AI_FULL_REPORT"])
                                    and all(v13after[k]==old[k] for k in
                                        AI_FIELDS if k not in (
                                            PREFIX+"AI_FULL_REPORT",
                                            PREFIX+"AI_MANAGER_BRIEF",
                                            PREFIX+"AI_SUMMARY"))
                                    and "10  ПУБЛИЧНАЯ ПРОВЕРКА V13" in
                                        v13after[PREFIX+"AI_FULL_REPORT"],
                                    "PREVIOUS_UNCERTAIN_EVIDENCE_NOT_ADDITIVE")
                            update,counts=v13after,{"new_rows":7}
                            status(code+"REUSED_V13_INTENT","YES")
                        else:
                            require(v13status is None,
                                    "PREVIOUS_V13_STATUS_NOT_RECOVERABLE")
                            findings=source_findings(first,notice,exact,client)
                            report=source_only_investigation(findings)
                            _,facts=evidence_rows(findings,report)
                            buyer=public_buyer_candidate(findings)
                            inn=published_buyer_inn_evidence(findings) if buyer else None
                            contacts=checked_public_contacts(findings,buyer,inn)
                            update,counts=draft_addendum(
                                old,facts,buyer,inn,contacts,source_id=source)
                            status(code+"REUSED_V13_INTENT","NO")
                        status(code+"NEW_EVIDENCE_ROWS",min(counts["new_rows"],99))
                        if update is None:
                            update_status(conn,card,"RESERVED","NO_NEW_EVIDENCE")
                            status(code+"RESULT","NO_NEW_EVIDENCE")
                            no_data_count+=1
                            continue
                        fresh=normalized(rest("crm.item.get",{
                            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                        })["item"])
                        validate_identity(fresh,card)
                        require(fingerprint(first)==fingerprint(fresh)
                                and all(fresh.get(k)==old[k] for k in AI_FIELDS),
                                "CRM_CONCURRENT_EDIT_NO_OVERWRITE")
                        salt,proof=intention(conn,card,update)
                        phase="WRITE_INTENT"
                        # Live REST adapter permits exactly ALL NINE AI
                        # fields (or the approved 12-field bundle), never a
                        # partial 3-field update. Six other AI fields are
                        # unchanged byte-for-byte by draft_addendum / V13
                        # archived target proof. Non-AI CRM fields are NEVER
                        # sent, score/status/stage/tasks/STOP/TDI untouched.
                        require(set(update)==AI_FIELDS
                                and all(update[k]==old[k] for k in
                                        AI_FIELDS if k not in (
                                            PREFIX+"AI_FULL_REPORT",
                                            PREFIX+"AI_MANAGER_BRIEF",
                                            PREFIX+"AI_SUMMARY")),
                                "FULL_NINE_EXACT_OTHER_AI_PRESERVATION")
                        rest("crm.item.update",{
                            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                            "fields":update,
                        })
                        after=normalized(rest("crm.item.get",{
                            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                        })["item"])
                        validate_identity(after,card)
                        observed={k:after.get(k) for k in AI_FIELDS}
                        require(observed==update
                                and secrets.compare_digest(
                                  compute_digest(observed,salt),proof)
                                and non_ai_unchanged(fresh,after),
                                "NINE_FIELD_READBACK_OR_NON_AI_DRIFT")
                        update_status(conn,card,"WRITE_INTENT","VERIFIED")
                        write_count+=1
                        status(code+"RESULT","VERIFIED")
                    except Exception:
                        try:
                            update_status(conn,card,phase,
                                          "UNCERTAIN" if phase=="WRITE_INTENT"
                                          else "CONFLICT")
                        except Exception:
                            pass
                        status(code+"RESULT","UNCERTAIN" if phase=="WRITE_INTENT"
                               else "CONFLICT")
                        if phase=="WRITE_INTENT":
                            break  # never blindly continue after uncertain CRM write
                status("VERIFIED_WRITES",write_count)
                status("NO_NEW_EVIDENCE",no_data_count)
                with conn.cursor() as cur:
                    cur.execute("""
                      SELECT status,count(*) FROM serviceit_vps_b2b_public_v14_receipts
                      GROUP BY status
                    """)
                    grouped=dict(cur.fetchall())
                for name in ("VERIFIED","NO_NEW_EVIDENCE","CONFLICT","UNCERTAIN"):
                    status("RECEIPTS_"+name,min(int(grouped.get(name,0)),99))
                good=(grouped.get("UNCERTAIN",0)==0
                      and grouped.get("CONFLICT",0)==0
                      and grouped.get("RESERVED",0)==0
                      and grouped.get("WRITE_INTENT",0)==0
                      and grouped.get("VERIFIED",0)+grouped.get("NO_NEW_EVIDENCE",0)==4)
                status("SAFE_OUTCOME","YES" if good else "NO")
                return 0 if good else 2
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


def main():
    try:
        return run()
    except Exception:
        # Never print raw response, stack trace, source body, ID or secrets.
        status("SAFE_OUTCOME","NO")
        return 2

if __name__=="__main__":
    raise SystemExit(main())
