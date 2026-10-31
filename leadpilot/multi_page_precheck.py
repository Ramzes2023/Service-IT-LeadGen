"""Join several locally saved Trudvsem pages before an FNS batch lookup.

Read-only, offline, no CRM or outreach. The official FNS reconciliation and all
snapshots must be present and consistent; missing pages never count as zero hits.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from .new_1c_fns_precheck import plan, save

REGIONS = {"77": "Москва", "50": "Московская область"}
OFFSETS = {0, 1, 2, 3, 4}


def combine_pages(folders: list[str | Path], offsets: list[int], prior_fns: str | Path,
                  *, today: date | None = None, max_new: int = 100) -> dict:
    """Merge only fresh 1C signals with distinct INNs and matching page metadata."""
    now = today or date.today()
    if (not isinstance(folders, list) or not isinstance(offsets, list)
            or not 1 <= len(folders) <= 5 or len(folders) != len(offsets)
            or any(type(x) is not int or x not in OFFSETS for x in offsets)
            or len(offsets) != len(set(offsets))):
        raise ValueError("Provide 1-5 distinct page indices: offsets 0,1,2,3,4")
    if type(max_new) is not int or not 1 <= max_new <= 100:
        raise ValueError("max_new must be an integer from 1 to 100")

    merged: dict[str, dict] = {}
    conflicting = set()
    counters = Counter()
    details = []

    for offset, folder_string in sorted(zip(offsets, folders)):
        folder = Path(folder_string)
        page_meta = {}
        for code, region in REGIONS.items():
            path = folder / f"trudvsem_{code}.json"
            if not path.is_file():
                raise ValueError(f"Missing saved source for region {code}, offset {offset}")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if (not isinstance(data, dict) or data.get("source") != "trudvsem-open-vacancies"
                    or data.get("region") != region or data.get("term") != "1С"
                    or type(data.get("offset")) is not int or data["offset"] != offset
                    or not isinstance(data.get("signals"), list)
                    or type(data.get("fetched")) is not int
                    or type(data.get("valid_signals")) is not int
                    or not 0 <= data["valid_signals"] == len(data["signals"]) <= data["fetched"] <= 50):
                raise ValueError(f"Inconsistent page metadata for region {code}, offset {offset}")
            page_meta[code] = {"fetched": data["fetched"],
                               "parsed_signals": data["valid_signals"]}
            counters["fetched"] += data["fetched"]
            counters["parsed_signals"] += data["valid_signals"]

        # Reuse already-tested strict source/INN, date and FNS checks.
        report = plan(folder, prior_fns, today=now, max_new=100)
        for key in ("fresh_validated_signals", "stale_or_untrusted_source",
                    "already_checked_fns", "not_1c"):
            counters[key] += report["counts"].get(key, 0)
        for item in report["candidates"]:
            inn = item["inn"]
            if inn in conflicting:
                continue
            old = merged.get(inn)
            if old is None:
                merged[inn] = {**item, "source_offsets": [offset],
                               "occurrences": 1}
            elif old["employer"].casefold().strip() != item["employer"].casefold().strip():
                # Same verified INN but differing business identity across pages.
                # Hold rather than silently choosing one of the names.
                conflicting.add(inn)
                merged.pop(inn, None)
            else:
                counters["duplicate_employer_between_pages"] += 1
                old["occurrences"] += 1
                old["source_offsets"].append(offset)
                if item["signal_date"] > old["signal_date"]:
                    old.update({"signal_date": item["signal_date"],
                                "signal_url": item["signal_url"],
                                "signal": item["signal"]})
        details.append({"offset": offset, "regions": page_meta,
                        "new_inns_on_page": report["counts"]["new_unique_inns"]})

    selected = sorted(merged.values(),
                      key=lambda r: (r["signal_date"], r["inn"]), reverse=True)
    truncated = len(selected) > max_new
    selected = selected[:max_new]
    counters["held_identity_conflicts"] = len(conflicting)
    counters["unique_new_inns"] = len(merged)
    counters["selected_for_fns"] = len(selected)
    return {
        "mode": "NEW_1C_FNS_MULTIPAGE_PRECHECK_ONLY",
        "date": now.isoformat(), "fns_source": "https://rmsp.nalog.ru/search.html?mode=inn-list",
        "pages": details,
        "counts": {**dict(counters), "pages_checked": len(folders),
                   "truncated": truncated, "ready_for_crm": 0},
        "candidates": selected,
        "identity_conflicts_hold": sorted(conflicting),
        "crm_writes": "NO", "outbound_messages": "NO",
        "warning": "FNS SME status and intention to buy 1C support NOT verified for new INNs."
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Combine bounded 1C pages for manual FNS INN check")
    p.add_argument("--folders", nargs="+", required=True)
    p.add_argument("--offsets", type=int, nargs="+", required=True)
    p.add_argument("--prior-fns", default="exports/fns_official_reconciliation.json")
    p.add_argument("--output", default="exports/round2_combined_precheck.json")
    p.add_argument("--inn-list", default="exports/round2_combined_inns.txt")
    p.add_argument("--limit", type=int, default=100)
    args = p.parse_args()
    report = combine_pages(args.folders, args.offsets, args.prior_fns, max_new=args.limit)
    stored = save(report, args.output, args.inn_list)
    print("MULTIPAGE_FNS_PRECHECK=" + json.dumps({
        "counts": report["counts"], "files": stored,
        "identity_conflicts_hold": report["identity_conflicts_hold"],
        "crm_writes": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
