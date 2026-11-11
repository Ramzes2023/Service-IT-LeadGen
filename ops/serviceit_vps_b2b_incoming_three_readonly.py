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
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_vps_b2b_bridge import exact_duplicate
    effective = exact_environment(env)
    out("DEEP_RESEARCH_ENABLED", env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED", "NO"))
    out("V35_RETRY_ENABLED", effective.get("SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED", "NO"))
    out("V20_ENABLED", effective.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED", "NO"))
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=18) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        base = validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
        for state, count in conn.execute(
            "SELECT state,count(*) FROM serviceit_vps_b2b_inbox GROUP BY state"
        ).fetchall():
            out(f"INBOX_{state}", count)
        for tid in (4677404, 4676478, 4676643):
            source = f"B2B-CENTER-{tid}"
            row = conn.execute("""
                SELECT b.state,b.card_id,coalesce(v.status,'NO_RECEIPT')
                FROM serviceit_vps_b2b_inbox b
                LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v ON v.card_id=b.card_id
                WHERE b.source_id=%s
            """, (source,)).fetchone()
            out(f"TENDER_{tid}_INBOX", row[0] if row else "NOT_RECEIVED")
            out(f"TENDER_{tid}_V11", row[2] if row else "NO_RECEIPT")
            try:
                out(f"TENDER_{tid}_CRM_EXISTS", "YES" if exact_duplicate(client, base, source) else "NO")
            except Exception:
                out(f"TENDER_{tid}_CRM_EXISTS", "UNVERIFIED")
        out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
