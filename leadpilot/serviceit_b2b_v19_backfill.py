"""V19 one-use additive B2B evidence research for historical AND finished new cards.

Never replay initial intake, never replace V11 report, never change HOLD/STOP,
score, tasks, customer identity, contacts or any fields except 3 narrative AI.
Only independently quoted public source facts or CLEARLY unverified indexed
Brave leads may be appended. No FNS claim, no buyer outreach, no CAPTCHA.
An immutable PG receipt records attempts and prevents ambiguous external replay.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

import httpx

from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_dossier_v11 import original_nine, evidence_rows, tidy
from .serviceit_b2b_routerai_deep import source_only_investigation
from .serviceit_vps_b2b_deep_research import safe_notice, source_findings
from .serviceit_vps_b2b_dossier_v11_worker import (
    LOCK_ID, compute_digest, non_ai_unchanged,
)
from .serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint,
)
from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import live_permitted, exact_environment
from .bitrix_metadata_audit import validate_webhook

NAME = "B2B_DOSSIER_V19"
TABLE = "serviceit_vps_b2b_v19_research_receipts"
SUMMARY = PREFIX + "AI_SUMMARY"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
REPORT = PREFIX + "AI_FULL_REPORT"
STATUS = PREFIX + "AI_STATUS"
MAX_LINE = 280
MAX_FACTS = 6
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_v19_research_receipts (
    card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
    source_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'RESERVED','WRITE_INTENT','VERIFIED','NO_NEW_EVIDENCE',
      'INSUFFICIENT_CAPACITY','CONFLICT','UNCERTAIN')),
    before_fields JSONB NOT NULL,
    after_fields JSONB,
    salt BYTEA,
    digest BYTEA,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((salt IS NULL AND digest IS NULL) OR
      (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""


class V19Blocked(ValueError):
    pass


def need(ok, why):
    if not ok:
        raise V19Blocked(why)


def select_one(connection, *, preview=False):
    """Signed finished V11 cards only: four historical first, then new.

    Exclude current/newly created and previously V19 scanned/written cards.
    Require a reviewed, finished V11 receipt; do NOT replay V11 itself.
    """
    with connection.cursor() as cur:
        if preview:
            cur.execute("""
                SELECT b.card_id,b.source_id,b.notice_json
                FROM serviceit_vps_b2b_inbox b
                JOIN serviceit_vps_b2b_dossier_v11_receipts v
                  ON v.card_id=b.card_id AND v.status='VERIFIED'
                WHERE b.state='VERIFIED'
                ORDER BY b.received_at,b.source_id LIMIT 4
            """)
            return cur.fetchall()
        cur.execute(f"""
            SELECT b.card_id,b.source_id,b.notice_json
            FROM serviceit_vps_b2b_inbox b
            JOIN serviceit_vps_b2b_dossier_v11_receipts v
              ON v.card_id=b.card_id AND v.status='VERIFIED'
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
              AND b.received_at < now()-interval '20 minutes'
              AND NOT EXISTS (
                SELECT 1 FROM {TABLE} r
                WHERE r.card_id=b.card_id AND (
                  r.status NOT IN ('NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')
                  OR r.updated_at >= now()-interval '24 hours'
                ))
              -- A no-evidence result is NOT permanent completion. Retry only
              -- after >=24h. Already VERIFIED/UNCERTAIN/CONFLICT receipts
              -- remain immutable and can never be blindly re-applied.
              -- A prior unrelated AI summary write is allowed to remain
              -- unresolved; never touch or replay it. Skip only its card,
              -- so it cannot starve all following pending B2B dossiers.
              AND NOT EXISTS (
                SELECT 1 FROM serviceit_vps_native_receipts n
                WHERE n.card_id=b.card_id
                  AND n.status IN ('RESERVED','WRITE_INTENT')
              )
            ORDER BY b.received_at,b.source_id LIMIT 1
        """)
        return cur.fetchone()


def novel_evidence(findings, previous_report):
    """Extract bounded source-cited new facts; index is NEVER verified."""
    need(type(previous_report) is str, "BASE_REPORT_INVALID")
    found = []
    docs, cited = evidence_rows(findings, source_only_investigation(findings))
    # Keep independently read quotations FIRST and source URL unchanged.
    for row in cited[:MAX_FACTS]:
        quote = tidy(row.get("quote"), MAX_LINE)
        url = row.get("url")
        if not quote or type(url) is not str or quote in previous_report:
            continue
        found.append("ПРОЧИТАННЫЙ ИСТОЧНИК / " + row["category"] +
                     ": " + quote + " [" + url + "]")
    indexed = (findings.get("indexed_candidates") or []) if type(findings) is dict else []
    for entry in indexed[:3]:
        if type(entry) is not dict or entry.get("evidence_level") != "INDEX_ONLY_UNVERIFIED":
            continue
        url = entry.get("url")
        if type(url) is not str or not url.startswith("https://") or len(url)>950:
            continue
        platform = entry.get("title_match") == "EXACT_ID_AND_TOPIC_AND_PLATFORM"
        for fact in (entry.get("claims") or [])[:MAX_FACTS]:
            if type(fact) is not dict or fact.get("category") not in (
                    "BUYER","INN","DEADLINE","LOCATION","BUDGET"):
                continue
            quote = tidy(fact.get("quote"),MAX_LINE)
            if not quote or quote in previous_report:
                continue
            found.append(
                "ИНДЕКС BRAVE / " + fact["category"] +
                (" (ИСТОЧНИК НЕ ПРОЧИТАН, НЕ ФНС): " if platform else
                 " (НЕ ПРОВЕРЕН ИСТОЧНИК И ПЛОЩАДКА): ") +
                quote + " [" + url + "]")
            if len(found)>=MAX_FACTS:
                break
        if len(found)>=MAX_FACTS:
            break
    # Never publish the same indexed snippet twice or fake price=0.
    return list(dict.fromkeys(found))[:MAX_FACTS]


def prepare_addendum(before, items, tender_id):
    """Preserve full old report, prepend meaningful small manager highlights."""
    need(type(before) is dict and set(before)==AI_FIELDS
         and all(type(v) is str and v.strip() for v in before.values())
         and re.fullmatch(r"\d{5,12}",tender_id), "BEFORE_NINE_INVALID")
    if not isinstance(items,list) or not items:
        return None
    safe = [x for x in items[:MAX_FACTS] if type(x) is str
            and 20 <= len(x) <= 1350 and x not in before[REPORT]]
    if not safe:
        return None
    section = (
        "\n\n09  ДОПОЛНЕНИЕ V19 ПО НЕЗАВИСИМЫМ ИСТОЧНИКАМ — №"
        + tender_id + "\n"
        + "Прочитанные страницы и находки Brave имеют РАЗНЫЙ уровень доверия. "
        + "Индексные выписки требуют проверки на исходной площадке; "
        + "не являются ФНС, доказательством цены или разрешением на контакт.\n"
        + "\n".join(safe)
    )
    summary = ("ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ (указаны источники, индекс требует "
               "подтверждения): " + " | ".join(safe[:2]) + "\n")
    brief = ("ОБНОВЛЕНИЕ ПО ТЕНДЕРУ №" + tender_id +
             " — проверить найденные данные, не подменять ими документы:\n" +
             "\n".join(safe[:3]) + "\n")
    result = dict(before)
    # Do not chop verified historical dossier/manager edits to fit.
    if (len(before[REPORT])+len(section)>12000 or
            len(before[SUMMARY])+len(summary)>4096 or
            len(before[BRIEF])+len(brief)>4096):
        return None
    result[REPORT] = before[REPORT]+section
    result[SUMMARY] = summary+before[SUMMARY]
    result[BRIEF] = brief+before[BRIEF]
    need(set(result)==AI_FIELDS
         and all(type(v) is str and v.strip() and
                 len(v)<=(12000 if k==REPORT else 4096)
                 for k,v in result.items())
         and all(result[k]==before[k]
                 for k in AI_FIELDS if k not in (REPORT,SUMMARY,BRIEF)),
         "ADDENDUM_ONLY_THREE_FIELDS")
    return result


def active_card_write(conn, card):
    with conn.cursor() as cur:
        cur.execute("""
           SELECT count(*) FROM serviceit_vps_native_receipts
           WHERE card_id=%s AND status IN ('RESERVED','WRITE_INTENT')
        """,(card,))
        count=int(cur.fetchone()[0])
        for table in ("serviceit_vps_b2b_dossier_v11_receipts",
                      "serviceit_vps_b2b_tender_doc_receipts"):
            cur.execute("SELECT to_regclass(%s)",("public."+table,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute("SELECT count(*) FROM "+table+
                        " WHERE card_id=%s AND status IN "
                        "('RESERVED','WRITE_INTENT','AI_INTENT')",(card,))
            count+=int(cur.fetchone()[0])
    return count>0


def reserve(conn, card, source, before):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {TABLE}(card_id,source_hash,status,before_fields)
                VALUES (%s,%s,'RESERVED',%s::jsonb)
                ON CONFLICT(card_id) DO UPDATE SET
                status='RESERVED',
                before_fields=EXCLUDED.before_fields,
                source_hash=EXCLUDED.source_hash,
                after_fields=NULL,salt=NULL,digest=NULL,
                updated_at=now()
              WHERE {TABLE}.source_hash=EXCLUDED.source_hash
                AND {TABLE}.status IN (
                  'NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')
                AND {TABLE}.updated_at < now()-interval '24 hours'
              RETURNING card_id
            """,(card,hashlib.sha256(source.encode("ascii")).hexdigest(),
                  json.dumps(before,ensure_ascii=False)))
            return cur.fetchone() is not None


