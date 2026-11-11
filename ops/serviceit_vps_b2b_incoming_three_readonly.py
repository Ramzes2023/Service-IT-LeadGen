"""Read the four signed dossier receipts and compare them with current CRM."""
import hashlib
import os
import re
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
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
    effective = exact_environment(env)
    out("DEEP_RESEARCH_ENABLED", env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED", "NO"))
    out("V35_RETRY_ENABLED", effective.get("SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED", "NO"))
    out("V20_ENABLED", effective.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED", "NO"))
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=18) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        for state, count in conn.execute(
            "SELECT state,count(*) FROM serviceit_vps_b2b_inbox GROUP BY state"
        ).fetchall():
            out(f"INBOX_{state}", count)
        for tid in (4677404, 4676478, 4676643):
            source = f"B2B-CENTER-{tid}"
            row = conn.execute("""
                SELECT b.state,b.card_id,coalesce(v.status,'NO_RECEIPT'),coalesce(q.status,'NO_QUEUE')
                FROM serviceit_vps_b2b_inbox b
                LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v ON v.card_id=b.card_id
                LEFT JOIN serviceit_vps_new_cards q ON q.card_id=b.card_id
                WHERE b.source_id=%s
            """, (source,)).fetchone()
            out(f"TENDER_{tid}_INBOX", row[0] if row else "NOT_RECEIVED")
            out(f"TENDER_{tid}_V11", row[2] if row else "NO_RECEIPT")
            out(f"TENDER_{tid}_COORDINATOR_QUEUE", row[3] if row else "NO_QUEUE")
            if row and row[1]:
                out(f"TENDER_{tid}_CARD", row[1])
                native = conn.execute(
                    "SELECT status FROM serviceit_vps_native_receipts "
                    "WHERE card_id=%s AND namespace='B2B_RESEARCH_V4'", (row[1],)
                ).fetchone()
                out(f"TENDER_{tid}_RESEARCH", native[0] if native else "NO_RECEIPT")
                documents = conn.execute(
                    "SELECT status FROM serviceit_vps_b2b_tender_doc_receipts WHERE card_id=%s",
                    (row[1],)
                ).fetchone()
                out(f"TENDER_{tid}_DOCUMENTS", documents[0] if documents else "NO_RECEIPT")
            try:
                signal = PREFIX + "SIGNAL_ID"
                result = rest("crm.item.list", {
                    "entityTypeId": 1062, "useOriginalUfNames": "Y",
                    "filter": {"categoryId": 21, signal: source}, "start": 0,
                })
                candidates = result["items"]
                assert isinstance(candidates, list) and len(candidates) < 50 and "next" not in result
                for item in candidates:
                    current = normalized(rest("crm.item.get", {
                        "entityTypeId": 1062, "useOriginalUfNames": "Y", "id": item["id"],
                    })["item"])
                    validate_identity(current, item["id"])
                    assert current[signal] == source
                    report = current.get(REPORT) or ""
                    if "01  ПАСПОРТ ЗАКУПКИ" in report:
                        out(f"TENDER_{tid}_PASSPORT", "PRESENT")
                        passport = report.split("01  ПАСПОРТ ЗАКУПКИ", 1)[1].split(
                            "02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ", 1)[0]
                        for key, prefix in _PASSPORT_PREFIXES.items():
                            value = next((line[len(prefix):] for line in passport.splitlines()
                                          if line.startswith(prefix)), "")
                            state = "PRESENT" if value and value != _PASSPORT_GAPS[key] else "MISSING"
                            if key == "DEADLINE" and ("сверить" in value or value.startswith("[АРХИВ")):
                                state = "EMAIL_OR_ARCHIVE_ONLY"
                            out(f"TENDER_{tid}_FIELD_{key}", state)
                        proof = re.search(r"Доказательств: (\d+) цитат / (\d+) ", report)
                        if proof:
                            out(f"TENDER_{tid}_CITATIONS", int(proof[1]))
                            out(f"TENDER_{tid}_SOURCES", int(proof[2]))
                out(f"TENDER_{tid}_CRM_EXISTS", "YES" if candidates else "NO")
            except Exception:
                out(f"TENDER_{tid}_CRM_EXISTS", "UNVERIFIED")
        out("CRM_WRITES", 0)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        out("FAILURE_CLASS", type(error).__name__.upper())
        raise SystemExit(2) from None
