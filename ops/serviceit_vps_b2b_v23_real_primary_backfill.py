#!/usr/bin/env python3
"""V23 one-use source-only B2B dossier backfill with exact Bitrix readback.

Read four signed historical 1062/21 B2B signals. Only append genuinely unseen,
word-for-word quotations independently fetched from the exact public B2B
tender page. Three permitted AI text fields only; nine other AI fields remain
byte-for-byte and ALL non-AI fields must survive. Never change STOP/HOLD,
buyer, INN, score, task, stage or contacts. No Brave/RouterAI, no browser,
no captcha/session. Separate durable PG receipt; unknown write NEVER replayed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_b2b_routerai_deep import (
    b2b_primary_detail_facts, public_documents,
)
from leadpilot.serviceit_b2b_v19_backfill import (
    REPORT, SUMMARY, BRIEF, active_card_write, prepare_addendum, select_one,
)
from leadpilot.serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint,
)
from leadpilot.serviceit_public_evidence_research import research_public
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice, host_allowlist
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import (
    LOCK_ID, compute_digest, non_ai_unchanged,
)
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import (
    live_permitted, exact_environment, INTERVAL_SECONDS,
)

FILES = ("serviceit_b2b_v19_backfill.py", "serviceit_b2b_routerai_deep.py",
         "serviceit_vps_worker.py")
TABLE = "serviceit_vps_b2b_v23_primary_source_receipts"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_v23_primary_source_receipts (
 card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK (status IN
  ('RESERVED','WRITE_INTENT','VERIFIED','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""
PROTECTED = frozenset(("RESERVED","WRITE_INTENT","UNCERTAIN","CONFLICT"))
ALLOWED = frozenset(("OTHER","DEADLINE","BUDGET","PAYMENT","LOCATION",
                     "EVALUATION","DOCUMENTS","SUBMISSION"))
TAGS = {
    "OTHER": "ТИП ПРОЦЕДУРЫ / ОРГАНИЗАТОР / ДОСТУП (не проверено ФНС)",
    "DEADLINE": "СРОК ПОДАЧИ (страница B2B)",
    "BUDGET": "ЦЕНА (отсутствие суммы не равно нулю)",
    "PAYMENT": "УСЛОВИЯ ОПЛАТЫ",
    "LOCATION": "ГЕОГРАФИЯ / ПОСТАВКА",
    "EVALUATION": "КРИТЕРИИ ОЦЕНКИ",
    "DOCUMENTS": "ДОКУМЕНТЫ ЗАЯВКИ",
    "SUBMISSION": "ПОДАЧА ЗАЯВКИ",
}


class BackfillBlocked(ValueError):
    pass


def need(value, code):
    if not value:
        raise BackfillBlocked(code)


def emit(name, value):
    need(bool(re.fullmatch(r"[A-Z0-9_]{3,75}", name)), "OUTPUT_NAME_INVALID")
    need(bool(re.fullmatch(r"(?:YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,80})",
                           str(value))), "OUTPUT_VALUE_INVALID")
    print("V23_" + name + "=" + str(value), flush=True)


def verified_primary_lines(client, before, notice, env):
    """Only the signed, robots-approved ORIGINAL /tender-ID/ URL may testify."""
    url = notice["source_url"]
    tid = notice["tender_id"]
    path = urlsplit(url).path
    need(re.search(r"/tender-" + re.escape(tid) + r"/?$", path),
         "V23_PRIMARY_URL_ID_REQUIRED")
    result = research_public(
        before, client, host_allowlist(env),
        deep_search_enabled=False, deep_llm_context_enabled=False,
        browser_render=None, detail_excerpt_chars=4800,
    )
    if result.get("status") != "PUBLIC_PAGES_INSPECTED":
        return [], 0, "PRIMARY_UNREADABLE"
    docs = [d for d in public_documents(result)
            if d.get("url") == url
            and re.search(r"(?<!\d)" + re.escape(tid) + r"(?!\d)",
                          d.get("text",""))]
    if not docs:
        return [], 0, "PRIMARY_UNREADABLE"
    facts = b2b_primary_detail_facts(docs)
    prev = original_nine(before)[REPORT]
    lines, used = [], set()
    for fact in facts:
        quote, kind = fact.get("quote"), fact.get("category")
        if (kind not in ALLOWED or type(quote) is not str
                or not 12 <= len(quote) <= 220 or quote in prev
                or quote not in docs[0]["text"] or (kind, quote) in used):
            continue
        used.add((kind,quote))
        line = ("ПРОЧИТАННЫЙ ПЕРВОИСТОЧНИК / " + TAGS[kind] +
                ": " + quote + " [" + url + "]")
        if len(line) <= 1150:
            lines.append(line)
        if len(lines) == 6:
            break
    return lines, len(facts), "PRIMARY_READ"


def bounded_draft(item, notice, lines):
    before = original_nine(item)
    draft = prepare_addendum(before, lines, notice["tender_id"])
    if draft is None:
        return None
    suffix = draft[REPORT][len(before[REPORT]):]
    marker = "09  ДОПОЛНЕНИЕ V19"
    need(suffix.startswith("\n\n"+marker), "V23_REPORT_INSERT_BOUNDARY")
    draft[REPORT] = before[REPORT] + suffix.replace(
        marker, "14  ПРОВЕРЕННЫЕ ПОЛЯ ПЕРВОИСТОЧНИКА V23", 1)
    need(draft[REPORT].startswith(before[REPORT]) and
         len(draft[REPORT]) <= 12000,
         "V23_OLD_REPORT_MUST_SURVIVE")
    need(all(draft[key].endswith(before[key]) for key in (SUMMARY, BRIEF)),
         "V23_OLD_BRIEF_MUST_SURVIVE")
    return draft


def receipt_status(conn, card):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", ("public."+TABLE,))
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT status FROM "+TABLE+" WHERE card_id=%s", (card,))
        row = cur.fetchone()
        return row[0] if row else None


def prior_writes_safe(conn, card, source):
    """V11 proof required; do not race uncertain/in-flight previous writers."""
    src = hashlib.sha256(source.encode("ascii")).hexdigest()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT status,source_hash
            FROM serviceit_vps_b2b_dossier_v11_receipts
            WHERE card_id=%s
        """, (card,))
        v11 = cur.fetchone()
        if not v11 or v11 != ("VERIFIED", src):
            return False
        for table in ("serviceit_vps_b2b_v19_research_receipts",
                      "serviceit_vps_b2b_v20_revision_receipts"):
            cur.execute("SELECT to_regclass(%s)", ("public."+table,))
            if cur.fetchone()[0] is None:
                continue
            sql = ("SELECT status FROM "+table+" WHERE card_id=%s" +
                   (" ORDER BY attempt_no DESC LIMIT 1"
                    if "v20_" in table else ""))
            cur.execute(sql, (card,))
            row = cur.fetchone()
            if row and row[0] in PROTECTED:
                return False
    return not active_card_write(conn, card)