def move(conn, card, before, after, *, desired=None):
    with conn.transaction():
        with conn.cursor() as cur:
            if desired is not None:
                salt=secrets.token_bytes(16)
                digest=compute_digest(desired,salt)
                cur.execute(f"""
                    UPDATE {TABLE}
                    SET status='WRITE_INTENT',after_fields=%s::jsonb,
                        salt=%s,digest=%s,updated_at=now()
                    WHERE card_id=%s AND status='RESERVED'
                    RETURNING card_id
                """,(json.dumps(desired,ensure_ascii=False),salt,digest,card))
                need(cur.fetchone() is not None,"DURABLE_WRITE_INTENT_REQUIRED")
                return salt,digest
            cur.execute(f"""
                UPDATE {TABLE} SET status=%s,updated_at=now()
                WHERE card_id=%s AND status=%s RETURNING card_id
            """,(after,card,before))
            need(cur.fetchone() is not None,"RECEIPT_PHASE_DRIFT")


def run_once(env, client, conn, *, preview=False):
    """Execute bounded proof/readback. Max one future/historical card per cycle."""
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")=="YES",
         "V19_LIVE_GATES_MISSING")
    exact=exact_environment(env)
    if not preview:
        conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
        locked=bool(cur.fetchone()[0])
    if not locked:
        return {"status":"V19_OTHER_DOSSIER_WORKER_ACTIVE","crm_writes":0}
    try:
        records=select_one(conn,preview=preview)
        rows=records if preview else ([records] if records else [])
        if not rows:
            return {"status":"V19_NO_ELIGIBLE_CARDS","crm_writes":0}
        webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest=Rest(webhook,client)
        stats=[]
        for card,source,notice in rows[:4]:
            if active_card_write(conn,card):
                stats.append({"status":"V19_SAME_CARD_WRITE_IN_FLIGHT","crm_writes":0})
                continue
            first=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
            validate_identity(first,card)
            url,title=safe_notice(notice,card,source,first)
            need(url==notice["source_url"] and bool(title),
                 "ORIGINAL_NOTICE_CARD_MISMATCH")
            before=original_nine(first)
            findings = {}
            try:
                findings=source_findings(first,notice,exact,client,enable_v18=True)
                items=novel_evidence(findings,before[REPORT])
                draft=prepare_addendum(before,items,notice["tender_id"])
            except Exception:
                # A transport/policy failure never authorizes a fabricated CRM
                # fact. Keep the card intact; no paid immediate retry.
                items=[]
                draft=None
            if preview:
                stats.append({
                    "status":"V19_PREVIEW",
                    "crm_writes":0,
                    "new_cited_lines":min(len(items),MAX_FACTS),
                    "new_3_field_draft":bool(draft),
                    "verified_public_pages":min(
                        len((findings or {}).get("pages") or []),9)
                        ,
                })
                continue
            if not reserve(conn,card,source,before):
                return {"status":"V19_ALREADY_FENCED_NO_RETRY","crm_writes":0}
            if draft is None:
                move(conn,card,"RESERVED",
                     "NO_NEW_EVIDENCE" if not items else "INSUFFICIENT_CAPACITY")
                return {"status":"V19_NO_NEW_EVIDENCE_OR_CAPACITY",
                        "crm_writes":0}
            fresh=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
            validate_identity(fresh,card)
            if fingerprint(fresh)!=fingerprint(first):
                move(conn,card,"RESERVED","CONFLICT")
                return {"status":"V19_CRM_CHANGED_NO_RETRY","crm_writes":0}
            salt,digest=move(conn,card,"RESERVED","WRITE_INTENT",desired=draft)
            try:
                rest("crm.item.update",{
                    "entityTypeId":1062,"id":card,
                    "useOriginalUfNames":"Y","fields":draft,
                })
                after=normalized(rest("crm.item.get",{
                    "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                validate_identity(after,card)
                need(original_nine(after)==draft and
                     secrets.compare_digest(compute_digest(draft,salt),digest)
                     and non_ai_unchanged(fresh,after),
                     "V19_EXACT_NINE_READBACK_FAILED")
                move(conn,card,"WRITE_INTENT","VERIFIED")
                return {"status":"V19_ADDITIVE_NINE_READBACK_VERIFIED",
                        "crm_writes":1,"source_lines":min(len(items),MAX_FACTS)}
            except Exception:
                try:
                    move(conn,card,"WRITE_INTENT","UNCERTAIN")
                except Exception:
                    pass
                return {"status":"V19_UNCERTAIN_NO_RETRY",
                        "crm_writes":"UNVERIFIED"}
        if preview:
            return {"status":"V19_FOUR_READONLY_PREVIEW","crm_writes":0,
                    "previewed":len(stats),
                    "results":stats}
        return {"status":"V19_ALL_CARDS_BLOCKED","crm_writes":0}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))
