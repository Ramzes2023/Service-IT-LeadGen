"""Human scope decisions for the first small-business 1C pilot.

Offline only: no automatic exclusion based on names, no CRM writes, no outreach.
This review never certifies FNS category or permits contact with any prospect.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

DECISIONS = {"PENDING", "PROVISIONAL_SMALL", "DEFER", "EXCLUDE"}
TYPES = {"unknown", "micro", "small", "medium", "large", "public_institution", "other"}
REASONS = {"", "SIZE_OUT_OF_SCOPE", "PUBLIC_INSTITUTION", "NOT_1C_BUYER",
           "UNVERIFIED_ORGANIZATION", "OTHER"}


def _safe_output(value: str | Path) -> Path:
    p = Path(value).resolve()
    if not p.is_relative_to(Path("exports").resolve()) or p.suffix.lower() != ".json":
        raise ValueError("Output must be .json under exports")
    return p


def _source_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and bool(u.hostname) and
                u.port in (None, 443) and not u.username and
                not u.password and not u.fragment)
    except ValueError:
        return False


def _recent(value: str, today: date) -> bool:
    try:
        age = (today - date.fromisoformat(value)).days
        return 0 <= age <= 60
    except (ValueError, TypeError):
        return False


def prepare(checklist: str | Path, output: str | Path) -> dict:
    obj = json.loads(Path(checklist).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != "HUMAN_EVIDENCE_CHECKLIST":
        raise ValueError("Expected existing human evidence checklist")
    rows = obj.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Expected 1 to 100 companies")
    seen, results = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed company row")
        inn = row.get("inn")
        if (not isinstance(inn, str) or not inn.isascii() or not inn.isdigit()
                or len(inn) not in (10, 12) or inn in seen):
            raise ValueError("INN invalid or duplicated")
        seen.add(inn)
        results.append({"inn": inn, "company_name": row.get("company_name") or "",
                        "decision": "PENDING", "reported_business_type": "unknown",
                        "reason": "", "evidence_url": "", "evidence_date": "",
                        "evidence_text": "", "reviewed_by": "", "notes": ""})
    p = _safe_output(output)
    if p.exists():
        raise FileExistsError("Existing manual decisions must not be overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mode": "PILOT_SCOPE_REVIEW", "version": 1, "crm_ready": 0,
                             "crm_writes": "NO", "items": results},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows": len(results), "file": str(p), "crm_ready": 0, "crm_writes": "NO"}


def audit(path: str | Path, *, today: date | None = None) -> dict:
    now = today or date.today()
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != "PILOT_SCOPE_REVIEW":
        raise ValueError("Expected pilot scope decisions")
    rows = obj.get("items")
    if not isinstance(rows, list):
        raise ValueError("Invalid review structure")
    counts = {"total": len(rows), "pending": 0, "exclusion_reviewed": 0,
              "provisional_small": 0, "deferred": 0, "invalid_review": 0,
              "crm_ready": 0, "crm_writes": "NO"}
    seen, details = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid review row")
        inn = row.get("inn")
        if (not isinstance(inn, str) or not inn.isascii() or not inn.isdigit() or
                len(inn) not in (10, 12) or inn in seen):
            raise ValueError("INN invalid or duplicated")
        seen.add(inn)
        decision = row.get("decision")
        typ = row.get("reported_business_type")
        reason = row.get("reason")
        if decision not in DECISIONS or typ not in TYPES or reason not in REASONS:
            raise ValueError("Unsupported decision, company type, or reason")
        evidence = (isinstance(row.get("evidence_text"), str) and
                    len(row["evidence_text"].strip()) >= 20)
        supports_decision = (decision == "EXCLUDE" and
                             reason in REASONS - {"", "UNVERIFIED_ORGANIZATION"} and
                             typ in {"medium", "large", "public_institution", "other"})
        supports_provisional = (decision == "PROVISIONAL_SMALL" and
                                typ in {"micro", "small"} and
                                reason == "" and
                                isinstance(row.get("evidence_text"), str) and
                                inn in row["evidence_text"])
        documented = (bool((row.get("reviewed_by") or "").strip()) and
                      _source_url(row.get("evidence_url")) and
                      _recent(row.get("evidence_date"), now) and evidence)
        if decision == "PENDING":
            status = "PENDING"
            counts["pending"] += 1
        elif not documented or not (supports_decision or supports_provisional or decision == "DEFER"):
            status = "INVALID_MANUAL_EVIDENCE"
            counts["invalid_review"] += 1
        elif decision == "EXCLUDE":
            status = "EXCLUSION_REVIEWED_NOT_CRM_ACTION"
            counts["exclusion_reviewed"] += 1
        elif decision == "PROVISIONAL_SMALL":
            status = "SMALL_CLAIM_REQUIRES_FNS_VERIFICATION"
            counts["provisional_small"] += 1
        else:
            status = "DEFERRED"
            counts["deferred"] += 1
        details.append({"inn": inn, "status": status})
    return {"mode": "PILOT_SCOPE_AUDIT", "counts": counts, "details": details,
            "note": "Evidence reviewed, not independently authenticated; no lead is CRM-ready.",
            "outreach": "NOT_AUTHORIZED"}


def main():
    parser = argparse.ArgumentParser(description="Manual scope decisions for 1C pilot")
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--checklist", default="exports/mvp_enrichment_checklist.json")
    p.add_argument("--output", default="exports/mvp_scope_review.json")
    a = sub.add_parser("audit")
    a.add_argument("--review", default="exports/mvp_scope_review.json")
    args = parser.parse_args()
    result = prepare(args.checklist, args.output) if args.mode == "prepare" else audit(args.review)
    print("PILOT_SCOPE=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
