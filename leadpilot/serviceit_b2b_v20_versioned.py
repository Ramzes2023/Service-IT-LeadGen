"""V20: immutable versioned evidence follow-up AFTER a VERIFIED V19 supplement.

The first V19 VERIFIED receipt is never rewritten. Every V20 investigation gets
its own PostgreSQL attempt row. Only 3 narrative fields may gain source-cited
addenda; no changes to stages, contact, company INN, score, HOLD, tasks or mail.
A confirmed write is never blindly replayed. HTTPS/robots and original signed
B2B identity checks are delegated to the existing reviewed research pipeline.

This module is OFF by default and requires an explicit VPS opt-in. It is not
a bypass for blocked pages, paid APIs or authentication.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from .serviceit_b2b_hold_ai_fields import PREFIX
from .serviceit_b2b_v19_backfill import (
    REPORT, SUMMARY, BRIEF, V19Blocked, need, novel_evidence,
    prepare_addendum, active_card_write,
)
from .serviceit_b2b_dossier_v11 import (
    original_nine, confirmed_passport_values, sync_confirmed_passport,
)
from .serviceit_b2b_routerai_deep import source_only_investigation
from .serviceit_enrichment_worker import Rest, normalized, validate_identity, fingerprint
from .serviceit_vps_b2b_deep_research import safe_notice, source_findings
from .serviceit_vps_b2b_dossier_v11_worker import (
    LOCK_ID, compute_digest, non_ai_unchanged,
)
from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import live_permitted, exact_environment
from .bitrix_metadata_audit import validate_webhook

TABLE = "serviceit_vps_b2b_v20_revision_receipts"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_v20_revision_receipts (
 card_id BIGINT NOT NULL CHECK (card_id BETWEEN 8 AND 2000000000),
 attempt_no BIGINT NOT NULL CHECK (attempt_no BETWEEN 1 AND 1000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK (status IN (
  'RESERVED','WRITE_INTENT','VERIFIED','NO_NEW_EVIDENCE',
  'INSUFFICIENT_CAPACITY','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(card_id, attempt_no),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""


def selected_one(conn):
    """Only historically signed V19 VERIFIED cards; no uncertain V20 replay.

    A VERIFIED V20 card is re-investigated no more than once per 72h,
    an inconclusive card no more than once per 24h. Failed external writes
    or identity conflicts freeze that card for human reconciliation.
    """
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT b.card_id,b.source_id,b.notice_json
            FROM serviceit_vps_b2b_inbox b
            JOIN serviceit_vps_b2b_dossier_v11_receipts v
              ON v.card_id=b.card_id AND v.status='VERIFIED'
            JOIN serviceit_vps_b2b_v19_research_receipts r
              ON r.card_id=b.card_id AND r.status='VERIFIED'
            LEFT JOIN LATERAL (
              SELECT z.status,z.updated_at,z.source_hash
              FROM {TABLE} z WHERE z.card_id=b.card_id
              ORDER BY z.attempt_no DESC LIMIT 1
            ) last ON true
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
              AND b.received_at < now()-interval '24 hours'
              AND (
                last.status IS NULL
                OR (last.status='VERIFIED'
                    AND last.updated_at < now()-interval '72 hours')
                OR (last.status IN ('NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')
                    AND last.updated_at < now()-interval '24 hours')
              )
              AND (last.source_hash IS NULL OR
                   last.source_hash=r.source_hash)
              AND NOT EXISTS (
                SELECT 1 FROM serviceit_vps_native_receipts n
                WHERE n.card_id=b.card_id
                  AND n.status IN ('RESERVED','WRITE_INTENT')
              )
            ORDER BY COALESCE(last.updated_at,b.received_at),
                     b.received_at,b.source_id LIMIT 1
        """)
        return cur.fetchone()


def versioned_addendum(before, findings, tender_id):
    """All old AI text survives verbatim, and the new section has V20 label."""
    lines = novel_evidence({**findings, "indexed_candidates": []}, before[REPORT])
    draft = prepare_addendum(before, lines, tender_id)
    values = confirmed_passport_values(findings, source_only_investigation(findings))
    if draft is None:
        synchronized = sync_confirmed_passport(before, values)
        return (synchronized if synchronized != before else None), len(lines)
    suffix = draft[REPORT][len(before[REPORT]):]
    marker = "09  ДОПОЛНЕНИЕ V19"
    need(suffix.startswith("\n\n" + marker), "V20_APPEND_BOUNDARY_INVALID")
    draft[REPORT] = before[REPORT] + suffix.replace(
        marker, "13  ВЕРСИОННОЕ ДОПОЛНЕНИЕ V20", 1)
    if len(draft[REPORT]) > 12000:
        return None, len(lines)
    need(draft[REPORT].startswith(before[REPORT]), "V20_OLD_REPORT_CHANGED")
    for key in (SUMMARY, BRIEF):
        need(draft[key].endswith(before[key]), "V20_OLD_HIGHLIGHT_CHANGED")
    return sync_confirmed_passport(draft, values), len(lines)