def reserve(conn, card, source, before):
    src = hashlib.sha256(source.encode("ascii")).hexdigest()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO "+TABLE+
                " (card_id,source_hash,status,before_fields) "
                "VALUES (%s,%s,'RESERVED',%s::jsonb) "
                "ON CONFLICT (card_id) DO NOTHING RETURNING card_id",
                (card,src,json.dumps(before,ensure_ascii=False)))
            return cur.fetchone() is not None


def intent(conn, card, desired):
    salt=secrets.token_bytes(16)
    digest=compute_digest(desired,salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE "+TABLE+" SET status='WRITE_INTENT',"
                "after_fields=%s::jsonb,salt=%s,digest=%s,updated_at=now() "
                "WHERE card_id=%s AND status='RESERVED' RETURNING card_id",
                (json.dumps(desired,ensure_ascii=False),salt,digest,card))
            need(cur.fetchone() is not None, "V23_WRITE_INTENT_NOT_DURABLE")
    return salt,digest


def finish(conn, card, status):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE "+TABLE+" SET status=%s,updated_at=now() "
                "WHERE card_id=%s AND status='WRITE_INTENT' RETURNING card_id",
                (status,card))
            need(cur.fetchone() is not None, "V23_FINALIZE_INCONSISTENT")


def handle(conn, client, rest, env, record, *, apply):
    card, source, notice = record
    need(isinstance(card,int) and
         bool(re.fullmatch(r"B2B-CENTER-[0-9]{5,12}",source)) and
         isinstance(notice,dict) and notice.get("source")=="B2B-Center" and
         notice.get("tender_id")==source.removeprefix("B2B-CENTER-"),
         "V23_SIGNED_SCOPE_REQUIRED")
    prior = receipt_status(conn,card)
    if prior is not None:
        return "ALREADY_FENCED",0,0
    if not prior_writes_safe(conn,card,source):
        return "PRIOR_WRITE_PENDING",0,0
    first=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(first,card)
    url,title=safe_notice(notice,card,source,first)
    need(bool(title) and url==notice["source_url"],"V23_SOURCE_IDENTITY")
    lines,facts,phase=verified_primary_lines(client,first,notice,env)
    if not lines:
        return (phase if phase!="PRIMARY_READ" else "NO_UNSEEN_FACTS",
                facts,0)
    draft=bounded_draft(first,notice,lines)
    if draft is None:
        return "NO_FIELD_CAPACITY",facts,len(lines)
    if not apply:
        return "DRAFT_READY",facts,len(lines)
    fresh=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(fresh,card)
    if (fingerprint(first)!=fingerprint(fresh)
            or not prior_writes_safe(conn,card,source)):
        return "PREWRITE_CHANGED",facts,0
    if not reserve(conn,card,source,original_nine(first)):
        return "ALREADY_FENCED",facts,0
    salt,digest=intent(conn,card,draft)
    try:
        rest("crm.item.update",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
            "fields":draft})
        after=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
        validate_identity(after,card)
        need(original_nine(after)==draft and
             secrets.compare_digest(compute_digest(draft,salt),digest) and
             non_ai_unchanged(fresh,after),
             "V23_EXACT_BITRIX_NINE_AND_NON_AI_READBACK")
        finish(conn,card,"VERIFIED")
        return "VERIFIED",facts,len(lines)
    except Exception:
        try:
            finish(conn,card,"UNCERTAIN")
        except Exception:
            pass
        return "UNCERTAIN",facts,0


