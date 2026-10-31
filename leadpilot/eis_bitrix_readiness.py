"""EIS -> Bitrix24 *readiness* gate. Never a CRM sender.

Sources: independently checked EIS SQLite+CSV, optional official FNS
buyer-research audit and manager research queue, optional admin-reviewed
Bitrix24 test smart-process preflight. Every external artefact is an
untrusted *claim* until human evidence/portal permission is confirmed.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from .eis_daily_health import verify as verify_eis
from .eis_buyer_review import _read as read_json

MAX_BYTES = 3 * 1024 * 1024
REQUIRED_MIKHAIL = (
    "OFFICIAL_NOTICE_ACTIVE_DEADLINE_AND_TERMS",
    "EXACT_BUYER_INN_AND_FNS_MICRO_SMALL_MOSCOW_MO",
    "BUYER_IS_COMMERCIAL_TARGET_NOT_PUBLIC_INSTITUTION",
    "CORPORATE_CONTACT_AND_LEGAL_BASIS",
    "CRM_DUPLICATES_EXISTING_CLIENTS_AND_STOP_LIST",
    "MANAGER_REVIEW_AND_FIXED_LEAD_SCORE",
    "BITRIX24_ADMIN_APPROVED_TEST_PORTAL_FIELDS_AND_ROBOTS",
    "BITRIX24_EXPLICIT_SYNTHETIC_WRITE_ACCEPTANCE",
)


def _read(path: str | Path) -> dict:
    return read_json(path, MAX_BYTES)


def check(state_dir: str | Path, *, buyer_audit: str | Path | None = None,
          manager_queue: str | Path | None = None,
          crm_preflight: str | Path | None = None) -> dict:
    """Audits actual EIS evidence but grants no write authority."""
    source = verify_eis(state_dir)
    triage = _read(Path(source["latest_run"]) / "eis_triage.json")
    counts = triage["counts"]
    public = counts["public_sector_text_hints"]
    total = counts["unique_notices"]
    if (type(total) is not int or type(public) is not int
            or public > total or public < 0
            or triage.get("crm_writes") != "NO"):
        raise ValueError("EIS_BITRIX_BAD_TRIAGE_COUNTS")
    audit_stats = {"status": "NOT_PROVIDED", "fns_matched_research": 0,
                   "manager_research_candidates": 0}
    if buyer_audit is not None:
        audit = _read(buyer_audit)
        if (audit.get("mode") != "EIS_BUYER_FNS_REVIEW_ONLY"
                or audit.get("crm_writes") != "NO"
                or audit.get("outbound_messages") != "NO"
                or not isinstance(audit.get("counts"), dict)
                or audit["counts"].get("crm_ready") != 0
                or audit["counts"].get("qualified_leads") != 0
                or not isinstance(audit.get("items"), list)):
            raise ValueError("EIS_BITRIX_UNTRUSTED_BUYER_AUDIT")
        check_day = date.fromisoformat(audit["checked_on"])
        if not 0 <= (date.today() - check_day).days <= 7:
            raise ValueError("EIS_BITRIX_STALE_BUYER_AUDIT")
        audit_stats = {
            "status": "OFFLINE_RESEARCH_ONLY",
            "fns_matched_research": audit["counts"].get("fns_size_region_matched", 0),
            "manager_research_candidates": audit["counts"].get("manager_research_candidates", 0),
        }
    queue_stats = {"status": "NOT_PROVIDED", "companies_for_manual_review": 0}
    if manager_queue is not None:
        q = _read(manager_queue)
        if (buyer_audit is None or q.get("mode") != "EIS_MANUAL_MANAGER_QUEUE"
                or q.get("crm_writes") != "NO"
                or q.get("outbound_messages") != "NO"
                or not isinstance(q.get("counts"), dict)
                or q["counts"].get("qualified_leads") != 0
                or q["counts"].get("crm_ready") != 0
                or not isinstance(q.get("companies"), list)):
            raise ValueError("EIS_BITRIX_UNTRUSTED_MANAGER_QUEUE")
        if q["counts"].get("manager_review_companies") != len(q["companies"]):
            raise ValueError("EIS_BITRIX_MANAGER_COUNT_MISMATCH")
        if any(c.get("crm_ready") is not False for c in q["companies"]
               if isinstance(c, dict)):
            raise ValueError("EIS_BITRIX_PREAPPROVED_MANAGER_QUEUE")
        queue_stats = {"status": "HUMAN_REVIEW_ONLY",
                       "companies_for_manual_review": len(q["companies"])}
    portal = {"status": "NOT_TESTED_WITH_COMPANY_PORTAL",
              "isolated_test_smartprocess_read_confirmed": False}
    if crm_preflight is not None:
        cfg = _read(crm_preflight)
        if (cfg.get("mode") != "EIS_BITRIX_READ_ONLY" or cfg.get("crm_writes") != "NO"
                or cfg.get("write_authorized") is not False
                or cfg.get("crm_ready") is not False):
            raise ValueError("EIS_BITRIX_UNTRUSTED_PORTAL_PREFLIGHT")
        portal = {
            "status": "OPERATOR_SUPPLIED_PREFLIGHT_NOT_LIVE_VERIFIED_HERE",
            "isolated_test_smartprocess_read_confirmed": (
                cfg.get("status") == "READ_OK_WRITE_AND_DUPLICATE_GATES_CLOSED"),
        }
    holds = list(REQUIRED_MIKHAIL)
    return {
        "mode": "EIS_TO_BITRIX24_MVP_READINESS_AUDIT_ONLY",
        "eis_transport_and_sqlite": "PASS",
        "eis_latest_run": source["latest_run"],
        "eis_notices": total,
        "eis_public_sector_text_hints": public,
        "eis_new_since_previous_success": source["new_since_previous"],
        "buyer_audit": audit_stats,
        "manager_queue": queue_stats,
        "bitrix24": portal,
        "marketing_qualified_leads": 0,
        "crm_ready": 0,
        "crm_write_enabled": False,
        "crm_writes": "NO",
        "outbound_messages": "NO",
        "open_acceptance_gates": holds,
        "next_action": "Verify original notice status/deadline/buyer INN; "
                       "compare verified FNS micro/small Moscow/MO; "
                       "obtain company-approved isolated Bitrix24 test portal.",
        "warning": "Public 44-FZ tender notices do not prove SME buyer or a "
                   "qualifying commercial lead. No real Bitrix24 access or writes.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="EIS -> Bitrix24 safe readiness, read-only")
    p.add_argument("--state-dir", required=True)
    p.add_argument("--buyer-audit")
    p.add_argument("--manager-queue")
    p.add_argument("--crm-preflight")
    args = p.parse_args()
    try:
        result = check(args.state_dir, buyer_audit=args.buyer_audit,
                       manager_queue=args.manager_queue,
                       crm_preflight=args.crm_preflight)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("EIS_BITRIX_READINESS=BLOCKED; ERROR=" + str(exc).splitlines()[0][:100])
        raise SystemExit(2)
    print("EIS_BITRIX_READINESS=" + json.dumps(result, ensure_ascii=True, sort_keys=True))
    print("EIS_BITRIX24_CONNECTOR=NOT_AUTHORIZED; CRM_WRITES=NO; READY_LEADS=0")


if __name__ == "__main__":
    main()
