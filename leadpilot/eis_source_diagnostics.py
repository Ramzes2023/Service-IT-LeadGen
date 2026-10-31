"""Read-only explanation for zero EIS research candidates from a saved report.

This module cannot qualify or send leads; it only validates summary counters
created by the official EIS RSS parser. Never emits buyer data or URL queries.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ALLOWED_SOURCES = {"eis-zakupki-gov-ru-official-rss"}
MAX_REPORT_BYTES = 2 * 1024 * 1024
FILTERS = (
    "not_explicit_external_1c_service", "unverified_notice_link",
    "future_date_rejected", "stale_rejected", "duplicate_notices",
    "publication_date_unknown",
    "scope_subject_label_missing", "scope_subject_empty",
    "scope_bitrix_out_of_scope", "scope_service_negated",
    "scope_no_1c_in_purchase_subject",
    "scope_no_external_service_in_purchase_subject",
)


def diagnose(report: dict) -> dict:
    if (not isinstance(report, dict)
            or report.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
            or report.get("source") not in ALLOWED_SOURCES
            or report.get("crm_writes") != "NO"
            or report.get("outbound_messages") != "NO"):
        raise ValueError("EIS_AUDIT_INVALID_ORIGIN")
    counts = report.get("counts")
    rows = report.get("items")
    if not isinstance(counts, dict) or not isinstance(rows, list):
        raise ValueError("EIS_AUDIT_INVALID_STRUCTURE")
    keys = ("items_seen", "candidates_for_identity_review",
            "qualified_small_business_leads", "crm_ready", *FILTERS)
    nums = {}
    for k in keys:
        n = counts.get(k, 0)
        if type(n) is not int or not 0 <= n <= 500:
            raise ValueError("EIS_AUDIT_BAD_COUNTER")
        nums[k] = n
    seen = nums["items_seen"]
    n = nums["candidates_for_identity_review"]
    if (n != len(rows) or n > seen or nums["qualified_small_business_leads"] != 0
            or nums["crm_ready"] != 0):
        raise ValueError("EIS_AUDIT_UNSAFE_OR_INCONSISTENT_COUNTS")
    if seen == 0:
        state = "OFFICIAL_RSS_HAS_ZERO_ITEMS"
    elif n > 0:
        state = "RESEARCH_ITEMS_FOUND_BUYER_STILL_UNVERIFIED"
    elif nums["not_explicit_external_1c_service"] == seen:
        state = "ALL_ITEMS_FAILED_EXPLICIT_EXTERNAL_1C_SCOPE"
    elif nums["stale_rejected"] == seen:
        state = "ALL_ITEMS_OUTSIDE_30_DAY_WINDOW"
    elif nums["unverified_notice_link"] == seen:
        state = "ALL_ITEMS_FAILED_NOTICE_URL_VALIDATION"
    else:
        state = "ZERO_AFTER_MULTIPLE_RESEARCH_GATES"
    raw_url = report.get("search_rss_url", "")
    if not isinstance(raw_url, str):
        raise ValueError("EIS_AUDIT_SOURCE_URL_MISSING")
    parsed = urlsplit(raw_url)
    if parsed.scheme != "https" or parsed.hostname != "zakupki.gov.ru":
        raise ValueError("EIS_AUDIT_SOURCE_NOT_OFFICIAL")
    query = parse_qs(parsed.query)
    source_profile = {
        "fz44_filter_present": "on" in query.get("fz44", []),
        "fz223_filter_present": "on" in query.get("fz223", []),
        "search_term_present": bool(query.get("searchString")),
    }
    return {
        "mode": "EIS_RSS_COVERAGE_DIAGNOSTICS_ONLY",
        "status": state,
        "seen": seen,
        "research_candidates": n,
        "rejection_counts": {k: nums[k] for k in FILTERS},
        "scope_breakdown": {k.removeprefix("scope_"): nums[k] for k in FILTERS if k.startswith("scope_")},
        "scope_sample_count": len(report.get("scope_rejection_samples", [])) if isinstance(report.get("scope_rejection_samples", []), list) else 0,
        "source_profile": source_profile,
        "qualified_leads": 0,
        "crm_ready": False,
        "crm_writes": "NO",
        "outbound_messages": "NO",
    }


def _public_samples(report: dict, max_samples: int = 8) -> list[dict]:
    """Public titles only, from a trusted local EIS parser report.

    Do not print URLs, buyer details, contacts, search credentials or source
    document bodies. ASCII escaping survives legacy Windows PowerShell pipes.
    """
    samples = report.get("scope_rejection_samples", [])
    if not isinstance(samples, list) or len(samples) > 15:
        raise ValueError("EIS_AUDIT_SAMPLES_INVALID")
    allowed_reasons = {
        "SUBJECT_LABEL_MISSING", "SUBJECT_EMPTY", "BITRIX_OUT_OF_SCOPE",
        "SERVICE_NEGATED", "NO_1C_IN_PURCHASE_SUBJECT",
        "NO_EXTERNAL_SERVICE_IN_PURCHASE_SUBJECT",
    }
    result = []
    for sample in samples[:max_samples]:
        if not isinstance(sample, dict):
            raise ValueError("EIS_AUDIT_SAMPLE_INVALID")
        reason = sample.get("reason")
        if reason not in allowed_reasons:
            raise ValueError("EIS_AUDIT_SAMPLE_REASON_INVALID")
        record = {"reason": reason}
        for field, limit in (("rss_title", 180), ("procurement_subject", 250)):
            value = sample.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > limit):
                raise ValueError("EIS_AUDIT_SAMPLE_TEXT_INVALID")
            record[field] = value
        result.append(record)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain EIS 0 candidates from saved JSON, offline")
    parser.add_argument("--report", required=True, help="Local exports/eis_source_*.json")
    parser.add_argument("--show-public-samples", action="store_true",
                        help="Print at most 8 public RSS titles as ASCII-escaped JSON")
    args = parser.parse_args()
    p = Path(args.report)
    try:
        if not p.is_file() or not 1 <= p.stat().st_size <= MAX_REPORT_BYTES:
            raise ValueError("EIS_AUDIT_REPORT_MISSING_OR_OVERSIZED")
        report = json.loads(p.read_text(encoding="utf-8-sig"))
        summary = diagnose(report)
        samples = _public_samples(report) if args.show_public_samples else []
    except (ValueError, OSError, UnicodeError) as err:
        print("EIS_COVERAGE_AUDIT=BLOCKED; REASON=" + str(err)[:80])
        raise SystemExit(2)
    print("EIS_COVERAGE_AUDIT=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.show_public_samples:
        print("EIS_PUBLIC_SAMPLES_ASCII=" + json.dumps(samples, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
