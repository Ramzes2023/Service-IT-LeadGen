"""Owner-only read-only signed original four B2B source/card identity audit.

Shows only public procurement numbers, internal CRM card IDs and receipt
STATES. No contact, buyer, description, webhook, API key, report or title
content; does not contact external APIs or write to PostgreSQL/Bitrix.
"""
from __future__ import annotations
import os
import re
import sys
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_worker import live_permitted, INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one

TABLES = {
    "V11": "serviceit_vps_b2b_dossier_v11_receipts",
    "V19": "serviceit_vps_b2b_v19_research_receipts",
    "V20": "serviceit_vps_b2b_v20_revision_receipts",
    "V23": "serviceit_vps_b2b_v23_primary_source_receipts",
}
VALUES = {"VERIFIED", "NO_NEW_EVIDENCE", "INSUFFICIENT_CAPACITY",
          "RESERVED", "WRITE_INTENT", "UNCERTAIN", "CONFLICT",
          "NOT_FOUND", "NOT_PRESENT"}


def emit(name, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,80}", name)
    assert re.fullmatch(r"[A-Z0-9_]{1,85}", str(value))
    print("B2B_FOUR_IDENTITY_" + name + "=" + str(value), flush=True)


def main():
    env = dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS == 300
    emit("LIVE_300SEC", "YES")
    with db(env) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                records = select_one(conn, preview=True)
                assert isinstance(records, list) and len(records) == 4
                emit("COUNT", "4")
                for i, (card, source, notice) in enumerate(records, 1):
                    assert type(card) is int and 8 <= card <= 2000000000
                    assert type(source) is str and isinstance(notice, dict)
                    tid = notice.get("tender_id")
                    assert type(tid) is str and re.fullmatch(r"\d{5,12}", tid)
                    assert source == "B2B-CENTER-" + tid
                    assert notice.get("source") == "B2B-Center"
                    subject = notice.get("subject")
                    assert type(subject) is str and 10 <= len(subject) <= 240
                    emit(f"CARD_{i}_ID", card)
                    emit(f"CARD_{i}_TENDER", tid)
                    emit(f"CARD_{i}_SUBJECT_1C", "YES" if
                         re.search(r"(?i)(?<!\w)1[сc](?!\w)", subject) else "NO")
                    emit(f"CARD_{i}_SUBJECT_IT", "YES" if
                         re.search(r"(?i)\b(?:ИТ|IT)\b", subject) else "NO")
                    for label, table in TABLES.items():
                        cur.execute("SELECT to_regclass(%s)",
                                    ("public." + table,))
                        if cur.fetchone()[0] is None:
                            status = "NOT_PRESENT"
                        else:
                            sql = ("SELECT status FROM " + table +
                                   " WHERE card_id=%s ORDER BY updated_at DESC LIMIT 1")
                            cur.execute(sql, (card,))
                            row = cur.fetchone()
                            status = str(row[0]) if row else "NOT_FOUND"
                        assert status in VALUES
                        emit(f"CARD_{i}_{label}", status)
    emit("CRM_WRITES", "NO")
    emit("PG_WRITES", "NO")
    emit("EXTERNAL_SEARCH", "NO")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        emit("FAIL_CLOSED", "YES")
        raise SystemExit(2)
