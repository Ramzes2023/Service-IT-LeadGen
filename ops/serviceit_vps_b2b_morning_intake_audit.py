"""Read-only trace of public B2B tender IDs from recent mail."""
import hashlib
import os
from pathlib import Path
import re
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db, secret_file

TENDERS = (4678279, 4677923, 4677404, 4677160, 4676643, 4677837, 4674919, 4679263)


def emit(key, value):
    value = str(value)
    if re.fullmatch(r"[A-Z0-9_]+", value):
        print(f"INTAKE_{key}={value}", flush=True)


def main():
    from leadpilot.serviceit_vps_b2b_bridge import (
        enabled, exact_duplicate, positive_duplicate_control, read_candidate, validate_webhook,
    )
    env = dict(os.environ)
    emit("MOSCOW_ONLY", "YES" if env.get("SERVICEIT_B2B_MOSCOW_ONLY_ENABLED") == "YES" else "NO")
    emit("WRITER_ENABLED", "YES" if enabled(env, writer=True) else "NO")
    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT state,count(*)
            FROM serviceit_vps_b2b_inbox
            WHERE received_at >= TIMESTAMPTZ '2026-11-12 00:00:00 Europe/Rome'
            GROUP BY state
        """).fetchall()
        emit("TODAY_RECEIVED", sum(count for _, count in rows))
        for state, count in rows:
            emit("TODAY_" + state, count)
        latest = conn.execute("""
            SELECT source_id,state,card_id
            FROM serviceit_vps_b2b_inbox
            WHERE received_at >= TIMESTAMPTZ '2026-11-12 00:00:00 Europe/Rome'
            ORDER BY received_at DESC LIMIT 20
        """).fetchall()
        for source, state, card in latest:
            match = re.fullmatch(r"B2B-CENTER-(\d{5,12})", source)
            if match:
                label = f"RECENT_{match[1]}"
                emit(label, state)
                if card:
                    emit(label + "_CARD", card)
        for tid in TENDERS:
            row = conn.execute("SELECT state,card_id FROM serviceit_vps_b2b_inbox WHERE source_id=%s",
                               (f"B2B-CENTER-{tid}",)).fetchone()
            emit(f"TENDER_{tid}_PG", row[0] if row else "ABSENT")
            if row and row[1]:
                emit(f"TENDER_{tid}_PG_CARD", row[1])
        reviews = conn.execute("""
            SELECT source_id,notice_json FROM serviceit_vps_b2b_inbox
            WHERE state='REVIEW' AND source_id=ANY(%s)
        """, (["B2B-CENTER-4677923","B2B-CENTER-4673595"],)).fetchall()
        emit("PENDING", "YES" if read_candidate(conn) else "NO")
    base = validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        emit("BITRIX_DUPLICATE_CONTROL", "YES" if positive_duplicate_control(client, base) else "NO")
        from leadpilot.serviceit_b2b_moscow_geo import (
            HOSTS, _get, _robots, VisibleText, same_official_tender_head,
            matched_excerpt, classify_location_lines,
        )
        for source, notice in reviews:
            tid = source.split("-")[-1]
            phase = "ROBOTS"
            try:
                _robots(client, notice["source_url"], HOSTS)
                phase = "FETCH"
                html = _get(client, notice["source_url"], HOSTS)
                parser = VisibleText()
                parser.feed(html)
                flat = " ".join(parser.parts)
                emit(f"GEO_{tid}_HEAD", "YES" if same_official_tender_head(
                    flat, tid, notice["subject"]) else "NO")
                emit(f"GEO_{tid}_MATCHED", "YES" if matched_excerpt(
                    flat, tid, notice["subject"]) else "NO")
                emit(f"GEO_{tid}_LOCATION", classify_location_lines(parser.parts))
            except Exception as exc:
                emit(f"GEO_{tid}_{phase}", str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{3,70}", str(exc))
                     else type(exc).__name__.upper())
        for tid in TENDERS:
            try:
                matches = exact_duplicate(client, base, f"B2B-CENTER-{tid}")
            except Exception as exc:
                emit(f"TENDER_{tid}_CRM", "READ_UNVERIFIED")
                emit(f"TENDER_{tid}_CRM_ERROR", type(exc).__name__.upper())
                continue
            emit(f"TENDER_{tid}_CRM_COUNT", len(matches))
            if len(matches) == 1:
                emit(f"TENDER_{tid}_CRM_CARD", matches[0]["id"])


def research():
    from leadpilot.serviceit_vps_worker import exact_environment, live_permitted
    env = dict(os.environ)
    effective = exact_environment(env)
    emit("RESEARCH_ENABLED", "YES" if live_permitted(env) and
         effective.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES" else "NO")
    from leadpilot.b2b_email_opportunity_bridge import classify_services
    emit("RESEARCH_PROFILE_SHA", hashlib.sha256(
        Path("/app/leadpilot/b2b_email_opportunity_bridge.py").read_bytes()).hexdigest().upper())
    emit("RESEARCH_TEAMS_PROFILE", "YES" if "LICENSES" in
         classify_services("Приобретение лицензий Teams (продление)") else "NO")
    emit("RESEARCH_CLOUD_PROFILE", "YES" if "CLOUD" in
         classify_services("Предоставление доступа к облачным сервисам") else "NO")
    tables = {
        "DOSSIER": "serviceit_vps_b2b_dossier_v11_receipts",
        "RESEARCH": "serviceit_vps_b2b_v19_research_receipts",
        "REVISION": "serviceit_vps_b2b_v20_revision_receipts",
        "MANAGER": "serviceit_vps_b2b_dossier_consistency_v37_receipts",
    }
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_kanban_presentation import SCORE_FIELD
    target_cards = []
    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT source_id, card_id FROM serviceit_vps_b2b_inbox
            WHERE state='VERIFIED' AND card_id IS NOT NULL
            ORDER BY received_at DESC LIMIT 12
        """).fetchall()
        for source, card in rows:
            if source in ("B2B-CENTER-4677923", "B2B-CENTER-4679263"):
                target_cards.append(card)
                state = conn.execute("""
                    SELECT status FROM serviceit_vps_native_receipts
                    WHERE card_id=%s AND namespace='B2B_RESEARCH_V4'
                """, (card,)).fetchone()
                emit(f"CARD_{card}_RESEARCH_NATIVE", state[0] if state else "NONE")
            for label, table in tables.items():
                exists = conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
                if not exists:
                    emit(f"CARD_{card}_{label}", "NO_TABLE")
                    continue
                row = conn.execute(f"SELECT status FROM {table} WHERE card_id=%s "
                                   "ORDER BY updated_at DESC LIMIT 1", (card,)).fetchone()
                emit(f"CARD_{card}_{label}", row[0] if row else "NONE")

    if target_cards:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
            rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
            for target_card in target_cards:
                card = normalized(rest("crm.item.get", {
                    "entityTypeId": 1062, "id": target_card, "useOriginalUfNames": "Y",
                })["item"])
                validate_identity(card, target_card)
                emit(f"CARD_{target_card}_CRM_REPORT",
                     "YES" if card.get(PREFIX+"AI_FULL_REPORT") else "NO")
                emit(f"CARD_{target_card}_CRM_RESEARCH_STATUS", card.get(PREFIX+"AI_STATUS") or "NONE")
                score = card.get(SCORE_FIELD)
                emit(f"CARD_{target_card}_CRM_SCORE", score if score is not None else "NONE")
                emit(f"CARD_{target_card}_MANAGER_DECISION",
                     "PRESENT" if card.get(PREFIX+"REVIEW_DECISION") else "EMPTY")


if __name__ == "__main__":
    try:
        research() if "--research" in sys.argv else main()
    except Exception as exc:
        emit("AUDIT_ERROR", str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{3,70}", str(exc))
             else type(exc).__name__.upper())
        raise SystemExit(2) from None
