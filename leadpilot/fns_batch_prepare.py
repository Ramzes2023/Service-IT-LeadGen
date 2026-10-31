"""Prepare a local list of legal-entity INNs for the official FNS batch UI.

Manual browser workflow only. No undocumented portal API, no CRM or outreach.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path

FNS_BATCH_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"
ALLOWED_MODES = frozenset({"PILOT_SCOPE_REVIEW", "HUMAN_EVIDENCE_CHECKLIST"})


def _safe_path(target: str | Path) -> Path:
    output = Path(target).resolve()
    if (not output.is_relative_to(Path("exports").resolve())
            or output.suffix.lower() != ".txt"):
        raise ValueError("Output must be a .txt file inside exports/")
    return output


def collect_inns(review_path: str | Path, *, mode: str = "legal_entity") -> list[str]:
    """Return distinct, validated INNs without company names or contact details."""
    if mode != "legal_entity":
        raise ValueError("Pilot accepts legal entities only (10 digit INNs)")
    report = json.loads(Path(review_path).read_text(encoding="utf-8-sig"))
    if not isinstance(report, dict) or report.get("mode") not in ALLOWED_MODES:
        raise ValueError("Expected local pilot or enrichment review")
    rows = report.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Expected 1-100 shortlisted organizations")
    inns = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed review row")
        value = row.get("inn")
        if (not isinstance(value, str) or len(value) != 10
                or not value.isascii() or not value.isdigit()):
            raise ValueError("Only exact 10-digit legal entity INNs permitted")
        if value in inns:
            raise ValueError("Duplicate INN in source review")
        inns.add(value)
    return sorted(inns)


def prepare(review_path: str | Path, output: str | Path, *,
            separator: str = "newline") -> dict:
    if separator not in ("newline", "comma"):
        raise ValueError("separator must be newline or comma")
    inns = collect_inns(review_path)
    dest = _safe_path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    delimiter = "\n" if separator == "newline" else ","
    # Exclusive create: existing user files are never overwritten.
    with dest.open("x", encoding="utf-8", newline="\n") as out:
        out.write(delimiter.join(inns) + "\n")
    return {
        "mode": "FNS_BATCH_MANUAL_REVIEW",
        "organization_count": len(inns),
        "output": str(dest),
        "official_browser_url": FNS_BATCH_URL,
        "last_fns_result": "NOT_YET_CHECKED",
        "fns_categories_verified": 0,
        "crm_ready": 0,
        "crm_writes": "NO",
        "outbound_messages": "NO",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare INN list for FNS official batch search")
    p.add_argument("--review", default="exports/mvp_scope_review.json")
    p.add_argument("--output", default="exports/fns_batch_inns.txt")
    p.add_argument("--separator", choices=("newline", "comma"), default="newline")
    args = p.parse_args()
    result = prepare(args.review, args.output, separator=args.separator)
    print("FNS_BATCH_PREPARED=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
