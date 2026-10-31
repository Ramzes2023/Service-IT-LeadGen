"""Create an evidence-review queue from four official EIS RSS research snapshots.

No automatic lead qualification, CRM operations, correspondence or contact
enrichment. 44-FZ tender buyers are never assumed to be small businesses.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from .eis_rss import SOURCE_NAME, _url

PROFILE_IDS = ("support", "technical", "customization", "setup")
SOURCE_MODE = "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
BATCH_MODE = "EIS_TARGETED_1C_RESEARCH_ONLY"
MAX_BYTES = 2 * 1024 * 1024
PUBLIC_SECTOR_HINT = re.compile(
    r"(?i)(?:^|\W)(?:ФГБУ|ФКУ|ФБУ|ГБУ|ГКУ|МБУ|МКУ|ГАУ|МАУ|"
    r"ГБУЗ|ГКУЗ|ФГБОУ|ГБОУ|ГУП|МУП|КАЗЕНН(?:ОЕ|ЫЙ)|БЮДЖЕТН(?:ОЕ|ЫЙ))(?=\W|$)"
)
COLUMNS = ("notice_id", "published_on", "title", "buyer_name_rss_unverified",
           "public_sector_hint", "seen_in_profiles", "official_notice_url",
           "buyer_inn", "fns_size", "buyer_region", "deadline",
           "review_status", "verification_holds", "crm_ready")


def _read_json(path: Path) -> dict:
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_BYTES:
        raise ValueError("EIS_REVIEW_INPUT_MISSING_OR_OVERSIZED")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("EIS_REVIEW_INPUT_NOT_OBJECT")
    return value


def _integer(n: object, cap: int = 500) -> int:
    if type(n) is not int or not 0 <= n <= cap:
        raise ValueError("EIS_REVIEW_INVALID_COUNTER")
    return n


def build_queue(summary_path: str | Path, *, today: date | None = None) -> dict:
    """Validate all four actual files and explain which notices require review."""
    summary = Path(summary_path).resolve()
    doc = _read_json(summary)
    if (doc.get("mode") != BATCH_MODE or doc.get("crm_writes") != "NO"
            or doc.get("outbound_messages") != "NO"
            or doc.get("buyer_fns_status") != "MANUAL_EXACT_INN_REQUIRED"):
        raise ValueError("EIS_REVIEW_UNTRUSTED_TARGETED_SUMMARY")
    profiles = doc.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(PROFILE_IDS):
        raise ValueError("EIS_REVIEW_PROFILE_LIST_INVALID")
    if not summary.name.endswith("_summary.json"):
        raise ValueError("EIS_REVIEW_SUMMARY_FILENAME_INVALID")
    base = summary.name[:-len("_summary.json")]
    day = today or date.today()
    unique: dict[str, dict] = {}
    profile_rows = []
    for expected_id, profile in zip(PROFILE_IDS, profiles):
        if not isinstance(profile, dict) or profile.get("profile_id") != expected_id:
            raise ValueError("EIS_REVIEW_PROFILE_ORDER_INVALID")
        path = summary.parent / (base + "_" + expected_id + ".json")
        rss = _read_json(path)
        if (rss.get("mode") != SOURCE_MODE or rss.get("source") != SOURCE_NAME
                or rss.get("crm_writes") != "NO" or rss.get("outbound_messages") != "NO"):
            raise ValueError("EIS_REVIEW_UNTRUSTED_RSS")
        counts = rss.get("counts")
        items = rss.get("items")
        if not isinstance(counts, dict) or not isinstance(items, list):
            raise ValueError("EIS_REVIEW_RSS_STRUCTURE_INVALID")
        expected = _integer(profile.get("research_candidates"))
        number = _integer(counts.get("candidates_for_identity_review", 0))
        scanned = _integer(counts.get("items_seen", 0))
        if (expected != number or len(items) != expected or expected > scanned
                or counts.get("qualified_small_business_leads") != 0
                or counts.get("crm_ready") != 0 or profile.get("items_seen") != scanned):
            raise ValueError("EIS_REVIEW_RSS_COUNTER_MISMATCH")
        added = 0
        seen_in_profile = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("EIS_REVIEW_NOTICE_INVALID")
            url = item.get("source_url")
            if not isinstance(url, str) or _url(url, feed=False) != url:
                raise ValueError("EIS_REVIEW_NOTICE_NOT_OFFICIAL")
            uid = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
            if (item.get("signal_id") != uid or url in seen_in_profile or
                    item.get("buyer_inn") is not None or
                    item.get("crm_ready") is not False or
                    item.get("outbound_authorized") is not False or
                    item.get("manager_approved") is not False or
                    item.get("lead_score") is not None):
                raise ValueError("EIS_REVIEW_PREAPPROVED_OR_DUPLICATED_NOTICE")
            seen_in_profile.add(url)
            published = item.get("published_on")
            if published is not None:
                if not isinstance(published, str):
                    raise ValueError("EIS_REVIEW_DATE_INVALID")
                try:
                    age = (day - date.fromisoformat(published)).days
                except ValueError:
                    raise ValueError("EIS_REVIEW_DATE_INVALID") from None
                if not 0 <= age <= 30:
                    raise ValueError("EIS_REVIEW_STALE_OR_FUTURE_NOTICE")
            title = item.get("procurement_subject_from_rss") or item.get("title")
            buyer = item.get("buyer_name_rss_unverified")
            if not isinstance(title, str) or not 1 <= len(title) <= 700:
                raise ValueError("EIS_REVIEW_TITLE_INVALID")
            if buyer is not None and (not isinstance(buyer, str) or len(buyer) > 250):
                raise ValueError("EIS_REVIEW_BUYER_HINT_INVALID")
            if url not in unique:
                added += 1
                unique[url] = {
                    "notice_id": uid, "published_on": published or "",
                    "title": title, "buyer_name_rss_unverified": buyer or "",
                    "public_sector_hint": ("POSSIBLE_PUBLIC_INSTITUTION_REVIEW"
                        if buyer and PUBLIC_SECTOR_HINT.search(buyer)
                        else "UNVERIFIED_BUYER_CATEGORY"),
                    "seen_in_profiles": [expected_id],
                    "official_notice_url": url,
                    "buyer_inn": "", "fns_size": "UNVERIFIED",
                    "buyer_region": "UNVERIFIED", "deadline": "UNVERIFIED",
                    "review_status": "RESEARCH_ONLY_NOT_A_QUALIFIED_LEAD",
                    "verification_holds": (
                        "ORIGINAL_NOTICE_SCOPE;BUYER_EXACT_INN;FNS_MICRO_SMALL;"
                        "MOSCOW_OR_MO_REGION;ACTIVE_DEADLINE;LEGAL_CONTACT_BASIS;"
                        "CRM_EXISTING_CUSTOMER_DUPLICATE_STOPLIST;MANAGER_APPROVAL"),
                    "crm_ready": False,
                }
            else:
                unique[url]["seen_in_profiles"].append(expected_id)
        if _integer(profile.get("new_unique_notices")) != added:
            raise ValueError("EIS_REVIEW_CROSS_PROFILE_DEDUPE_MISMATCH")
        profile_rows.append({"profile_id": expected_id,
                             "items_seen": scanned, "research_candidates": expected,
                             "new_unique_notices": added})
    total = _integer(doc.get("counts", {}).get("unique_research_notices"), 2000)
    if total != len(unique):
        raise ValueError("EIS_REVIEW_SUMMARY_UNIQUE_COUNT_MISMATCH")
    rows = list(unique.values())
    rows.sort(key=lambda x: (
        x["public_sector_hint"] == "POSSIBLE_PUBLIC_INSTITUTION_REVIEW",
        not bool(x["published_on"]), x["published_on"] or "0000",
        x["notice_id"]
    ), reverse=False)
    public_hints = sum(r["public_sector_hint"] == "POSSIBLE_PUBLIC_INSTITUTION_REVIEW"
                       for r in rows)
    return {
        "mode": "EIS_PREQUALIFICATION_EVIDENCE_REVIEW_ONLY",
        "source": SOURCE_NAME, "checked_on": day.isoformat(),
        "counts": {"unique_notices_to_review": len(rows),
                   "suspected_public_institutions": public_hints,
                   "buyer_category_unverified": len(rows) - public_hints,
                   "exact_inn_fns_verified": 0,
                   "qualified_b2b_leads": 0, "crm_ready": 0},
        "profiles": profile_rows,
        "items": rows,
        "note": "Tender buyer must not be presumed micro/small or a new CRM lead",
        "crm_writes": "NO", "outbound_messages": "NO",
    }


def _csv_safe(value: object) -> str:
    value = str(value if value is not None else "")
    return ("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value)


def save_queue(review: dict, json_name: str, csv_name: str) -> tuple[Path, Path]:
    root = Path("exports").resolve()
    targets = (Path(json_name).resolve(), Path(csv_name).resolve())
    if (targets[0] == targets[1] or
            targets[0].suffix.lower() != ".json" or targets[1].suffix.lower() != ".csv" or
            any(not p.is_relative_to(root) or p.exists() for p in targets)):
        raise ValueError("EIS_REVIEW_OUTPUT_MUST_BE_NEW_EXPORT_FILES")
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
    with targets[0].open("x", encoding="utf-8") as handle:
        json.dump(review, handle, ensure_ascii=False, indent=2)
    with targets[1].open("x", encoding="utf-8-sig", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for entry in review["items"]:
            row = dict(entry)
            row["seen_in_profiles"] = ",".join(row["seen_in_profiles"])
            w.writerow({key: _csv_safe(row[key]) for key in COLUMNS})
    return targets


def main() -> None:
    cli = argparse.ArgumentParser(description="EIS notice evidence review, no qualified leads")
    cli.add_argument("--summary", required=True)
    cli.add_argument("--json", default="exports/eis_108_review.json")
    cli.add_argument("--csv", default="exports/eis_108_review.csv")
    args = cli.parse_args()
    try:
        report = build_queue(args.summary)
        filenames = save_queue(report, args.json, args.csv)
    except (OSError, ValueError, UnicodeError) as error:
        print("EIS_REVIEW=BLOCKED; REASON=" + str(error).splitlines()[0][:100])
        raise SystemExit(2)
    print("EIS_REVIEW_COUNTS=" + json.dumps(report["counts"], ensure_ascii=True))
    print("EIS_REVIEW_PROFILE_COUNTS=" + json.dumps(report["profiles"], ensure_ascii=True))
    print("EIS_REVIEW_JSON=" + str(filenames[0]))
    print("EIS_REVIEW_CSV=" + str(filenames[1]))
    print("EIS_REVIEW_STATUS=HUMAN_INN_FNS_NOTICE_CHECK_PENDING; QUALIFIED_LEADS=0; CRM_WRITES=NO")


if __name__ == "__main__":
    main()
