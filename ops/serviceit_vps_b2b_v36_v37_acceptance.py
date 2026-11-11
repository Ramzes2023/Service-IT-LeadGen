"""Read the four signed dossier receipts and compare them with current CRM."""
import hashlib
import os
import sys
from pathlib import Path

import httpx
from leadpilot.serviceit_b2b_v19_backfill import (
    Rest, original_nine, normalized, safe_notice, select_one, validate_identity, REPORT,
)
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted, INTERVAL_SECONDS, exact_environment
from leadpilot.serviceit_b2b_dossier_v11 import _PASSPORT_PREFIXES, _PASSPORT_GAPS

FILES = (
    "serviceit_b2b_tender_index_v18.py", "serviceit_b2b_dossier_v11.py",
    "serviceit_b2b_v19_backfill.py", "serviceit_vps_b2b_deep_research.py",
    "serviceit_b2b_free_public_fallback.py", "serviceit_b2b_remote_public_fetch.py",
    "serviceit_b2b_routerai_deep.py", "serviceit_vps_b2b_document_worker.py",
    "serviceit_b2b_v20_versioned.py", "serviceit_vps_b2b_dossier_v11_worker.py",
    "serviceit_b2b_tender_documents.py",
)
TABLES = (
    ("V19", "serviceit_vps_b2b_v19_research_receipts"),
    ("V20", "serviceit_vps_b2b_v20_revision_receipts"),
    ("TDI", "serviceit_vps_b2b_tender_doc_receipts"),
)

def out(key, value):
    print(f"DOSSIER_READBACK_{key}={value}", flush=True)

def main():
    assert [hashlib.sha256((Path("/app/leadpilot") / f).read_bytes()).hexdigest()
            for f in FILES] == sys.argv[1:]
    out("LIVE_HASHES_MATCH", "YES")
    env = dict(os.environ)
    out("FIVE_MINUTE_LIVE", "YES" if live_permitted(env) and INTERVAL_SECONDS == 300 else "NO")
    out("TDI_ENABLED", env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED", "NO"))
    out("V19_ENABLED", exact_environment(env).get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED", "NO"))
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        rows = select_one(conn, preview=True)
        assert len(rows) == 4
        for card, source, notice in rows:
            first = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(first, card)
            url, title = safe_notice(notice, card, source, first)
            assert url == notice["source_url"] and title
            current = original_nine(first)
            _, _, passport = current[REPORT].partition("01  ПАСПОРТ ЗАКУПКИ")
            passport = passport.split("02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ", 1)[0]
            for key, prefix in _PASSPORT_PREFIXES.items():
                value = next((line[len(prefix):] for line in passport.splitlines()
                              if line.startswith(prefix)), "")
                state = "PRESENT" if value and value != _PASSPORT_GAPS[key] else "GAP"
                if key == "DEADLINE" and (value.startswith("[АРХИВ") or "сверить" in value):
                    state = "UNVERIFIED"
                out(f"CARD_{card}_PASSPORT_{key}", state)
            verified_lines = set()
            for label, table in TABLES:
                columns = "status" if label == "TDI" else "status, after_fields"
                rows = conn.execute(
                    f"SELECT {columns} FROM {table} WHERE card_id=%s "
                    "ORDER BY updated_at DESC LIMIT 1", (card,)).fetchall()
                status = rows[0][0] if rows else "NO_RECEIPT"
                out(f"CARD_{card}_{label}", status)
                if label != "TDI" and status == "VERIFIED":
                    assert isinstance(rows[0][1], dict)
                    saved = rows[0][1]
                    out(f"CARD_{card}_{label}_MATCH", "YES" if all(
                        current.get(k) == v for k, v in saved.items()) else "NO")
                    verified_lines.update(line for line in saved.get(REPORT, "").splitlines()
                                          if line.startswith("ПРОЧИТАННЫЙ ИСТОЧНИК / ")
                                          and line in current[REPORT])
            out(f"CARD_{card}_VERIFIED_LINES", len(verified_lines))
        new_cards = conn.execute("""
            SELECT count(*) FROM serviceit_vps_b2b_inbox
            WHERE state='VERIFIED' AND card_id IS NOT NULL
              AND card_id NOT IN (11,13,15,17)
        """).fetchone()[0]
        out("OTHER_VERIFIED_INBOX_CARDS", new_cards)
        out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
