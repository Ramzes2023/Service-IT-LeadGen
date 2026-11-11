"""Reconcile the four historical passports using verified facts and readback."""
import hashlib
import os
from pathlib import Path
import secrets
import sys

import httpx
from leadpilot import serviceit_b2b_v19_backfill as v19
from leadpilot import serviceit_b2b_v20_versioned as v20
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment, live_permitted
from leadpilot.serviceit_b2b_dossier_v11 import (
    REPORT, confirmed_passport_values, sync_confirmed_passport,
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS
from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
from leadpilot.serviceit_vps_b2b_document_worker import LOCK_ID as DOCUMENT_LOCK

FILES = (
    "serviceit_b2b_tender_index_v18.py", "serviceit_b2b_dossier_v11.py",
    "serviceit_b2b_v20_versioned.py", "serviceit_b2b_v19_backfill.py",
    "serviceit_vps_b2b_deep_research.py", "serviceit_b2b_free_public_fallback.py",
    "serviceit_b2b_remote_public_fetch.py", "serviceit_b2b_routerai_deep.py",
    "serviceit_vps_b2b_document_worker.py",
)
TARGETS = {11, 13, 15, 17}
HTML_RECOVERY = {19: "B2B-CENTER-4676478", 21: "B2B-CENTER-4676643"}
SAFE_PRIOR = {"VERIFIED", "NO_NEW_EVIDENCE", "INSUFFICIENT_CAPACITY"}

def out(key, value):
    print(f"DOSSIER_READBACK_{key}={value}", flush=True)

def read(rest, card):
    item = v20.normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["item"])
    v20.validate_identity(item, card)
    return item

def previously_verified_mkb(conn, current, card, notice):
    if card != 17 or notice["tender_id"] != "4675475":
        return {}
    row = conn.execute("""
        SELECT after_fields, salt, digest
        FROM serviceit_vps_b2b_mkb_4675475_manual_receipts
        WHERE card_id=17 AND status='VERIFIED'
          AND version='MKB_4675475_PUBLIC_OPERATOR_20261111_V1'
    """).fetchone()
    if not row or not row[1] or not row[2]:
        return {}
    saved, salt, digest = row
    if not secrets.compare_digest(v20.compute_digest(saved, bytes(salt)), bytes(digest)):
        return {}
    heading = "12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475"
    _, marker, fragment = saved[REPORT].partition(heading)
    link = "https://zakupki.kontur.ru/4675475_40"
    if not marker or link not in fragment or marker + fragment not in current[REPORT]:
        return {}
    expected = {
        "BUYER": "ПАО «Московский Кредитный Банк»",
        "INN": "7734202860",
        "DEADLINE": "16.11.2026 16:00 МСК",
        "LOCATION": "111141, Москва, проезд 2-й Перова Поля, д. 2, стр. 4",
    }
    if not all(value in fragment for value in expected.values()):
        return {}
    for key in expected:
        suffix = " (публичная карточка, ФНС отдельно)" if key == "INN" else " (внешняя проверка 11.11.2026)"
        expected[key] += suffix + " [" + link + "]"
    return expected


def blocked_partial_update(conn, card, source_hash, current):
    """Prove the known local validation failure without rewriting its receipt."""
    if card != 19:
        return False
    row = conn.execute(f"""
        SELECT status,source_hash,before_fields,after_fields,salt,digest,
               updated_at >= '2026-11-11 22:02:00+00'::timestamptz
               AND updated_at < '2026-11-11 22:03:00+00'::timestamptz
        FROM {v20.TABLE} WHERE card_id=%s ORDER BY attempt_no DESC LIMIT 1
    """, (card,)).fetchone()
    if not row:
        return False
    status, source, before, desired, salt, digest, known_attempt = row
    return (status == "UNCERTAIN" and source == source_hash and known_attempt
            and current == before and isinstance(desired, dict)
            and set(desired) == AI_FIELDS and salt is not None and digest is not None
            and 0 < sum(before[key] != desired[key] for key in AI_FIELDS) < len(AI_FIELDS)
            and secrets.compare_digest(v20.compute_digest(desired, bytes(salt)), bytes(digest)))


