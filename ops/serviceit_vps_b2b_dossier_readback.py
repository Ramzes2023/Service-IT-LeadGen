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

FILES = (
    "serviceit_b2b_tender_index_v18.py", "serviceit_b2b_dossier_v11.py",
    "serviceit_b2b_v19_backfill.py", "serviceit_vps_b2b_deep_research.py",
    "serviceit_b2b_free_public_fallback.py", "serviceit_b2b_remote_public_fetch.py",
    "serviceit_b2b_routerai_deep.py", "serviceit_vps_b2b_document_worker.py",
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
    env = dict(os.environ)
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
        out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
