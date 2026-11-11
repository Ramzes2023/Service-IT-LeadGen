#!/usr/bin/env python3
"""One-use verified official CBR legal identity addendum for SIGNED card 11 only.

Adds official Moscow Exchange INN/OGRN and CBR citation solely to three
existing narrative AI fields, not legal/score/contact/STOP/stage fields.
One official free TinyFish Fetch call, no paid models or B2B scraping.
Durable PostgreSQL WRITE_INTENT before Bitrix REST, exact 9-field and all
non-AI readback after. UNKNOWN result is UNCERTAIN and is NEVER retried.
No private customer text or tokens in log.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import (
    select_one,prepare_addendum,active_card_write,REPORT,SUMMARY,BRIEF,
)
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import (
    LOCK_ID,compute_digest,non_ai_unchanged,
)
from leadpilot.serviceit_enrichment_worker import (
    Rest,normalized,validate_identity,fingerprint,
)
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.bitrix_metadata_audit import validate_webhook

TENDER="4673971"
CARD=11
INN="7702077840"
OGRN="1027739387411"
URL="https://www.cbr.ru/registries/rcb/ecb/orginfo/?id=11123764"
FETCH="https://api.fetch.tinyfish.ai"
TABLE="serviceit_vps_b2b_cbr_official_v1_receipts"
CITED_LINE=(
    "ОФИЦИАЛЬНЫЙ РЕЕСТР БАНКА РОССИИ / ПАО МОСКОВСКАЯ БИРЖА: "
    "ИНН "+INN+", ОГРН "+OGRN+
    "; юридическое лицо проверено в публичном реестре эмитентов, "
    "но это не выписка ФНС и не подтверждение суммы закупки. ["+URL+"]"
)
DDL="""
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_cbr_official_v1_receipts (
    card_id BIGINT PRIMARY KEY CHECK(card_id=11),
    tender_id TEXT NOT NULL CHECK(tender_id='4673971'),
    source_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK(status IN
        ('RESERVED','WRITE_INTENT','VERIFIED','UNCERTAIN','CONFLICT')),
    before_fields JSONB NOT NULL,
    after_fields JSONB,
    salt BYTEA,
    digest BYTEA,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((salt IS NULL AND digest IS NULL) OR
           (octet_length(salt)=16 AND octet_length(digest)=32))
)
"""


class OneUseBlocked(ValueError):
    pass


def need(ok,reason):
    if not ok:
        raise OneUseBlocked(reason)


def emit(k,v):
    need(re.fullmatch(r"[A-Z0-9_]{3,65}",k) is not None,"LOG_KEY_INVALID")
    need(re.fullmatch(r"(?:YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,85})",
                      str(v)) is not None,"LOG_VALUE_INVALID")
    print("CBR_ONEUSE_"+k+"="+str(v),flush=True)


def official_fact(client,key):
    """Network evidence is never built from index snippets."""
    r=client.post(
        FETCH,
        json={"urls":[URL],"format":"markdown","links":False,
              "image_links":False,"ttl":0,"per_url_timeout_ms":17000},
        headers={"X-API-Key":key,"Accept":"application/json"},
        timeout=39,follow_redirects=False)
    if (r.status_code!=200 or len(r.content)>300000
        or "application/json" not in r.headers.get("content-type","")):
        return False
    d=r.json()
    rows=d.get("results") if isinstance(d,dict) else None
    if not isinstance(rows,list) or len(rows)!=1:
        return False
    obj=rows[0]
    txt=obj.get("text") if isinstance(obj,dict) else None
    if (not isinstance(txt,str) or not 400<len(txt)<110000
        or obj.get("url")!=URL or obj.get("final_url")!=URL):
        return False
    # Require both legal identifiers and the exact issuer within SAME
    # opening emitter identity section, not an unrelated table footer.
    head=txt[:6500]
    return (INN in head and OGRN in head
            and "ПАО Московская Биржа" in head
            and "Московская Биржа ММВБ-РТС" in head)


def current_item(rest):
    x=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y"})["item"])
    validate_identity(x,CARD)
    return x


def active_conflicts(conn):
    if active_card_write(conn,CARD):
        return True
    with conn.cursor() as cur:
        for table in ("serviceit_vps_b2b_v19_research_receipts",
                      "serviceit_vps_b2b_v20_revision_receipts",
                      "serviceit_vps_b2b_v23_primary_source_receipts",
                      TABLE):
            cur.execute("SELECT to_regclass(%s)",("public."+table,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute("SELECT count(*) FROM "+table+
                        " WHERE card_id=%s AND status IN "
                        "('RESERVED','WRITE_INTENT','UNCERTAIN','CONFLICT')",
                        (CARD,))
            if int(cur.fetchone()[0])>0:
                return True
    return False


def mark(conn,before,after,*,desired=None):
    with conn.transaction():
        with conn.cursor() as cur:
            if desired is None:
                cur.execute("UPDATE "+TABLE+
                    " SET status=%s,updated_at=now() "
                    " WHERE card_id=%s AND status=%s RETURNING card_id",
                    (after,CARD,before))
                need(cur.fetchone() is not None,"RECEIPT_PHASE_DRIFT")
                return None
            salt=secrets.token_bytes(16)
            digest=compute_digest(desired,salt)
            cur.execute("UPDATE "+TABLE+
                " SET status='WRITE_INTENT',after_fields=%s::jsonb,"
                " salt=%s,digest=%s,updated_at=now() "
                " WHERE card_id=%s AND status='RESERVED' RETURNING card_id",
                (json.dumps(desired,ensure_ascii=False),salt,digest,CARD))
            need(cur.fetchone() is not None,"DURABLE_INTENT_REQUIRED")
            return salt,digest


def execute():
    env=dict(os.environ)
    need(live_permitted(env) and INTERVAL_SECONDS==300
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_TINYFISH_API_KEY_FILE")
             =="/run/secrets/serviceit_tinyfish_key"
         and env.get("SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED")=="YES"
         and env.get("SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED")=="YES"
         and env.get("SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED")=="NO",
         "LIVE_CRM_OR_SOURCE_GATES_MISSING")
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=40) as client:
      with db(env) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
            locked=bool(cur.fetchone()[0])
        if not locked:
            emit("OUTCOME","OTHER_WRITER_ACTIVE")
            return 0
        try:
            original=select_one(conn,preview=True)
            need(len(original)==4,"ORIGINAL_SIGNED_FOUR_MISSING")
            card,source,notice=original[0]
            need(card==CARD and source=="B2B-CENTER-"+TENDER
                 and notice.get("source")=="B2B-Center"
                 and notice.get("tender_id")==TENDER
                 and "сервер" in notice.get("subject","").casefold(),
                 "SIGNED_TENDER_ID_MISMATCH")
            rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
            validate_webhook(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"))
            first=current_item(rest)
            url,title=safe_notice(notice,CARD,source,first)
            need(url==notice["source_url"] and bool(title),
                 "SIGNED_BITRIX_IDENTITY_MISMATCH")
            before=original_nine(first)
            buyer_blob=json.dumps(notice,ensure_ascii=False).casefold()
            need("московская биржа" in buyer_blob or
                 "московская биржа" in before[REPORT].casefold(),
                 "BUYER_SOURCE_NAME_MISMATCH")
            need(not active_conflicts(conn),"PENDING_OR_UNCERTAIN_WRITE")
            if INN in before[REPORT] and OGRN in before[REPORT]:
                emit("OUTCOME","ALREADY_IN_DOSSIER")
                emit("CRM_WRITES",0)
                return 0
            key=secret_file(env,"SERVICEIT_TINYFISH_API_KEY",optional=True)
            need(type(key) is str and 16<=len(key)<=256,
                 "TINYFISH_KEY_NOT_AVAILABLE")
            need(official_fact(client,key),
                 "CBR_OFFICIAL_FULL_PAGE_NOT_VERIFIED")
            emit("CBR_OFFICIAL_PAGE","VERIFIED")
            draft=prepare_addendum(before,[CITED_LINE],TENDER)
            if not draft:
                emit("OUTCOME","NO_DOSSIER_CAPACITY")
                emit("CRM_WRITES",0)
                return 0
            fresh=current_item(rest)
            need(fingerprint(fresh)==fingerprint(first),
                 "CRM_SNAPSHOT_CHANGED")
            need(not active_conflicts(conn),"CONCURRENT_WRITE_GUARD")
            with conn.transaction():
                conn.execute(DDL)
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO "+TABLE+
                        " (card_id,tender_id,source_hash,status,before_fields)"
                        " VALUES (%s,%s,%s,'RESERVED',%s::jsonb)"
                        " ON CONFLICT(card_id) DO NOTHING RETURNING card_id",
                        (CARD,TENDER,hashlib.sha256(source.encode("ascii")).hexdigest(),
                         json.dumps(before,ensure_ascii=False)))
                    inserted=cur.fetchone() is not None
            if not inserted:
                emit("OUTCOME","ONEUSE_ALREADY_FENCED")
                emit("CRM_WRITES",0)
                return 0
            salt,digest=mark(conn,"RESERVED","WRITE_INTENT",desired=draft)
            try:
                rest("crm.item.update",{
                    "entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y",
                    "fields":draft})
                after=current_item(rest)
                need(original_nine(after)==draft
                     and secrets.compare_digest(
                         compute_digest(draft,salt),digest)
                     and non_ai_unchanged(fresh,after),
                     "EXACT_NINE_AND_NONAI_READBACK_FAILED")
                mark(conn,"WRITE_INTENT","VERIFIED")
                emit("OUTCOME","OFFICIAL_CBR_ADDITIVE_VERIFIED")
                emit("CRM_WRITES",1)
                emit("ONLY_THREE_AI_FIELDS","YES")
                return 0
            except Exception:
                try:
                    mark(conn,"WRITE_INTENT","UNCERTAIN")
                except Exception:
                    pass
                emit("OUTCOME","UNCERTAIN_NO_REPLAY")
                emit("CRM_WRITES","UNVERIFIED")
                return 2
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


if __name__=="__main__":
    try:
        raise SystemExit(execute())
    except Exception as error:
        code=str(error) if isinstance(error,OneUseBlocked) else "BLOCKED"
        if re.fullmatch(r"[A-Z0-9_]{3,85}",code) is None:
            code="BLOCKED"
        emit("SAFE_BLOCK",code)
        emit("CRM_WRITES","NO")
        raise SystemExit(2)