def apply_one(conn, client, rest, exact, card, source, notice, *, html_recovery=False):
    src_hash = hashlib.sha256(source.encode("ascii")).hexdigest()
    if v19.active_card_write(conn, card):
        return "WRITE_IN_FLIGHT", 0
    first = read(rest, card)
    url, title = v20.safe_notice(notice, card, source, first)
    if url != notice["source_url"] or not title:
        return "SIGNED_SOURCE_MISMATCH", 0
    before = v20.original_nine(first)
    for table in (v19.TABLE, v20.TABLE):
        prior = conn.execute(
            f"SELECT status, source_hash FROM {table} WHERE card_id=%s "
            "ORDER BY updated_at DESC LIMIT 1", (card,)).fetchone()
        if prior and (prior[0] not in SAFE_PRIOR or prior[1] != src_hash):
            if not (html_recovery and table == v20.TABLE
                    and blocked_partial_update(conn, card, src_hash, before)):
                return "RECEIPT_RECONCILIATION_REQUIRED", 0
            out(f"CARD_{card}_PREVIOUS_LOCAL_REJECTION_VERIFIED", "YES")
    findings = v20.source_findings(first, notice, exact, client, enable_v18=True)
    if html_recovery:
        pages = findings.get("pages", [])
        out(f"CARD_{card}_PUBLIC_PAGES", len(pages))
        if not any(p.get("url") == url for p in pages):
            return "OFFICIAL_SOURCE_NOT_VERIFIED", 0
        values = confirmed_passport_values(findings, source_only_investigation(findings))
    draft, count = v20.versioned_addendum(before, findings, notice["tender_id"])
    confirmed = previously_verified_mkb(conn, before, card, notice)
    projected = sync_confirmed_passport(draft or before, confirmed)
    if projected != before:
        draft = projected

    if draft is None:
        if html_recovery:
            report_confirmed(card, before[REPORT], values)
        return ("NO_NEW_EVIDENCE" if not count else "INSUFFICIENT_CAPACITY"), 0
    fresh = read(rest, card)
    if v20.fingerprint(first) != v20.fingerprint(fresh) or v19.active_card_write(conn, card):
        return "CARD_CHANGED_RECHECK_REQUIRED", 0
    attempt = v20.reserve(conn, card, src_hash, before)
    salt, digest = v20.transition(conn, card, attempt, "RESERVED", "WRITE_INTENT", desired=draft)
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            "fields": draft,
        })
        after = read(rest, card)
        if (v20.original_nine(after) != draft or not v20.non_ai_unchanged(fresh, after)
                or not secrets.compare_digest(v20.compute_digest(draft, salt), digest)):
            raise ValueError("READBACK_FAILED")
        v20.transition(conn, card, attempt, "WRITE_INTENT", "VERIFIED")
    except Exception as error:
        v20.transition(conn, card, attempt, "WRITE_INTENT", "UNCERTAIN")
        if not html_recovery:
            raise RuntimeError("WRITE_UNCERTAIN_NO_REPLAY") from None
        reason = str(error)
        if reason in {"REST_UPDATE_BLOCKED", "REST_HTTP_UNVERIFIED",
                      "REST_RESPONSE_UNVERIFIED", "READBACK_FAILED"}:
            out(f"CARD_{card}_WRITE_REASON", reason)
        return "WRITE_UNCERTAIN_NO_REPLAY", 0
    if html_recovery:
        report_confirmed(card, after[REPORT], values)
    return "VERIFIED_WRITTEN", count


def report_confirmed(card, report, values):
    passport = report.split("02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ", 1)[0]
    for field in ("BUYER", "INN", "DEADLINE", "LOCATION", "BUDGET"):
        value = values.get(field)
        out(f"CARD_{card}_{field}_IN_PASSPORT", "YES" if value and value in passport else "NO")


def main():
    args = sys.argv[1:]
    html_recovery = args[:1] == ["--public-html"]
    files = FILES + ("serviceit_public_evidence_research.py", "serviceit_enrichment_worker.py") if html_recovery else FILES
    if html_recovery:
        args = args[1:]
    assert [hashlib.sha256((Path("/app/leadpilot") / f).read_bytes()).hexdigest()
            for f in files] == args
    env = dict(os.environ)
    exact = exact_environment(env)
    assert live_permitted(env) and exact.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED") == "YES"
    assert env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    v20.validate_webhook(webhook)
    with db(env) as conn, httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20) as client:
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (v20.LOCK_ID,)).fetchone()[0]:
            out("LOCK_BUSY", "YES")
            return 2
        if html_recovery:
            if not conn.execute("SELECT pg_try_advisory_lock(%s)", (DOCUMENT_LOCK,)).fetchone()[0]:
                out("DOCUMENT_LOCK_BUSY", "YES")
                return 2
            rows = conn.execute("""
                SELECT b.card_id,b.source_id,b.notice_json
                FROM serviceit_vps_b2b_inbox b
                JOIN serviceit_vps_b2b_dossier_v11_receipts v
                    ON v.card_id=b.card_id AND v.status='VERIFIED'
                WHERE b.state='VERIFIED' AND b.card_id=ANY(%s)
                ORDER BY b.card_id
            """, (list(HTML_RECOVERY),)).fetchall()
            assert len(rows) == len(HTML_RECOVERY)
            assert {card: source for card, source, _ in rows} == HTML_RECOVERY
        else:
            rows = [row for row in v19.select_one(conn, preview=True) if row[0] in TARGETS]
            assert {row[0] for row in rows} == TARGETS and len(rows) == len(TARGETS)
        rest = v20.Rest(webhook, client)
        writes = 0
        uncertain = False
        for card, source, notice in rows:
            status, lines = apply_one(conn, client, rest, exact, card, source, notice,
                                      html_recovery=html_recovery)
            out(f"CARD_{card}_STATUS", status)
            out(f"CARD_{card}_NEW_LINES", lines)
            writes += status == "VERIFIED_WRITTEN"
            uncertain |= status == "WRITE_UNCERTAIN_NO_REPLAY"
        out("CRM_WRITES", writes)
        out("FINISHED", "YES")
    return 2 if uncertain else 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        out("FAILURE_CLASS", type(error).__name__.upper())
        raise SystemExit(2) from None
