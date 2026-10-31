"""Four bounded official EIS RSS 1C service searches; research only.

Operators must first have a successfully parsed official RSS report. Only
the existing searchString parameter is replaced; we do not guess undocumented
EIS filters, claim regional specificity, or certify the buyer as small business.
No CRM or contact scraping. Exactly one request per term, no retries.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .eis_rss import _url, fetch, SOURCE_NAME, save

SEARCH_PHRASES = (
    ("support", "сопровождение 1С"),
    ("technical", "техническая поддержка 1С"),
    ("customization", "доработка 1С"),
    ("setup", "настройка 1С"),
)
MAX_REPORT_BYTES = 2 * 1024 * 1024


def targeted_url(original: str, phrase: str) -> str:
    _url(original, feed=True)
    if phrase not in dict(SEARCH_PHRASES).values():
        raise ValueError("EIS_TARGET_UNAPPROVED_PHRASE")
    components = urlsplit(original)
    params = parse_qsl(components.query, keep_blank_values=True)
    if sum(k == "searchString" for k, _ in params) != 1:
        raise ValueError("EIS_TARGET_SEARCH_STRING_AMBIGUOUS")
    if not any(k == "searchString" and v.strip() for k, v in params):
        raise ValueError("EIS_TARGET_SEARCH_STRING_MISSING")
    replaced = [(k, phrase if k == "searchString" else v) for k, v in params]
    result = urlunsplit((components.scheme, components.netloc, components.path,
                         urlencode(replaced), ""))
    _url(result, feed=True)
    return result


def run_profiles(original: str, *, today: date | None = None,
                 downloader=fetch) -> dict:
    """Deterministic sequential searches, duplicates keyed by official notice URL."""
    seen: set[str] = set()
    profiles = []
    all_links = []
    for name, phrase in SEARCH_PHRASES:
        # No fallback if the source rejects or rate limits an endpoint.
        parsed = downloader(url=targeted_url(original, phrase), today=today)
        if (not isinstance(parsed, dict)
                or parsed.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
                or parsed.get("source") != SOURCE_NAME
                or parsed.get("crm_writes") != "NO"
                or parsed.get("outbound_messages") != "NO"):
            raise ValueError("EIS_TARGET_UNTRUSTED_SOURCE_REPORT")
        notices = parsed.get("items")
        counts = parsed.get("counts")
        if not isinstance(notices, list) or not isinstance(counts, dict):
            raise ValueError("EIS_TARGET_BAD_REPORT")
        accepted = counts.get("candidates_for_identity_review")
        scanned = counts.get("items_seen", 0)
        if (type(accepted) is not int or type(scanned) is not int
                or not 0 <= accepted <= scanned <= 500 or accepted != len(notices)
                or counts.get("crm_ready") != 0
                or counts.get("qualified_small_business_leads") != 0):
            raise ValueError("EIS_TARGET_UNSAFE_COUNTS")
        new_items = 0
        for item in notices:
            if not isinstance(item, dict):
                raise ValueError("EIS_TARGET_INVALID_NOTICE")
            url = item.get("source_url")
            if not isinstance(url, str) or _url(url, feed=False) != url:
                raise ValueError("EIS_TARGET_BAD_NOTICE_URL")
            if (item.get("crm_ready") is not False
                    or item.get("buyer_inn") is not None
                    or item.get("outbound_authorized") is not False):
                raise ValueError("EIS_TARGET_PREAPPROVED_NOTICE")
            if url not in seen:
                seen.add(url)
                new_items += 1
                all_links.append(url)
        profiles.append({
            "profile_id": name,
            "query": phrase,
            "items_seen": scanned,
            "scope_rejected": counts.get("not_explicit_external_1c_service", 0),
            "research_candidates": accepted,
            "new_unique_notices": new_items,
            "rss_report": parsed,
        })
    return {
        "mode": "EIS_TARGETED_1C_RESEARCH_ONLY",
        "profiles": profiles,
        "counts": {"profiles_checked": len(profiles),
                   "unique_research_notices": len(all_links),
                   "qualified_leads": 0, "crm_ready": 0},
        "regions_covered": "UNVERIFIED_FROM_RSS_LINK",
        "buyer_fns_status": "MANUAL_EXACT_INN_REQUIRED",
        "crm_writes": "NO", "outbound_messages": "NO",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Four official 1C RSS queries; research only")
    p.add_argument("--previous-report", required=True)
    p.add_argument("--prefix", default="exports/eis_targeted")
    args = p.parse_args()
    try:
        previous = Path(args.previous_report)
        if not previous.is_file() or not 1 <= previous.stat().st_size <= MAX_REPORT_BYTES:
            raise ValueError("EIS_TARGET_PREVIOUS_REPORT_MISSING")
        original = json.loads(previous.read_text(encoding="utf-8-sig"))
        if (not isinstance(original, dict)
                or original.get("mode") != "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
                or original.get("source") != SOURCE_NAME
                or original.get("crm_writes") != "NO"
                or original.get("outbound_messages") != "NO"):
            raise ValueError("EIS_TARGET_SOURCE_NOT_VERIFIED")
        url = original.get("search_rss_url")
        if not isinstance(url, str):
            raise ValueError("EIS_TARGET_SOURCE_LINK_INVALID")
        targeted_url(url, SEARCH_PHRASES[0][1])
        output_prefix = Path(args.prefix).resolve()
        if not output_prefix.is_relative_to(Path("exports").resolve()):
            raise ValueError("EIS_TARGET_OUTPUT_NOT_UNDER_EXPORTS")
        if any(output_prefix.with_name(output_prefix.name + "_" + name + ".json").exists()
               for name, _ in SEARCH_PHRASES) or output_prefix.with_name(
                   output_prefix.name + "_summary.json").exists():
            raise ValueError("EIS_TARGET_OUTPUT_ALREADY_EXISTS")
        batch = run_profiles(url)
        for profile in batch["profiles"]:
            file = output_prefix.with_name(output_prefix.name + "_" + profile["profile_id"] + ".json")
            save(profile["rss_report"], file)
        rows = [{k: v for k, v in profile.items() if k != "rss_report"}
                for profile in batch["profiles"]]
        summary = {k: v for k, v in batch.items() if k != "profiles"}
        summary["profiles"] = rows
        final_path = output_prefix.with_name(output_prefix.name + "_summary.json")
        with final_path.open("x", encoding="utf-8") as out:
            json.dump(summary, out, ensure_ascii=True, indent=2)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print("EIS_TARGETED=BLOCKED; REASON=" + str(exc).splitlines()[0][:100])
        raise SystemExit(2)
    print("EIS_TARGETED=" + json.dumps(summary, ensure_ascii=True))
    print("SUMMARY_FILE=" + str(final_path))
    print("EIS_TARGETED_QUALIFIED_LEADS=0; CRM_WRITES=NO")


if __name__ == "__main__":
    main()
