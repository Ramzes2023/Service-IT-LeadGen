#!/usr/bin/env python3
"""No-write live next-B2B-card acceptance audit, run INSIDE coordinator.

Never creates a fake Bitrix card, never reserves a receipt and never runs
Brave/RouterAI or source collectors. Does verify the real Bitrix list API,
the current 5-minute PG checkpoint, queue states, rendered dossier contract
from an EXISTING signed real notice (in memory), and the exact CRM readback
of any new B2B cards already processed since the initial four.
Only fixed boolean/count/status codes leave the VPS; no IDs or data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import sys

import httpx


def say(key: str, value) -> None:
    if not re.fullmatch(r"[A-Z0-9_]{3,60}", key):
        raise ValueError("OUTPUT_KEY_INVALID")
    raw = str(value)
    if not re.fullmatch(r"(YES|NO|UNKNOWN|NOT_OBSERVED|[0-9]{1,4})", raw):
        raise ValueError("OUTPUT_VALUE_INVALID")
    print("FUTURE_B2B_" + key + "=" + raw, flush=True)


def checks(env, client):
    from leadpilot.serviceit_vps_worker import (
        live_permitted, exact_environment, readonly_preflight, poll_new,
        INTERVAL_SECONDS,
    )
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_b2b_dossier_v11 import compose
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity

    expected = sys.argv[1:3]
    names = ("serviceit_b2b_dossier_v11.py",
             "serviceit_vps_b2b_dossier_v11_worker.py")
    sha_match = len(expected) == 2 and all(
        re.fullmatch(r"[0-9a-f]{64}", value or "") and
        (Path("/app/leadpilot") / name).is_file() and
        hashlib.sha256((Path("/app/leadpilot") / name).read_bytes()).hexdigest() == value
        for name, value in zip(names, expected)
    )
    say("V11_EXACT_LIVE_SHA", "YES" if sha_match else "NO")
    poll_expected=sys.argv[3:5]
    poll_names=("serviceit_bitrix_poll_fallback.py",
                "serviceit_bitrix_batch_worker.py")
    poll_sha_match=(len(poll_expected)==2 and all(
        re.fullmatch(r"[0-9a-f]{64}", val or "") and
        (Path("/app/leadpilot")/name).is_file() and
        hashlib.sha256((Path("/app/leadpilot")/name).read_bytes()).hexdigest()==val
        for name,val in zip(poll_names,poll_expected)))
    say("POLL_MODULES_LIVE_SHA", "YES" if poll_sha_match else "NO")

    flags = live_permitted(env) and INTERVAL_SECONDS == 300 and all((
        env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES",
        env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES",
        env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES",
        env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT", "NO") == "NO",
    ))
    say("FIVE_MINUTE_LIVE_GATES", "YES" if flags else "NO")

    secrets_ok = False
    try:
        exact = exact_environment(env)
        secrets_ok = (
            bool(exact.get("SERVICEIT_BITRIX_WEBHOOK")) and
            bool(exact.get("ROUTERAI_API_KEY")) and
            bool(exact.get("SERVICEIT_BRAVE_SEARCH_API_KEY")))
    except Exception:
        pass
    say("BITRIX_ROUTERAI_BRAVE_KEYS", "YES" if secrets_ok else "NO")

    try:
        from leadpilot.serviceit_vps_b2b_deep_research import source_findings
        from leadpilot.serviceit_b2b_routerai_deep import investigate_routerai
        from leadpilot.serviceit_b2b_public_mirrors import brave_mirror_search
        modules = all(callable(fn) for fn in (
            source_findings, investigate_routerai, brave_mirror_search, compose))
    except Exception:
        modules = False
    say("SOURCE_RESEARCH_IMPORTS", "YES" if modules else "NO")
    tdi_enabled = env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED", "NO") == "YES"
    tdi_present = (Path("/app/leadpilot") /
                   "serviceit_vps_b2b_document_worker.py").is_file()
    say("TDI_ACTIVE", "YES" if tdi_enabled and tdi_present else "NO")

    # Real metadata/list probes ONLY; no Bitrix update, queue insert or model call.
    api_ready = False
    try:
        metadata = readonly_preflight(env, client=client)
        api_ready = metadata.get("status") == "READONLY_CRM_FIELD_METADATA_CONFIRMED"
        with db(env) as conn:
            probe = poll_new(env, client=client, connection=conn, preview_only=True)
        api_ready = api_ready and probe.get("status") == "POLL_READONLY"
    except Exception as exc:
        api_ready = False
        # Only exact whitelisted reason codes may leave the VPS. Never
        # print exception text, provider URLs, notices, credentials or IDs.
        code = str(exc)
        checks = {
            "POLL_CALIBRATION_POSITIVE_MISSING": "CALIBRATION",
            "POLL_CALIBRATION_IDENTITY_INVALID": "CALIBRATION",
            "POLL_CALIBRATION_PAGINATION_INVALID": "CALIBRATION",
            "BATCH_LIST_FILTER_INVALID": "FILTER",
            "BATCH_LIST_SCOPE_INVALID": "FILTER",
            "POLL_ITEM_BEFORE_ACTIVATION_OR_FUTURE": "WINDOW",
            "POLL_ACTIVATION_TIMESTAMP_INVALID": "WINDOW",
            "POLL_LIST_SHAPE_UNTRUSTED": "RESPONSE",
            "POLL_INCOMPLETE_PAGINATION": "RESPONSE",
            "BATCH_LIST_HTTP_UNVERIFIED": "HTTP",
        }
        reason = checks.get(code, "OTHER")
        for name in ("CALIBRATION","FILTER","WINDOW","RESPONSE","HTTP","OTHER"):
            say("BITRIX_POLL_FAILURE_"+name,
                "YES" if reason == name else "NO")
    say("BITRIX_NEW_CARD_READONLY_POLL", "YES" if api_ready else "NO")

    poll_age = None
    inbox_count = 0
    verified_count = 0
    overdue = 0
    v11_failures = 0
    queue_failures = 0
    pending = 0
    receipt_rows = []
    sample = None
    try:
        with db(env) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT greatest(0,floor(extract(epoch from
                      now()-last_success_at)))::int
                    FROM serviceit_vps_poll_state WHERE singleton=1
                """)
                row = cur.fetchone()
                poll_age = row[0] if row else None
                cur.execute("""
                    SELECT count(*) FROM serviceit_vps_new_cards
                    WHERE status IN ('REVIEW','UNCERTAIN')
                """)
                queue_failures = int(cur.fetchone()[0])
                cur.execute("""
                    SELECT count(*) FROM serviceit_vps_new_cards
                    WHERE status IN ('PENDING','RUNNING')
                """)
                pending = int(cur.fetchone()[0])
                cur.execute("""
                    SELECT count(*) FROM serviceit_vps_b2b_dossier_v11_receipts
                    WHERE status IN ('CONFLICT','UNCERTAIN','WRITE_INTENT')
                       OR (status='RESERVED' AND updated_at < now()-interval '30 minutes')
                """)
                v11_failures = int(cur.fetchone()[0])
                cur.execute("""
                    SELECT
                      count(*) AS total,
                      count(*) FILTER(WHERE v.status='VERIFIED') AS verified,
                      count(*) FILTER(WHERE v.card_id IS NULL
                                     AND b.received_at < now()-interval '40 minutes') AS overdue
                    FROM serviceit_vps_b2b_inbox b
                    LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v
                      ON v.card_id=b.card_id
                    WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
                """)
                inbox_count, verified_count, overdue = (int(x) for x in cur.fetchone())
                cur.execute("""
                    SELECT b.notice_json,v.before_fields
                    FROM serviceit_vps_b2b_inbox b
                    JOIN serviceit_vps_b2b_dossier_v11_receipts v
                      ON v.card_id=b.card_id
                    WHERE b.state='VERIFIED' AND v.status='VERIFIED'
                    ORDER BY b.received_at LIMIT 1
                """)
                sample = cur.fetchone()
                # V12 can legitimately supersede the four historical V11
                # snapshots. Only compare unmodified V11 receipts to live CRM.
                cur.execute("""
                    SELECT v.card_id,v.after_fields,v.salt,v.digest
                    FROM serviceit_vps_b2b_dossier_v11_receipts v
                    LEFT JOIN serviceit_vps_b2b_dossier_v12_continuity_receipts a
                      ON a.card_id=v.card_id
                    WHERE v.status='VERIFIED' AND a.card_id IS NULL
                    ORDER BY v.card_id LIMIT 10
                """)
                receipt_rows = cur.fetchall()
    except Exception:
        say("POSTGRESQL_READONLY", "NO")
        return False

    say("POSTGRESQL_READONLY", "YES")
    say("POLL_AGE_SECONDS", min(poll_age,9999) if poll_age is not None else "UNKNOWN")
    poll_fresh = poll_age is not None and poll_age <= 720
    say("POLL_FRESH_UNDER_12MIN", "YES" if poll_fresh else "NO")
    say("SIGNED_B2B_CARDS", min(inbox_count,9999))
    say("V11_VERIFIED_CARDS", min(verified_count,9999))
    say("OVERDUE_UNSIGNED_WORK", min(overdue,9999))
    say("NEW_CARD_QUEUE_PENDING", min(pending,9999))
    say("NEW_CARD_QUEUE_REVIEW_UNCERTAIN", min(queue_failures,9999))
    say("V11_RECEIPT_BLOCKERS", min(v11_failures,9999))

    # Genuine saved signed notice + prior nine real Bitrix fields, but NO
    # mutation: prove the same formatter returns a readable full report.
    rendered = False
    try:
        notice, before = sample
        fields, coverage = compose(notice, before,
                                   {"status":"NO_PUBLIC_SOURCE","pages":[]},
                                   {"facts":[]})
        rendered = (
            set(fields) == AI_FIELDS and
            all(type(x) is str and x for x in fields.values()) and
            all(label in fields[PREFIX+"AI_FULL_REPORT"] for label in (
                "01  ПАСПОРТ ЗАКУПКИ",
                "02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ",
                "03  ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ",
                "04  КОНТАКТЫ И ЮРИДИЧЕСКАЯ ПРОВЕРКА",
                "05  НАПРАВЛЕНИЕ И ЗАДАЧИ ДЛЯ МЕНЕДЖЕРА",
                "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ",
                "08  РЕЕСТР ИСТОЧНИКОВ",
            )) and
            "КОМУ ПЕРЕДАТЬ  " in fields[PREFIX+"AI_MANAGER_BRIEF"] and
            coverage["citations"] == 0
        )
    except Exception:
        rendered = False
    say("NEW_CARD_V11_STYLE_IN_MEMORY", "YES" if rendered else "NO")

    # If there is a future B2B record beyond the initial 4, independently
    # read it from real Bitrix. NO synthetic CRM records or writes.
    actual_new = verified_count > 4
    crm_verified = True
    if actual_new:
        try:
            rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
            for card,expected,salt,digest in receipt_rows:
                item = normalized(rest("crm.item.get",{
                    "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                })["item"])
                validate_identity(item,card)
                fields = {key:item.get(key) for key in AI_FIELDS}
                crm_verified = (crm_verified and fields == expected and
                    secrets.compare_digest(
                        compute_digest(fields,bytes(salt)), bytes(digest)))
        except Exception:
            crm_verified = False
    say("REAL_NEW_B2B_E2E", "YES" if actual_new and crm_verified else
        "NO" if actual_new else "NOT_OBSERVED")

    ready = bool(
        sha_match and flags and secrets_ok and modules and api_ready and
        poll_fresh and inbox_count >= 4 and verified_count >= 4 and
        overdue == 0 and v11_failures == 0 and queue_failures == 0 and
        rendered and crm_verified
    )
    say("READINESS", "YES" if ready else "NO")
    return ready


def main():
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=24) as client:
            ok=checks(dict(os.environ),client)
        return 0 if ok else 2
    except Exception:
        say("READINESS", "NO")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
