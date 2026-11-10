#!/usr/bin/env python3
"""Owner-gated one-time recovery of EXACTLY TWO verified B2B CRM card AI HOLDs.

Runs inside the live nonroot VPS coordinator via reviewed GitHub owner workflow.
Never performs crm.item.add, external model call, customer outreach, or source
dispatch. Reserves SHA256-salted WRITE_INTENT in PG before any crm.item.update.
If a readback cannot prove success, no replay is attempted.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import re
import secrets
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted
from leadpilot.serviceit_vps_native_fence import digested
from leadpilot.serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint, AI_FIELDS, FIELDS, PREFIX,
)
from leadpilot.serviceit_windows_autofill import (
    preliminary_fields, preliminary_schema_limits,
)
from leadpilot.serviceit_bitrix_event_queue_readonly_worker import classify_item
from leadpilot.serviceit_bitrix_poll_fallback import after_activation, date_value

MAX = 2
NAMESPACE = "AI_NINE_V1"
ALLOWED_RESULT = "EXACT_WORKER_REVIEW"


class RecoveryBlocked(Exception):
    pass


def need(ok, code):
    if not ok:
        raise RecoveryBlocked(code)


def candidates(connection):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT b.card_id, b.source_id
              FROM serviceit_vps_b2b_inbox b
              JOIN serviceit_vps_new_cards q ON q.card_id=b.card_id
              JOIN serviceit_vps_native_receipts r
                ON r.card_id=b.card_id AND r.namespace=%s
             WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
               AND q.status='REVIEW' AND q.attempts=1
               AND q.last_result=%s
               AND r.status='HOLD' AND r.field_names IS NULL
               AND r.digest_salt IS NULL AND r.expected_digest IS NULL
             ORDER BY b.received_at, b.source_id LIMIT %s
        """, (NAMESPACE, ALLOWED_RESULT, MAX + 1))
        return cur.fetchall()


def candidate_fields(rest, card, source, start, limits):
    need(type(card) is int and card >= 8
         and isinstance(source, str)
         and re.fullmatch(r"B2B-CENTER-[0-9]{5,12}", source),
         "RECOVERY_SOURCE_INVALID")
    item = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["item"])
    validate_identity(item, card)
    now = datetime.now(timezone.utc)
    need(start <= date_value(item.get("createdTime")) <= now + timedelta(minutes=2),
         "RECOVERY_NOT_NEW_CARD")
    need(item.get(PREFIX + "SIGNAL_ID") == source,
         "RECOVERY_IDENTITY_CHANGED")
    decision = classify_item(item, requested_id=card)
    need(decision.get("source_kind") == "b2b_center_hold"
         and decision.get("decision") == "HOLD_MISSING_PRELIMINARY_AI",
         "RECOVERY_NOT_UNQUALIFIED_B2B_HOLD")
    need(all(name in item and item[name] in (None, "") for name in FIELDS),
         "RECOVERY_EXISTING_AI_OR_SCORE")
    fields = preliminary_fields(item)
    need(set(fields) == AI_FIELDS
         and all(isinstance(value, str) and 0 < len(value) <= limits[name]
                 for name, value in fields.items()),
         "RECOVERY_FIELDS_INVALID")
    return item, fields


def checkpoint(connection, card, fields):
    salt = secrets.token_bytes(16)
    digest = digested(fields, salt)
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_native_receipts
                   SET status='WRITE_INTENT', field_names=%s,
                       digest_salt=%s, expected_digest=%s,
                       updated_at=now()
                 WHERE namespace=%s AND card_id=%s
                   AND status='HOLD' AND field_names IS NULL
                   AND digest_salt IS NULL AND expected_digest IS NULL
                RETURNING card_id
            """, (sorted(fields), salt, digest, NAMESPACE, card))
            need(cur.fetchone() is not None, "RECOVERY_ALREADY_FENCED")
    return salt, digest


def acknowledge(connection, card, salt, expected_digest):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_native_receipts SET
                    status='HOLD_9_WRITTEN', updated_at=now()
                 WHERE namespace=%s AND card_id=%s
                   AND status='WRITE_INTENT'
                   AND digest_salt=%s AND expected_digest=%s
                RETURNING card_id
            """, (NAMESPACE, card, salt, expected_digest))
            need(cur.fetchone() is not None, "RECOVERY_NATIVE_ACK_UNVERIFIED")
            cur.execute("""
                UPDATE serviceit_vps_new_cards
                   SET status='DONE', finished_at=now(),
                       last_result='B2B_EXACT_HOLD_9_RESTORED'
                 WHERE card_id=%s AND status='REVIEW'
                   AND attempts=1 AND last_result=%s
                RETURNING card_id
            """, (card, ALLOWED_RESULT))
            need(cur.fetchone() is not None, "RECOVERY_QUEUE_ACK_UNVERIFIED")


def recover_one(connection, rest, card, source, start, limits):
    before, fields = candidate_fields(rest, card, source, start, limits)
    second = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["item"])
    validate_identity(second, card)
    need(fingerprint(second) == fingerprint(before),
         "RECOVERY_CRM_CONCURRENT_EDIT")
    salt, digest = checkpoint(connection, card, fields)
    # The irreversible CRM mutation is fenced above. If it times out, read
    # back once; do not replay even if the API response was ambiguous.
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": fields,
        })
    except Exception:
        pass
    after = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["item"])
    validate_identity(after, card)
    need(after.get(PREFIX + "SIGNAL_ID") == source
         and all(after.get(key) == value for key, value in fields.items()),
         "RECOVERY_CRM_WRITE_UNCERTAIN_NO_RETRY")
    acknowledge(connection, card, salt, digest)


def main():
    env = dict(os.environ)
    need(live_permitted(env), "RECOVERY_NOT_NATIVE_LIVE")
    need(os.geteuid() != 0, "RECOVERY_NONROOT_REQUIRED")
    with db(env) as conn:
        rows = candidates(conn)
        # Enforce the two exact already forensic-reviewed 10 Nov B2B receipts,
        # never broaden a recovery to other source cards.
        need(len(rows) == MAX, "RECOVERY_CANDIDATE_COUNT_UNEXPECTED")
        start_env = env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
        need(isinstance(start_env, str) and start_env,
             "RECOVERY_NATIVE_TIMESTAMP_MISSING")
        start = after_activation({
            **env, "SERVICEIT_BITRIX_POLL_START_UTC": start_env,
        }, datetime.now(timezone.utc))
        webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
            rest = Rest(webhook, client)
            limits = preliminary_schema_limits(rest)
            # Strictly validate both candidate cards and empty AI fields
            # BEFORE the first irreversible PG write or CRM call.
            for card, source in rows:
                candidate_fields(rest, card, source, start, limits)
            print("B2B_AI_RECOVERY_PREFLIGHT=YES", flush=True)
            restored = 0
            for card, source in rows:
                recover_one(conn, rest, card, source, start, limits)
                restored += 1
            print("B2B_AI_RECOVERY_RESTORED=" + str(restored), flush=True)
            print("B2B_AI_RECOVERY_COMPLETE=YES", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, RecoveryBlocked) else "UNEXPECTED_EXCEPTION"
        if re.fullmatch(r"[A-Z0-9_]{3,90}", code) is None:
            code = "UNEXPECTED_EXCEPTION"
        print("B2B_AI_RECOVERY_BLOCKED_" + code + "=YES", flush=True)
        raise SystemExit(2)
