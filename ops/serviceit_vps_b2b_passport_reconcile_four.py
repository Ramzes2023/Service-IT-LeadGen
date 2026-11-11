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
from leadpilot.serviceit_b2b_dossier_v11 import REPORT, sync_confirmed_passport

FILES = (
    "serviceit_b2b_tender_index_v18.py", "serviceit_b2b_dossier_v11.py",
    "serviceit_b2b_v20_versioned.py", "serviceit_b2b_v19_backfill.py",
    "serviceit_vps_b2b_deep_research.py", "serviceit_b2b_free_public_fallback.py",
    "serviceit_b2b_remote_public_fetch.py", "serviceit_b2b_routerai_deep.py",
    "serviceit_vps_b2b_document_worker.py",
)
TARGETS = {11, 13, 15, 17}
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


def apply_one(conn, client, rest, exact, card, source, notice):
    src_hash = hashlib.sha256(source.encode("ascii")).hexdigest()
    if v19.active_card_write(conn, card):
        return "WRITE_IN_FLIGHT", 0
    for table in (v19.TABLE, v20.TABLE):
        prior = conn.execute(
            f"SELECT status, source_hash FROM {table} WHERE card_id=%s "
            "ORDER BY updated_at DESC LIMIT 1", (card,)).fetchone()
        if prior and (prior[0] not in SAFE_PRIOR or prior[1] != src_hash):
            return "RECEIPT_RECONCILIATION_REQUIRED", 0
    first = read(rest, card)
    url, title = v20.safe_notice(notice, card, source, first)
    if url != notice["source_url"] or not title:
        return "SIGNED_SOURCE_MISMATCH", 0
    before = v20.original_nine(first)
    findings = v20.source_findings(first, notice, exact, client, enable_v18=True)
    draft, count = v20.versioned_addendum(before, findings, notice["tender_id"])
    confirmed = previously_verified_mkb(conn, before, card, notice)
    projected = sync_confirmed_passport(draft or before, confirmed)
    if projected != before:
        draft = projected

    if draft is None:
        return ("NO_NEW_EVIDENCE" if not count else "INSUFFICIENT_CAPACITY"), 0
    fresh = read(rest, card)
    if v20.fingerprint(first) != v20.fingerprint(fresh) or v19.active_card_write(conn, card):
        return "CARD_CHANGED_RECHECK_REQUIRED", 0
    attempt = v20.reserve(conn, card, src_hash, before)
    salt, digest = v20.transition(conn, card, attempt, "RESERVED", "WRITE_INTENT", desired=draft)
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y", "fields": draft,
        })
        after = read(rest, card)
        if (v20.original_nine(after) != draft or not v20.non_ai_unchanged(fresh, after)
                or not secrets.compare_digest(v20.compute_digest(draft, salt), digest)):
            raise ValueError("READBACK_FAILED")
        v20.transition(conn, card, attempt, "WRITE_INTENT", "VERIFIED")
    except Exception:
        v20.transition(conn, card, attempt, "WRITE_INTENT", "UNCERTAIN")
        raise RuntimeError("WRITE_UNCERTAIN_NO_REPLAY") from None
    return "VERIFIED_WRITTEN", count

def main():
    assert [hashlib.sha256((Path("/app/leadpilot") / f).read_bytes()).hexdigest()
            for f in FILES] == sys.argv[1:]
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
        rows = [row for row in v19.select_one(conn, preview=True) if row[0] in TARGETS]
        assert {row[0] for row in rows} == TARGETS and len(rows) == len(TARGETS)
        rest = v20.Rest(webhook, client)
        writes = 0
        for card, source, notice in rows:
            status, lines = apply_one(conn, client, rest, exact, card, source, notice)
            out(f"CARD_{card}_STATUS", status)
            out(f"CARD_{card}_NEW_LINES", lines)
            writes += status == "VERIFIED_WRITTEN"
        out("CRM_WRITES", writes)
        out("FINISHED", "YES")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
