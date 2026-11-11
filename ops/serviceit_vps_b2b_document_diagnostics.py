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
    "serviceit_b2b_tender_documents.py",
)
from leadpilot.serviceit_vps_worker import exact_environment
from leadpilot.serviceit_vps_b2b_deep_research import source_findings
from leadpilot.serviceit_b2b_tender_documents import inspect_public_documents

def out(key, value):
    print(f"DOSSIER_READBACK_{key}={value}", flush=True)

def main():
    assert [hashlib.sha256((Path("/app/leadpilot") / f).read_bytes()).hexdigest()
            for f in FILES] == sys.argv[1:]
    env = dict(os.environ)
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = select_one(conn, preview=True)
        assert len(rows) == 4
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        for card, source, notice in rows:
            item = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(item, card)
            url, title = safe_notice(notice, card, source, item)
            assert url == notice["source_url"] and title
            findings = source_findings(item, notice, exact_environment(env), client,
                                       enable_v18=True)
            pages = findings.get("pages") or []
            out(f"CARD_{card}_PAGES", len(pages))
            if not pages:
                out(f"CARD_{card}_DOCUMENT_STATUS", "NO_VERIFIED_TENDER_PAGE")
                continue
            try:
                result = inspect_public_documents(client, findings, notice["tender_id"])
            except Exception:
                out(f"CARD_{card}_DOCUMENT_STATUS", "DOCUMENT_INSPECTION_FAILED")
                continue
            out(f"CARD_{card}_LINKS", result["discovered"])
            out(f"CARD_{card}_READABLE", len(result["documents"]))
            out(f"CARD_{card}_REJECTED", sum(result["rejections"].values()))
        out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