def reserve(conn, card, source_hash, before):
    """Insert a NEW history row, never overwrite earlier VERIFIED receipts."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {TABLE}
                    (card_id,attempt_no,source_hash,status,before_fields)
                SELECT %s,COALESCE(MAX(attempt_no),0)+1,%s,
                       'RESERVED',%s::jsonb
                FROM {TABLE} WHERE card_id=%s
                RETURNING attempt_no
            """, (card, source_hash, json.dumps(before,ensure_ascii=False),card))
            row=cur.fetchone()
            need(row is not None, "V20_RECEIPT_NOT_RESERVED")
            return int(row[0])


def transition(conn, card, attempt, before, after, *, desired=None):
    with conn.transaction():
        with conn.cursor() as cur:
            if desired is None:
                cur.execute(f"""
                    UPDATE {TABLE} SET status=%s,updated_at=now()
                    WHERE card_id=%s AND attempt_no=%s AND status=%s
                    RETURNING attempt_no
                """, (after,card,attempt,before))
                need(cur.fetchone() is not None, "V20_RECEIPT_PHASE_DRIFT")
                return None
            salt=secrets.token_bytes(16)
            digest=compute_digest(desired,salt)
            cur.execute(f"""
                UPDATE {TABLE}
                SET status='WRITE_INTENT',after_fields=%s::jsonb,
                    salt=%s,digest=%s,updated_at=now()
                WHERE card_id=%s AND attempt_no=%s AND status='RESERVED'
                RETURNING attempt_no
            """, (json.dumps(desired,ensure_ascii=False),
                  salt,digest,card,attempt))
            need(cur.fetchone() is not None, "V20_INTENT_MUST_BE_DURABLE")
            return salt,digest


def run_once(env, client, conn, *, search_scope=None):
    """One bounded history scan per enabled cycle; NEVER create a CRM card."""
    if env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED") != "YES":
        return {"status":"V20_OFF","crm_writes":0}
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")=="YES",
         "V20_LIVE_GATES_MISSING")
    exact=exact_environment(env)
    conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
        locked=bool(cur.fetchone()[0])
    if not locked:
        return {"status":"V20_OTHER_RESEARCH_ACTIVE","crm_writes":0}
    try:
        row=selected_one(conn)
        if not row:
            return {"status":"V20_NO_ELIGIBLE_VERIFIED_CARD","crm_writes":0}
        card,source,notice=row
        src_hash=hashlib.sha256(source.encode("ascii")).hexdigest()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source_hash FROM serviceit_vps_b2b_v19_research_receipts
                WHERE card_id=%s AND status='VERIFIED'
            """,(card,))
            prior=cur.fetchone()
        need(prior is not None and prior[0]==src_hash,
             "V20_PREVIOUS_VERIFIED_SOURCE_MISMATCH")
        rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        validate_webhook(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"))
        first=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
        validate_identity(first,card)
        url,title=safe_notice(notice,card,source,first)
        need(url==notice["source_url"] and bool(title),
             "V20_SIGNED_NOTICE_MISMATCH")
        before=original_nine(first)
        try:
            from contextlib import nullcontext
            with (search_scope(card,source) if search_scope else nullcontext()):
                findings=source_findings(first,notice,exact,client,enable_v18=True,
                                         connection=conn)
            draft,count=versioned_addendum(before,findings,notice["tender_id"])
        except Exception:
            # No invented facts, no repeated immediate paid queries.
            draft,count=None,0
        if active_card_write(conn,card):
            return {"status":"V20_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
        fresh=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
        validate_identity(fresh,card)
        if fingerprint(first)!=fingerprint(fresh):
            return {"status":"V20_PREWRITE_CRM_CHANGED","crm_writes":0}
        attempt=reserve(conn,card,src_hash,before)
        if draft is None:
            transition(conn,card,attempt,"RESERVED",
                       "NO_NEW_EVIDENCE" if not count
                       else "INSUFFICIENT_CAPACITY")
            return {"status":"V20_NO_NEW_EVIDENCE_OR_CAPACITY","crm_writes":0}
        salt,digest=transition(
            conn,card,attempt,"RESERVED","WRITE_INTENT",desired=draft)
        try:
            rest("crm.item.update",{
                "entityTypeId":1062,"id":card,
                "useOriginalUfNames":"Y","fields":draft})
            after=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
            validate_identity(after,card)
            need(original_nine(after)==draft and
                 secrets.compare_digest(compute_digest(draft,salt),digest)
                 and non_ai_unchanged(fresh,after),
                 "V20_NINE_AND_NON_AI_READBACK_FAILED")
            transition(conn,card,attempt,"WRITE_INTENT","VERIFIED")
            return {"status":"V20_VERSIONED_ADDITIVE_VERIFIED",
                    "crm_writes":1,"source_lines":min(count,6)}
        except Exception:
            try:
                transition(conn,card,attempt,"WRITE_INTENT","UNCERTAIN")
            except Exception:
                pass
            return {"status":"V20_UNCERTAIN_NO_REPLAY","crm_writes":"UNVERIFIED"}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))
