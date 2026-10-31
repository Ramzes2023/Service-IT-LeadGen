"""EIS RSS -> operator-supplied buyer identity -> official FNS cohort audit.

NO CRM, contact extraction, AI, sales outreach, or automatic commercial approval.
EIS mostly describes public procurement, not the small-business B2B pilot:
the buyer's legal INN and FNS SME size must match exactly.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from .eis_rss import SOURCE_NAME, _url
from .sme_vacancy_audit import _read_fns_candidates
from .verified_import import is_valid_inn

REGIONS = {"Москва", "Московская область"}
STATUSES = {"ACCEPTING", "CLOSED", "CANCELLED", "UNKNOWN"}
SCOPES = {"EXTERNAL_1C_SERVICE", "OTHER_SERVICE", "UNCERTAIN"}
MAX_FILE_SIZE = 2 * 1024 * 1024


def _read(path: str | Path, max_bytes: int) -> dict:
    p = Path(path)
    if not p.is_file() or not 1 <= p.stat().st_size <= max_bytes:
        raise ValueError("EIS_INPUT_MISSING_OR_OVERSIZED")
    try:
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ValueError("EIS_INVALID_JSON") from None
    if not isinstance(obj, dict):
        raise ValueError("EIS_INPUT_OBJECT_REQUIRED")
    return obj


def _day(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError("EIS_INVALID_" + field)
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError("EIS_INVALID_" + field) from None
    if d.isoformat() != value:
        raise ValueError("EIS_INVALID_" + field)
    return d


def review(eis_file: str | Path, fns_file: str | Path, operator_file: str | Path,
           *, today: date | None = None) -> dict:
    now = today or date.today()
    research = _read(eis_file, MAX_FILE_SIZE)
    if (research.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
            or research.get("source") != SOURCE_NAME
            or research.get("crm_writes") != "NO"
            or research.get("outbound_messages") != "NO"):
        raise ValueError("EIS_RESEARCH_ORIGIN_UNTRUSTED")
    if not 0 <= (now - _day(research.get("checked_on"), "RSS_DATE")).days <= 30:
        raise ValueError("EIS_RSS_STALE")
    source_items = research.get("items")
    if not isinstance(source_items, list) or len(source_items) > 500:
        raise ValueError("EIS_RSS_ITEMS_INVALID")
    count = research.get("counts")
    if (not isinstance(count, dict)
            or count.get("candidates_for_identity_review") != len(source_items)
            or count.get("crm_ready") != 0
            or count.get("qualified_small_business_leads") != 0):
        raise ValueError("EIS_RSS_COUNTS_INVALID")
    source = {}
    for row in source_items:
        if not isinstance(row, dict):
            raise ValueError("EIS_RSS_ROW_INVALID")
        url = row.get("source_url")
        if _url(url, feed=False) != url:
            raise ValueError("EIS_NOTICE_URL_INVALID")
        if (row.get("source_name") != SOURCE_NAME
                or row.get("buyer_inn") is not None
                or row.get("crm_ready") is not False
                or row.get("outbound_authorized") is not False
                or row.get("lead_score") is not None):
            raise ValueError("EIS_RSS_PREAPPROVED_OR_FABRICATED_BUYER")
        sid = row.get("signal_id")
        if (not isinstance(sid, str) or sid != hashlib.sha256(url.encode()).hexdigest()[:24]
                or url in source):
            raise ValueError("EIS_NOTICE_DUPLICATE_OR_BAD_SIGNAL_ID")
        source[url] = row
    operator = _read(operator_file, 200_000)
    if operator.get("mode") != "EIS_MANUAL_BUYER_FACTS" or set(operator) != {"mode", "items"}:
        raise ValueError("EIS_OPERATOR_SCHEMA_INVALID")
    entries = operator["items"]
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError("EIS_OPERATOR_ITEMS_INVALID")
    reviews = {}
    for row in entries:
        required = {"source_url", "buyer_inn", "buyer_region", "observed_on",
                    "procurement_status", "deadline_on", "scope_status",
                    "buyer_identity_note", "review_state"}
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("EIS_OPERATOR_FIELDS_INVALID")
        url = row["source_url"]
        if not isinstance(url, str) or url not in source or url in reviews:
            raise ValueError("EIS_OPERATOR_URL_UNKNOWN_OR_DUPLICATE")
        if row["review_state"] != "PENDING":
            raise ValueError("EIS_OPERATOR_CANNOT_SELF_APPROVE")
        if not is_valid_inn(row["buyer_inn"]) or len(row["buyer_inn"]) != 10:
            raise ValueError("EIS_BUYER_INN_INVALID")
        if (not isinstance(row["buyer_region"], str)
                or not 1 <= len(row["buyer_region"]) <= 100
                or not isinstance(row["procurement_status"], str)
                or not isinstance(row["scope_status"], str)
                or row["procurement_status"] not in STATUSES
                or row["scope_status"] not in SCOPES):
            raise ValueError("EIS_OPERATOR_CLASSIFICATION_INVALID")
        if (not isinstance(row["buyer_identity_note"], str)
                or not 15 <= len(row["buyer_identity_note"].strip()) <= 500):
            raise ValueError("EIS_OPERATOR_IDENTITY_SOURCE_NOTE_REQUIRED")
        observed = _day(row["observed_on"], "OBSERVATION_DATE")
        if not 0 <= (now - observed).days <= 7:
            raise ValueError("EIS_OPERATOR_OBSERVATION_STALE")
        due = _day(row["deadline_on"], "DEADLINE") if row["deadline_on"] is not None else None
        reviews[url] = {**row, "deadline_on": due.isoformat() if due else None}
    companies, fns_day = _read_fns_candidates(fns_file, now)
    results = []
    counts = {
        "rss_records": len(source), "buyer_facts_submitted": len(reviews),
        "operator_identity_pending": 0, "fns_size_region_matched": 0,
        "manager_research_candidates": 0, "excluded_or_held": 0,
        "qualified_leads": 0, "crm_ready": 0,
    }
    for url, raw in sorted(source.items()):
        review_row = reviews.get(url)
        holds = ["CRM_DUPLICATE_PENDING", "LEGAL_CONTACT_BASIS_PENDING",
                 "MANAGER_APPROVAL_PENDING", "LIVE_SOURCE_RECHECK_REQUIRED"]
        fns_company = None
        if review_row is None:
            status = "BUYER_IDENTIFICATION_PENDING"
            holds.append("BUYER_INN_NOT_REVIEWED")
            counts["operator_identity_pending"] += 1
        else:
            inn = review_row["buyer_inn"]
            fns_company = companies.get(inn)
            if fns_company is None:
                holds.append("BUYER_NOT_IN_VERIFIED_FNS_SMALL_BUSINESS_COHORT")
            elif fns_company["region"] != review_row["buyer_region"]:
                holds.append("BUYER_REGION_CONFLICT")
            else:
                counts["fns_size_region_matched"] += 1
            if review_row["buyer_region"] not in REGIONS:
                holds.append("OUTSIDE_MOSCOW_PILOT")
            if review_row["scope_status"] != "EXTERNAL_1C_SERVICE":
                holds.append("SERVICE_SCOPE_UNVERIFIED")
            if review_row["procurement_status"] != "ACCEPTING":
                holds.append("ACTIVE_PROCEDURE_UNVERIFIED")
            if review_row["deadline_on"] is None:
                holds.append("DEADLINE_UNKNOWN")
            elif review_row["deadline_on"] < now.isoformat():
                holds.append("DEADLINE_EXPIRED")
            eligible = (fns_company is not None
                        and fns_company["region"] == review_row["buyer_region"]
                        and review_row["buyer_region"] in REGIONS
                        and review_row["scope_status"] == "EXTERNAL_1C_SERVICE"
                        and review_row["procurement_status"] == "ACCEPTING"
                        and review_row["deadline_on"] is not None
                        and review_row["deadline_on"] >= now.isoformat())
            if eligible:
                status = "MANAGER_RESEARCH_CANDIDATE_NOT_APPROVED"
                counts["manager_research_candidates"] += 1
            else:
                status = "OUTSIDE_PILOT_OR_NEEDS_REVIEW"
                counts["excluded_or_held"] += 1
        results.append({
            "signal_id": raw["signal_id"],
            "source_url": url,
            "title": raw["title"],
            "published_on": raw.get("published_on"),
            "buyer_inn_operator_supplied": review_row["buyer_inn"] if review_row else None,
            "buyer_region_operator_supplied": review_row["buyer_region"] if review_row else None,
            "buyer_identity_review_note": review_row["buyer_identity_note"] if review_row else None,
            "fns_company_name": fns_company["company_name"] if fns_company else None,
            "fns_size": fns_company["fns_category"] if fns_company else "UNVERIFIED",
            "fns_snapshot": fns_day,
            "procurement_status_operator_supplied": review_row["procurement_status"] if review_row else "UNKNOWN",
            "deadline_operator_supplied": review_row["deadline_on"] if review_row else None,
            "scope_operator_supplied": review_row["scope_status"] if review_row else "UNCERTAIN",
            "status": status, "holds": holds,
            "source_verified_live": False, "sales_intent_verified": False,
            "crm_duplicate_check": "PENDING", "manager_approved": False,
            "crm_ready": False, "outbound_authorized": False, "lead_score": None,
        })
    return {
        "mode": "EIS_BUYER_FNS_REVIEW_ONLY", "checked_on": now.isoformat(),
        "fns_snapshot": fns_day, "counts": counts, "items": results,
        "warning": "Operator-supplied notice facts are unverified by this offline tool. No live buyer intent or contact permission established.",
        "crm_writes": "NO", "outbound_messages": "NO", "valyqon_changes": "NONE",
    }


def save(report: dict, filename: str | Path) -> str:
    output = Path(filename).resolve()
    root = Path("exports").resolve()
    if not output.is_relative_to(root) or output.suffix.lower() != ".json" or output.exists():
        raise ValueError("EIS_REVIEW_OUTPUT_MUST_BE_NEW_JSON_UNDER_EXPORTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return str(output)


def main():
    parser = argparse.ArgumentParser(description="EIS buyer review vs official small-business FNS cohort")
    parser.add_argument("--eis", required=True)
    parser.add_argument("--fns", required=True)
    parser.add_argument("--buyer-facts", required=True)
    parser.add_argument("--output", default="exports/eis_buyer_review.json")
    args = parser.parse_args()
    try:
        report = review(args.eis, args.fns, args.buyer_facts)
        path = save(report, args.output)
    except (ValueError, OSError) as exc:
        print("EIS_BUYER_REVIEW=BLOCKED; REASON=" + str(exc)[:100])
        raise SystemExit(2)
    print("EIS_BUYER_REVIEW=RESEARCH_ONLY; " + json.dumps(report["counts"], sort_keys=True))
    print("OUTPUT=" + path + "; CRM_WRITES=NO; OUTREACH=NO")


if __name__ == "__main__":
    main()