def run(mode, hashes):
    need(len(hashes)==len(FILES) and
         all(re.fullmatch(r"[a-f0-9]{64}",x) for x in hashes),
         "V23_REVIEWED_HASH_ARGS")
    actual=[hashlib.sha256((Path("/app/leadpilot")/f).read_bytes()).hexdigest()
            for f in FILES]
    need(actual==hashes,"V23_LIVE_SOURCE_HASH_DRIFT")
    env=dict(os.environ)
    need(live_permitted(env) and INTERVAL_SECONDS==300 and
         env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED")=="YES" and
         env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")=="YES" and
         exact_environment(env).get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES",
         "V23_ACTIVE_SAFETY_GATES_MISSING")
    emit("EXACT_CODE_AND_GATES","YES")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn:
        if mode=="apply":
            conn.execute(DDL)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
            locked=bool(cur.fetchone()[0])
        need(locked,"V23_ANOTHER_WRITER_ACTIVE")
        try:
            originals=select_one(conn,preview=True)
            need(len(originals)==4,"V23_EXACT_FOUR_ORIGINALS_REQUIRED")
            emit("SIGNED_FOUR","YES")
            with httpx.Client(trust_env=False,follow_redirects=False) as client:
                rest=Rest(webhook,client)
                writes=0
                for i,row in enumerate(originals,1):
                    try:
                        state,nfacts,new=handle(
                            conn,client,rest,env,row,apply=mode=="apply")
                    except (ValueError,LookupError,TypeError,httpx.HTTPError):
                        state,nfacts,new="EVIDENCE_OR_IDENTITY_BLOCKED",0,0
                    emit(f"CARD_{i}_STATE",state)
                    emit(f"CARD_{i}_FACTS",min(99,nfacts))
                    emit(f"CARD_{i}_NEW_LINES",min(99,new))
                    if state=="UNCERTAIN":
                        emit("CRM_OUTCOME","UNVERIFIED")
                        return 2
                    if state=="VERIFIED":
                        writes+=1
                emit("CRM_VERIFIED_UPDATES",writes)
                emit("MODE","APPLY" if mode=="apply" else "PREVIEW")
            return 0
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


def main(argv=None):
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview",action="store_true")
    group.add_argument("--apply",action="store_true")
    p.add_argument("hashes",nargs=len(FILES))
    args=p.parse_args(argv)
    try:
        return run("apply" if args.apply else "preview",args.hashes)
    except Exception:
        emit("SAFE_ABORT","YES")
        return 2

if __name__=="__main__":
    raise SystemExit(main())
