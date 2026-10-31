"""Manager-safe EIS 1C buyer research queue, not automatic CRM-ready leads.

Consumes only the exact-INN FNS audit; never trusts RSS/AI to authorize contact.
No network, no mail, no contacts, no CRM access. One row per legal buyer, multiple
cited procurement signals, explicit reasons why a manager must still verify.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .eis_buyer_review import _day, _read, _url

MAX_AUDIT_BYTES = 2 * 1024 * 1024
ALLOWED_SIZES = {"MICRO", "SMALL"}
ALLOWED_REGIONS = {"Москва", "Московская область"}
EXPECTED_HOLDS = {
    "CRM_DUPLICATE_PENDING", "LEGAL_CONTACT_BASIS_PENDING",
    "MANAGER_APPROVAL_PENDING", "LIVE_SOURCE_RECHECK_REQUIRED",
}


def _safe_excel(raw: object) -> str:
    value = str(raw if raw is not None else "").replace("\x00", "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def assemble(audit_file: str | Path, *, today: date | None = None) -> dict:
    now = today or date.today()
    report = _read(audit_file, MAX_AUDIT_BYTES)
    if (report.get("mode") != "EIS_BUYER_FNS_REVIEW_ONLY"
            or report.get("crm_writes") != "NO"
            or report.get("outbound_messages") != "NO"):
        raise ValueError("EIS_MANAGER_SOURCE_NOT_AUDIT")
    if not 0 <= (now - _day(report.get("checked_on"), "AUDIT_DATE")).days <= 7:
        raise ValueError("EIS_MANAGER_AUDIT_STALE")
    counts = report.get("counts")
    rows = report.get("items")
    if not isinstance(counts, dict) or not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("EIS_MANAGER_AUDIT_FORMAT")
    if counts.get("rss_records") != len(rows) or counts.get("qualified_leads") != 0 or counts.get("crm_ready") != 0:
        raise ValueError("EIS_MANAGER_AUDIT_COUNTS_OR_PREAPPROVAL")
    grouped = {}
    duplicates = set()
    held = 0
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("EIS_MANAGER_INVALID_RECORD")
        src = item.get("source_url")
        if not isinstance(src, str) or _url(src, feed=False) != src:
            raise ValueError("EIS_MANAGER_UNTRUSTED_SOURCE_URL")
        sid = item.get("signal_id")
        if (not isinstance(sid, str) or
                sid != hashlib.sha256(src.encode("utf-8")).hexdigest()[:24] or sid in duplicates):
            raise ValueError("EIS_MANAGER_BAD_OR_DUPLICATE_SIGNAL")
        duplicates.add(sid)
        if (item.get("crm_ready") is not False
                or item.get("outbound_authorized") is not False
                or item.get("manager_approved") is not False
                or item.get("lead_score") is not None):
            raise ValueError("EIS_MANAGER_IMPORTED_APPROVAL_FORBIDDEN")
        inn = item.get("buyer_inn_operator_supplied")
        size = item.get("fns_size")
        region = item.get("buyer_region_operator_supplied")
        due = item.get("deadline_operator_supplied")
        published = item.get("published_on")
        holds = item.get("holds")
        if not isinstance(holds, list) or not all(isinstance(v, str) for v in holds):
            raise ValueError("EIS_MANAGER_HOLDS_INVALID")
        if (item.get("status") != "MANAGER_RESEARCH_CANDIDATE_NOT_APPROVED"
                or not EXPECTED_HOLDS.issubset(holds)
                or item.get("source_verified_live") is not False
                or item.get("procurement_status_operator_supplied") != "ACCEPTING"
                or item.get("scope_operator_supplied") != "EXTERNAL_1C_SERVICE"
                or size not in ALLOWED_SIZES or region not in ALLOWED_REGIONS
                or not isinstance(inn, str) or len(inn) != 10 or not inn.isascii() or not inn.isdigit()
                or not isinstance(item.get("fns_company_name"), str)
                or not item["fns_company_name"].strip()
                or not isinstance(item.get("title"), str) or not item["title"].strip()
                or not isinstance(published, str)
                or not isinstance(due, str)):
            held += 1
            continue
        if not 0 <= (now - _day(published, "NOTICE_PUBLICATION")).days <= 30:
            held += 1
            continue
        if _day(due, "NOTICE_DEADLINE") < now:
            held += 1
            continue
        company = grouped.setdefault(inn, {
            "buyer_inn": inn, "company_name": item["fns_company_name"],
            "region": region, "fns_size": size,
            "review_status": "MANAGER_REVIEW_ONLY",
            "suggested_service": "1C maintenance / implementation",
            "why_now": "Published external 1C procurement request; live status must be rechecked",
            "source_signals": [],
            "decision_required": "Verify official notice and terms, current buyer intent, existing client, CRM duplicate, channel legality and stops",
            "holds": sorted(EXPECTED_HOLDS), "contact": None,
            "lead_score": None, "crm_ready": False,
            "outbound_authorized": False,
        })
        if company["company_name"] != item["fns_company_name"] or company["region"] != region or company["fns_size"] != size:
            raise ValueError("EIS_MANAGER_CONFLICTING_FNS_ENTITY")
        company["source_signals"].append({
            "signal_id": sid, "notice_url": src,
            "title": item["title"], "published_on": published,
            "deadline_operator_supplied": due,
            "source_verified_live": False,
            "date_status": "FUTURE_DEADLINE_OPERATOR_RECHECK",
        })
    queue = sorted(grouped.values(), key=lambda c: (min(s["deadline_operator_supplied"] for s in c["source_signals"]), c["buyer_inn"]))
    return {
        "mode": "EIS_MANUAL_MANAGER_QUEUE",
        "generated_on": now.isoformat(),
        "counts": {"notice_rows_checked": len(rows), "held_notice_rows": held,
                   "manager_review_companies": len(queue),
                   "manager_review_notices": sum(len(c["source_signals"]) for c in queue),
                   "qualified_leads": 0, "crm_ready": 0},
        "companies": queue,
        "delivery": "LOCAL_FILES_ONLY_PENDING_APPROVAL",
        "crm_writes": "NO", "outbound_messages": "NO",
        "note": "Not a list of ready leads. Review each official tender, FNS buyer size, CRM duplicate/stop and legal channel. Do not send to live Bitrix24.",
    }


def save(result: dict, json_path: str | Path, csv_path: str | Path) -> dict:
    root = Path("exports").resolve()
    a = Path(json_path).resolve()
    b = Path(csv_path).resolve()
    if (a == b or a.suffix.lower() != ".json" or b.suffix.lower() != ".csv"
            or not a.is_relative_to(root) or not b.is_relative_to(root)
            or a.exists() or b.exists()):
        raise ValueError("EIS_MANAGER_NEW_FILES_MUST_BE_UNDER_EXPORTS")
    a.parent.mkdir(parents=True, exist_ok=True)
    with a.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    with b.open("x", encoding="utf-8-sig", newline="") as out:
        fields = ["buyer_inn", "company_name", "region", "fns_size",
                  "review_status", "notice_count", "first_deadline",
                  "notice_urls", "why_now", "crm_ready"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for company in result["companies"]:
            signals = company["source_signals"]
            writer.writerow({
                "buyer_inn": company["buyer_inn"],
                "company_name": _safe_excel(company["company_name"]),
                "region": company["region"], "fns_size": company["fns_size"],
                "review_status": company["review_status"],
                "notice_count": len(signals),
                "first_deadline": min(s["deadline_operator_supplied"] for s in signals),
                "notice_urls": _safe_excel("; ".join(s["notice_url"] for s in signals)),
                "why_now": _safe_excel(company["why_now"]), "crm_ready": "NO",
            })
    return {"json": str(a), "csv": str(b), "crm_writes": "NO"}


def main() -> None:
    p = argparse.ArgumentParser(description="EIS buyer research -> manager review queue")
    p.add_argument("--audit", required=True)
    p.add_argument("--json", default="exports/eis_manager_review.json")
    p.add_argument("--csv", default="exports/eis_manager_review.csv")
    args = p.parse_args()
    try:
        report = assemble(args.audit)
        result = save(report, args.json, args.csv)
    except (ValueError, OSError) as exc:
        print("EIS_MANAGER_QUEUE=BLOCKED; ERROR_CODE=" + str(exc)[:100])
        raise SystemExit(2)
    print("EIS_MANAGER_QUEUE=" + json.dumps(report["counts"], sort_keys=True))
    print("FILES=" + json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
