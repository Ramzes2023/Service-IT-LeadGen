#!/usr/bin/env python3
"""V42: independently audit three actual new Bitrix B2B dossiers READ ONLY.

No SQL writes, CRM mutations, public-site requests, model calls, secrets,
source quotes, buyer names, INNs, addresses, or tender text in printed logs.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_vps_worker import live_permitted, exact_environment, INTERVAL_SECONDS

SOURCES = {
    19: ("4676478","АО «Тандер»","2310031475"),
    21: ("4676643","АО «Издательство „Просвещение“»","7715995942"),
    23: ("4677404","ООО УК «Промышленно-металлургический холдинг»","0411075740"),
}
MODULES = (
    "serviceit_vps_worker.py",
    "serviceit_enrichment_worker.py",
    "serviceit_b2b_dossier_consistency_worker_v37.py",
)
TABLES = {
    "V11":"serviceit_vps_b2b_dossier_v11_receipts",
    "V19":"serviceit_vps_b2b_v19_research_receipts",
    "V20":"serviceit_vps_b2b_v20_revision_receipts",
    "V41":"serviceit_vps_b2b_dossier_consistency_v37_receipts",
    "TDI":"serviceit_vps_b2b_tender_doc_receipts",
    "TINYFISH":"serviceit_vps_b2b_tinyfish_retry_attempts",
}
SAFE = re.compile(r"[A-Z0-9_]{2,85}\Z")


def out(name,value):
    assert SAFE.fullmatch(name)
    assert SAFE.fullmatch(str(value)) or (
        type(value) is int and 0<=value<=999999)
    print("V42_NEW_CARDS_"+name+"="+str(value),flush=True)


def status(value):
    v=str(value or "NO_RECEIPT").upper()
    return v if SAFE.fullmatch(v) else "UNKNOWN"


def count_present(text,phrase):
    return phrase.casefold() in (text or "").casefold()


def receipt(conn,table,card):
    exists=conn.execute("SELECT to_regclass(%s)",("public."+table,)).fetchone()[0]
    if not exists:
        return "NO_TABLE"
    rows=conn.execute(
        "SELECT status FROM "+table+
        " WHERE card_id=%s ORDER BY "+(
            "attempt_no DESC" if table.endswith(("retry_attempts","consistency_v37_receipts","v20_revision_receipts")) else
            "updated_at DESC" if table.endswith(("tender_doc_receipts","v19_research_receipts")) else
            "card_id DESC")+
        " LIMIT 1", (card,)).fetchone()
    return status(rows[0]) if rows else "NO_RECEIPT"


def check_one(conn,rest,card,tid,buyer,inn):
    row=conn.execute("""
        SELECT b.state,b.card_id,v.status
        FROM serviceit_vps_b2b_inbox b
        LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v
          ON v.card_id=b.card_id
        WHERE b.source_id=%s
        LIMIT 1
    """, ("B2B-CENTER-"+tid,)).fetchone()
    out("CARD_"+str(card)+"_INBOX", status(row[0]) if row else "NOT_RECEIVED")
    out("CARD_"+str(card)+"_DB_ID_MATCH",
        "YES" if row and row[1]==card else "NO")
    out("CARD_"+str(card)+"_V11_INBOX",
        status(row[2]) if row and row[2] else "NO_RECEIPT")
    for k,table in TABLES.items():
        out("CARD_"+str(card)+"_"+k,receipt(conn,table,card))
    data=rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"]
    item=normalized(data)
    validate_identity(item,card)
    out("CARD_"+str(card)+"_SOURCE_MATCH",
        "YES" if item.get(PREFIX+"SIGNAL_ID")=="B2B-CENTER-"+tid else "NO")
    report=item.get(PREFIX+"AI_FULL_REPORT") or ""
    brief=item.get(PREFIX+"AI_MANAGER_BRIEF") or ""
    summary=item.get(PREFIX+"AI_SUMMARY") or ""
    after=report.partition("01  ПАСПОРТ ЗАКУПКИ")[2]
    stop=min([x for x in (
        after.find("02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ"),
        after.find("02  ПРЕДМЕТ И ПАРАМЕТРЫ")) if x>=0],default=0)
    passport=after[:stop] if stop else after[:2000]
    out("CARD_"+str(card)+"_PASSPORT_PRESENT",
        "YES" if after else "NO")
    out("CARD_"+str(card)+"_PASSPORT_BUYER",
        "YES" if count_present(passport,buyer) else "NO")
    out("CARD_"+str(card)+"_PASSPORT_INN",
        "YES" if inn in passport else "NO")
    out("CARD_"+str(card)+"_CRM_COMPANY",
        "MATCH" if item.get(PREFIX+"COMPANY_NAME")==buyer else
        ("EMPTY" if not item.get(PREFIX+"COMPANY_NAME") else "OTHER"))
    out("CARD_"+str(card)+"_CRM_INN",
        "MATCH" if item.get(PREFIX+"COMPANY_INN")==inn else
        ("EMPTY" if not item.get(PREFIX+"COMPANY_INN") else "OTHER"))
    out("CARD_"+str(card)+"_CRM_REGION",
        "PRESENT" if item.get(PREFIX+"REGION") else "EMPTY")
    out("CARD_"+str(card)+"_BRIEF_BUYER",
        "YES" if count_present(brief,buyer) else "NO")
    out("CARD_"+str(card)+"_SUMMARY_INN",
        "YES" if inn in summary else "NO")
    out("CARD_"+str(card)+"_STANDARD_CITATIONS",
        min(999999,report.count("ПРОЧИТАННЫЙ ИСТОЧНИК")))
    out("CARD_"+str(card)+"_REPORT_CHARS",min(999999,len(report)))
    out("CARD_"+str(card)+"_V41_CANONICAL",
        "YES" if "V37 · СОГЛАСОВАННЫЙ ПАСПОРТ" in report else "NO")
    out("CARD_"+str(card)+"_STALE_BUYER",
        "YES" if ("Заказчик: не установлен" in passport
                  or "ЗАКАЗЧИК  не установлен" in passport) else "NO")
    out("CARD_"+str(card)+"_STALE_INN",
        "YES" if ("ИНН: нет подтверждения" in passport or
                  "ИНН: не подтверждён" in passport) else "NO")
    out("CARD_"+str(card)+"_NAMED_MANAGER",
        "YES" if "для михаила" in report.casefold() else "NO")
    return 1


def main():
    expected=sys.argv[1:]
    if len(expected)!=len(MODULES) or not all(
            re.fullmatch(r"[0-9a-f]{64}",v) for v in expected):
        out("SOURCE_ARGUMENTS","NO")
        return 2
    actual=[
        hashlib.sha256((Path("/app/leadpilot")/name).read_bytes()).hexdigest()
        for name in MODULES
    ]
    out("SOURCE_MATCH","YES" if actual==expected else "NO")
    if actual!=expected:
        return 2
    env=dict(os.environ)
    exact=exact_environment(env)
    out("COORDINATOR_ACTIVE",
        "YES" if live_permitted(env) and INTERVAL_SECONDS==300 else "NO")
    for key,name in (
        ("DEEP_RESEARCH","SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"),
        ("CONSISTENCY","SERVICEIT_VPS_B2B_DOSSIER_CONSISTENCY_ENABLED"),
        ("V19","SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED"),
        ("V20","SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED"),
        ("TINYFISH","SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED"),
    ):
        out(key+"_GATE","YES" if exact.get(name)=="YES" else "NO")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn,httpx.Client(
            trust_env=False,follow_redirects=False,timeout=25) as client:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            rest=Rest(webhook,client)
            done=0
            for card,(tid,buyer,inn) in SOURCES.items():
                done+=check_one(conn,rest,card,tid,buyer,inn)
    out("CARDS_CHECKED",done)
    out("CRM_WRITES",0)
    out("POSTGRES_WRITES",0)
    return 0 if done==len(SOURCES) else 2


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_FAILURE","YES")
        raise SystemExit(2)
