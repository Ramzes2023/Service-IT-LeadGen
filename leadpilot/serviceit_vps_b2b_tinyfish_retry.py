"""V35: durable, rate-limited TinyFish research retries independent of V19/V20.

One approved public Search/Fetch research attempt for one signed V11 card per
existing five-minute coordinator cycle. Includes V19/V20 VERIFIED cards.
Only PostgreSQL audit receipts are written: NEVER Bitrix, score, tasks, mail,
source notices, AI fields or private customer data. No auth/CAPTCHA bypass.
"""
from __future__ import annotations

import hashlib
import re

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_free_public_fallback import (
    ENABLE, T_EN, B_EN, read_free_public_mirror,
)
from .serviceit_b2b_hold_ai_fields import PREFIX
from .serviceit_enrichment_worker import Rest, normalized, validate_identity
from .serviceit_vps_b2b_deep_research import safe_notice
from .serviceit_vps_b2b_dossier_v11_worker import LOCK_ID
from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import live_permitted

TABLE = "serviceit_vps_b2b_tinyfish_retry_attempts"
ALLOWED_RESULTS = frozenset((
    "VERIFIED_PUBLIC_PAGE", "NO_VERIFIED_PUBLIC_PAGE",
    "PROVIDER_EXCEPTION", "IDENTITY_CONFLICT",
))
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_tinyfish_retry_attempts (
    card_id BIGINT NOT NULL CHECK(card_id BETWEEN 8 AND 2000000000),
    attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 100000),
    source_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'RESERVED','VERIFIED_PUBLIC_PAGE','NO_VERIFIED_PUBLIC_PAGE',
      'PROVIDER_EXCEPTION','IDENTITY_CONFLICT')),
    public_pages INTEGER NOT NULL DEFAULT 0
      CHECK(public_pages BETWEEN 0 AND 2),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY(card_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS serviceit_vps_b2b_tinyfish_retry_due
  ON serviceit_vps_b2b_tinyfish_retry_attempts
  (card_id, attempted_at DESC);
"""


def permitted(env):
    """The operator's existing public-site approval is the only opt-in."""
    return (
        isinstance(env, dict)
        and live_permitted(env)
        and env.get(ENABLE) == "YES"
        and (env.get(T_EN) == "YES" or env.get(B_EN) == "YES")
        and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
        and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21"
        and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"
    )


def due_row(conn):
    """One older, signed, V11-VERIFIED card, regardless of V19/V20 receipts.

    Never retry unresolved/crashed attempts; keep those for manual
    reconciliation. No default loop over historic CRM data.
    """
    with conn.cursor() as cur:
        cur.execute(f"""
          SELECT b.card_id,b.source_id,b.notice_json,last.source_hash
          FROM serviceit_vps_b2b_inbox b
          JOIN serviceit_vps_b2b_dossier_v11_receipts v
            ON v.card_id=b.card_id AND v.status='VERIFIED'
          LEFT JOIN LATERAL (
            SELECT a.status,a.source_hash,a.attempted_at
            FROM {TABLE} a WHERE a.card_id=b.card_id
            ORDER BY a.attempt_no DESC LIMIT 1
          ) last ON TRUE
          WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
            AND b.received_at < now()-interval '20 minutes'
            AND (
              last.status IS NULL
              OR (last.status IN (
                    'NO_VERIFIED_PUBLIC_PAGE','PROVIDER_EXCEPTION')
                  AND last.attempted_at < now()-interval '24 hours')
              OR (last.status='VERIFIED_PUBLIC_PAGE'
                  AND last.attempted_at < now()-interval '72 hours')
            )
            AND NOT EXISTS (
              SELECT 1 FROM serviceit_vps_native_receipts n
              WHERE n.card_id=b.card_id
                AND n.status IN ('RESERVED','WRITE_INTENT')
            )
          ORDER BY COALESCE(last.attempted_at,b.received_at),
                   b.received_at,b.source_id
          LIMIT 1
        """)
        return cur.fetchone()


def reserve(conn, card, digest):
    """Durably reserve BEFORE any external site or CRM GET."""
    if (type(card) is not int or not 8 <= card <= 2000000000
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise ValueError("TINYFISH_RETRY_SOURCE_INVALID")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COALESCE(MAX(attempt_no),0)+1 FROM {TABLE} "
                "WHERE card_id=%s", (card,))
            attempt = int(cur.fetchone()[0])
            if not 1 <= attempt <= 100000:
                raise ValueError("TINYFISH_RETRY_ATTEMPT_LIMIT")
            cur.execute(f"""
              INSERT INTO {TABLE}(card_id,attempt_no,source_hash,status)
              VALUES (%s,%s,%s,'RESERVED')
            """, (card, attempt, digest))
            return attempt


def finalize(conn, card, attempt, status, pages):
    if (status not in ALLOWED_RESULTS or type(pages) is not int
            or not 0 <= pages <= 2):
        raise ValueError("TINYFISH_RETRY_STATUS_INVALID")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
              UPDATE {TABLE}
              SET status=%s,public_pages=%s,finished_at=now()
              WHERE card_id=%s AND attempt_no=%s AND status='RESERVED'
              RETURNING attempt_no
            """, (status, pages, card, attempt))
            if cur.fetchone() is None:
                raise ValueError("TINYFISH_RETRY_RESULT_UNCERTAIN")


def run_once(env, client, conn):
    """One independent bounded provider stage per 5-minute native cycle."""
    if env.get("SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED") != "YES":
        return {"status": "TINYFISH_RETRY_DISABLED", "crm_writes": 0}
    if not permitted(env):
        return {"status": "TINYFISH_RETRY_POLICY_BLOCKED", "crm_writes": 0}
    # Idempotent schema migration. Only owns its new PG attempt table.
    with conn.transaction():
        conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
        locked = bool(cur.fetchone()[0])
    if not locked:
        return {"status": "TINYFISH_RETRY_OTHER_WORKER_ACTIVE",
                "crm_writes": 0}
    try:
        row = due_row(conn)
        if row is None:
            return {"status": "TINYFISH_RETRY_NOT_DUE", "crm_writes": 0}
        card, source, notice, previous_digest = row
        if (type(source) is not str
                or re.fullmatch(r"B2B-CENTER-\d{5,12}", source) is None):
            return {"status": "TINYFISH_RETRY_INVALID_SIGNED_SOURCE",
                    "crm_writes": 0}
        digest = hashlib.sha256(source.encode("ascii")).hexdigest()
        if previous_digest is not None and previous_digest != digest:
            return {"status": "TINYFISH_RETRY_SOURCE_CHANGED_NO_REPLAY",
                    "crm_writes": 0}
        attempt = reserve(conn, card, digest)
        # The CRM identity check is read-only. It prevents sending any
        # untrusted user-created heading or foreign notice to the provider.
        try:
            webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
            validate_webhook(webhook)
            rest = Rest(webhook, client)
            first = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card,
                "useOriginalUfNames": "Y"})["item"])
            validate_identity(first, card)
            url, title = safe_notice(notice, card, source, first)
            if url != notice.get("source_url") or not title:
                raise ValueError("TINYFISH_RETRY_SIGNED_IDENTITY_CONFLICT")
        except Exception:
            finalize(conn, card, attempt, "IDENTITY_CONFLICT", 0)
            return {"status": "TINYFISH_RETRY_IDENTITY_CONFLICT",
                    "crm_writes": 0}
        try:
            result = read_free_public_mirror(
                notice, env, client, index_hints=[])
            pages = (result.get("pages", []) if isinstance(result, dict)
                     and result.get("status") == "PUBLIC_PAGES_INSPECTED"
                     else [])
            count = len(pages) if isinstance(pages, list) else 0
            verified = 0 < count <= 2
            status = ("VERIFIED_PUBLIC_PAGE" if verified
                      else "NO_VERIFIED_PUBLIC_PAGE")
        except Exception:
            status, count = "PROVIDER_EXCEPTION", 0
        finalize(conn, card, attempt, status, count)
        return {"status": "TINYFISH_RETRY_" + status,
                "verified_pages": count, "crm_writes": 0}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
