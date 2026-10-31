"""Read-only, two-request EIS RSS search-integrity diagnostic.

A negative control changes ONLY the existing documented RSS searchString
parameter. Same notices in the control and target suggest the search filter
is not being applied (or cached), never imply a real commercial lead.
No CRM, accounts, contact extraction, bypass, retries or email.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .eis_rss import _url, fetch, SOURCE_NAME

CONTROL = "SERVICEIT_NEVER_MATCH_EXTERNAL_1C_TEST_8392571046"
MAX_INPUT_BYTES = 2 * 1024 * 1024


def negative_control_url(url: str) -> str:
    _url(url, feed=True)
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    words = [value for key, value in pairs if key == "searchString"]
    if len(words) != 1 or not 1 <= len(words[0].strip()) <= 250:
        raise ValueError("EIS_SEARCH_ORIGINAL_QUERY_NOT_UNIQUE_OR_EMPTY")
    if words[0] == CONTROL:
        raise ValueError("EIS_SEARCH_CONTROL_CANNOT_MATCH_PRIMARY")
    modified = [(key, CONTROL if key == "searchString" else value)
                for key, value in pairs]
    candidate = urlunsplit((parts.scheme, parts.netloc, parts.path,
                            urlencode(modified), ""))
    _url(candidate, feed=True)
    return candidate


def compare_reports(original: dict, control: dict) -> dict:
    for report in (original, control):
        if (not isinstance(report, dict)
                or report.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
                or report.get("source") != SOURCE_NAME
                or report.get("crm_writes") != "NO"
                or report.get("outbound_messages") != "NO"
                or not isinstance(report.get("counts"), dict)):
            raise ValueError("EIS_SEARCH_UNTRUSTED_RSS_RESEARCH")
    # Older RSS snapshots omit items_seen on an actually empty channel.
    # Zero is the only safe default; non-integer present values still fail.
    a = original["counts"].get("items_seen", 0)
    b = control["counts"].get("items_seen", 0)
    ac = original.get("all_notices_fingerprint_count")
    bc = control.get("all_notices_fingerprint_count")
    ah = original.get("all_notices_fingerprint_sha256")
    bh = control.get("all_notices_fingerprint_sha256")
    if any(type(x) is not int or not 0 <= x <= 500 for x in (a, b, ac, bc)):
        raise ValueError("EIS_SEARCH_INVALID_COUNT")
    if ac > a or bc > b:
        raise ValueError("EIS_SEARCH_INCONSISTENT_COUNTS")
    if (ah is not None and (not isinstance(ah, str) or not re.fullmatch(r"[0-9a-f]{64}", ah))
            or bh is not None and (not isinstance(bh, str) or not re.fullmatch(r"[0-9a-f]{64}", bh))):
        raise ValueError("EIS_SEARCH_INVALID_FINGERPRINT")
    if a == 0 and b == 0:
        status = "INCONCLUSIVE_BOTH_EMPTY"
    elif b == 0:
        status = "NEGATIVE_CONTROL_EMPTY_SEARCH_MAY_BE_APPLIED"
    elif ac >= 10 and ac == bc and ah is not None and ah == bh:
        status = "NEGATIVE_CONTROL_IDENTICAL_NOTICE_SET_SEARCH_UNTRUSTED"
    elif ah is not None and bh is not None and ah != bh:
        status = "NEGATIVE_CONTROL_DIFFERENT_NOTICE_SETS_NOT_PROOF_OF_SCOPE"
    else:
        status = "INCONCLUSIVE_DIFFERENT_COUNTS_OR_UNVERIFIABLE_IDS"
    return {
        "mode": "EIS_OFFICIAL_RSS_SEARCH_NEGATIVE_CONTROL",
        "status": status,
        "original_items_seen": a,
        "control_items_seen": b,
        "original_unique_official_notice_urls": ac,
        "control_unique_official_notice_urls": bc,
        "original_research_candidates": original["counts"].get("candidates_for_identity_review", 0),
        "verified_small_business_buyers": 0,
        "qualified_leads": 0,
        "crm_writes": "NO",
        "outbound_messages": "NO",
        "note": "A control probe cannot prove RSS query settings, customer region, active demand, or buyer FNS size.",
    }


def probe(url: str, *, today: date | None = None) -> dict:
    alternate = negative_control_url(url)
    primary = fetch(url=url, today=today)
    negative = fetch(url=alternate, today=today)
    return compare_reports(primary, negative)


def main() -> None:
    p = argparse.ArgumentParser(description="Two official EIS RSS requests to diagnose ignored searchString")
    p.add_argument("--previous-report", required=True, help="Already saved exports/eis_scope_*.json")
    p.add_argument("--output", default="exports/eis_search_integrity.json")
    args = p.parse_args()
    source = Path(args.previous_report)
    destination = Path(args.output).resolve()
    try:
        if not source.is_file() or not 1 <= source.stat().st_size <= MAX_INPUT_BYTES:
            raise ValueError("EIS_SEARCH_INPUT_MISSING_OR_OVERSIZED")
        prior = json.loads(source.read_text(encoding="utf-8-sig"))
        if (not isinstance(prior, dict)
                or prior.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
                or prior.get("source") != SOURCE_NAME
                or prior.get("crm_writes") != "NO"
                or prior.get("outbound_messages") != "NO"):
            raise ValueError("EIS_SEARCH_PREVIOUS_REPORT_UNTRUSTED")
        url = prior.get("search_rss_url")
        if not isinstance(url, str):
            raise ValueError("EIS_SEARCH_URL_MISSING")
        negative_control_url(url)
        root = Path("exports").resolve()
        if (not destination.is_relative_to(root)
                or destination.suffix.lower() != ".json" or destination.exists()):
            raise ValueError("EIS_SEARCH_OUTPUT_MUST_BE_NEW_UNDER_EXPORTS")
        result = probe(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=True, indent=2)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        # No raw URL, credentials, RSS body or exception tracebacks in output.
        code = str(exc).splitlines()[0][:90]
        print("EIS_SEARCH_PROBE=BLOCKED; REASON=" + code)
        raise SystemExit(2)
    print("EIS_SEARCH_PROBE=" + json.dumps(result, ensure_ascii=True, sort_keys=True))
    print("OUTPUT=" + str(destination))
    print("EIS_SEARCH_QUALIFIED_LEADS=0; CRM_WRITES=NO")


if __name__ == "__main__":
    main()
