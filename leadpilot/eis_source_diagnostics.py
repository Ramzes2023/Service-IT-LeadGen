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
        "source_profile": source_profile,
        "qualified_leads": 0,
        "crm_ready": False,
        "crm_writes": "NO",
        "outbound_messages": "NO",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Explain EIS 0 candidates from saved JSON, offline")
    parser.add_argument("--report", required=True, help="Local exports/eis_source_*.json")
    args = parser.parse_args()
    p = Path(args.report)
    try:
        if not p.is_file() or not 1 <= p.stat().st_size <= MAX_REPORT_BYTES:
            raise ValueError("EIS_AUDIT_REPORT_MISSING_OR_OVERSIZED")
        report = json.loads(p.read_text(encoding="utf-8-sig"))
        summary = diagnose(report)
    except (ValueError, OSError, UnicodeError) as err:
        print("EIS_COVERAGE_AUDIT=BLOCKED; REASON=" + str(err)[:80])
        raise SystemExit(2)
    print("EIS_COVERAGE_AUDIT=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
