"""Offline review of documented PUBLIC requests for external 1C services.

Manual, independently reviewable source citations only. Input is NOT a live
crawler or trusted proof of a current contract. Never writes to CRM or contacts
any company. A published tender is a stronger *hypothesis* than a vacancy,
but still needs real-time verification and manager authorization.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .sme_vacancy_audit import _read_fns_candidates

TRUSTED_HOSTS = {
    "zakupki.gov.ru", "zakupki.mos.ru", "b2b-center.ru", "bidzaar.com",
    "roseltorg.ru", "rts-tender.ru", "tek-torg.ru", "fabrikant.ru",
    "zakupki.kontur.ru", "kontendus.ru",
}
TYPES = {"COMMERCIAL_TENDER", "PROCUREMENT_NOTICE", "PUBLIC_REQUEST"}
SERVICE_WORDS = ("сопровожд", "поддержк", "доработ", "настройк", "внедрен",
                 "обслуживан", "консультационн", "модификац")
MAX_EVIDENCE_BYTES = 100 * 1024


def _url_allowed(raw: object) -> bool:
    if not isinstance(raw, str) or len(raw) > 1800:
        return False
    try:
        url = urlsplit(raw)
        host = (url.hostname or "").lower()
        return (url.scheme == "https" and not url.username and not url.password
                and url.port in (None, 443) and bool(url.path.strip("/"))
                and any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS))
    except (ValueError, TypeError):
        return False


def _parse_date(raw: object, field: str) -> date:
    if not isinstance(raw, str):
        raise ValueError(f"Invalid {field}: expected YYYY-MM-DD")
    try:
        out = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"Invalid {field}: expected YYYY-MM-DD") from None
    if out.isoformat() != raw:
        raise ValueError(f"Noncanonical {field}")
    return out


def _title_is_1c_service(title: object) -> bool:
    if not isinstance(title, str) or not 20 <= len(title) <= 500:
        return False
    t = title.casefold().replace("ё", "е")
    # Do not mix 1C accounting / ERP service with unrelated 1C-Bitrix products.
    if "битрикс" in t:
        return False
    return bool(re.search(r"1[сc](?![а-яa-z0-9])", t) and
                any(word in t for word in SERVICE_WORDS))


def _load_evidence(path: str | Path, now: date) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        raise ValueError("PUBLIC_REQUEST_EVIDENCE_MISSING")
    if p.stat().st_size <= 0 or p.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("PUBLIC_REQUEST_EVIDENCE_SIZE_INVALID")
    obj = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != "PUBLIC_1C_REQUESTS_MANUAL":
        raise ValueError("Unexpected public evidence schema")
    entries = obj.get("items")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("Require 1-100 real sourced items; do not create fake evidence")
    seen = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid public request row")
        inn = entry.get("buyer_inn")
        # Pure legal-entity INNs only for this first pilot, no IP personal data.
        if not isinstance(inn, str) or not re.fullmatch(r"\d{10}", inn):
            raise ValueError("Expected exactly 10-digit legal-entity buyer INN")
        from .verified_import import is_valid_inn
        if not is_valid_inn(inn):
            raise ValueError("Public buyer INN checksum failed")
        url = entry.get("source_url")
        if not _url_allowed(url):
            raise ValueError("Evidence URL must be an HTTPS public procurement source")
        title = entry.get("title")
        if not _title_is_1c_service(title):
            raise ValueError("Request must explicitly describe external 1C services")
        if entry.get("source_type") not in TYPES:
            raise ValueError("Invalid public request source type")
        posted = _parse_date(entry.get("published_on"), "published_on")
        if not 0 <= (now - posted).days <= 90:
            raise ValueError("Public request publication is stale or future dated")
        deadline_raw = entry.get("deadline_on")
        deadline = _parse_date(deadline_raw, "deadline_on") if deadline_raw is not None else None
        if deadline is not None and deadline < posted:
            raise ValueError("Deadline earlier than publication")
        note = entry.get("evidence_excerpt")
        if not isinstance(note, str) or not 20 <= len(note.strip()) <= 700:
            raise ValueError("Add a short factual excerpt from the public request")
        if entry.get("verified_by_operator") is not False:
            raise ValueError("Never assert independent operator verification from an import")
        key = (inn, url)
        if key in seen:
            raise ValueError("Duplicate public source for the same buyer")
        seen.add(key)
        result.append({
            "buyer_inn": inn, "source_url": url,
            "source_type": entry["source_type"], "title": title.strip(),
            "published_on": posted.isoformat(),
            "deadline_on": deadline.isoformat() if deadline else None,
            "evidence_excerpt": note.strip(),
        })
    return result


def review(fns_json: str | Path, evidence_json: str | Path,
           *, today: date | None = None) -> dict:
    now = today or date.today()
    companies, snapshot = _read_fns_candidates(fns_json, now)
    requests = _load_evidence(evidence_json, now)
    counts = Counter()
    attached: dict[str, list[dict]] = {}
    unmatched_inns = set()
    for record in requests:
        inn = record["buyer_inn"]
        if inn not in companies:
            unmatched_inns.add(inn)
            counts["requests_not_matched_to_fns_50"] += 1
            continue
        due = record["deadline_on"]
        if due is None:
            signal = "DEADLINE_UNKNOWN_MANUAL_RECHECK"
        elif due < now.isoformat():
            signal = "DEADLINE_PASSED_NOT_ACTIVE"
        else:
            signal = "PUBLISHED_EXTERNAL_1C_REQUEST_REVIEW"
        counts[signal] += 1
        attached.setdefault(inn, []).append({
            **record, "research_status": signal,
            "source_verified_live": False, "outsourcing_scope_reviewed": False,
            "contract_acceptance_authorized": False,
        })
    companies_out = []
    for inn in sorted(companies):
        company = companies[inn]
        evidence = sorted(attached.get(inn, []),
                          key=lambda d: (d["published_on"], d["source_url"]), reverse=True)
        companies_out.append({
            "inn": inn, "company_name": company["company_name"],
            "region": company["region"], "fns_category": company["fns_category"],
            "fns_snapshot": snapshot, "okved_public": company["okved_public"],
            "source_requests": evidence,
            "source_request_count": len(evidence),
            "lead_status": ("PUBLIC_SOURCE_MANUAL_REVIEW" if evidence
                            else "NO_DOCUMENTED_EXTERNAL_REQUEST_IN_SUBMITTED_ITEMS"),
            "needs_manual_live_page_check": bool(evidence),
            "needs_contract_scope_check": bool(evidence),
            "needs_legal_and_duplicate_check": True,
            "crm_ready": False, "outbound_authorized": False,
            "lead_score": None,
        })
    return {
        "mode": "SME_FIRST_PUBLIC_1C_REQUEST_REVIEW",
        "checked_on": now.isoformat(), "fns_snapshot": snapshot,
        "counts": {"size_verified_businesses": len(companies),
                   "source_requests_supplied": len(requests),
                   "source_requests_matching_fns": sum(len(v) for v in attached.values()),
                   "companies_with_public_requests": len(attached),
                   "requests_not_matched_to_fns_50": counts["requests_not_matched_to_fns_50"],
                   "out_of_scope_buyer_inns": len(unmatched_inns),
                   "deadline_passed": counts["DEADLINE_PASSED_NOT_ACTIVE"],
                   "deadline_unknown": counts["DEADLINE_UNKNOWN_MANUAL_RECHECK"],
                   "deadline_not_yet_passed_unverified": counts["PUBLISHED_EXTERNAL_1C_REQUEST_REVIEW"],
                   "crm_ready": 0},
        "companies": companies_out,
        "unmatched_buyer_inns_to_check_fns_separately": sorted(unmatched_inns),
        "crm_writes": "NO", "outbound_messages": "NO",
        "warnings": [
            "Source records were manually supplied and were NOT fetched or independently verified.",
            "A future deadline does not prove a tender remains open or is suitable for Service IT.",
            "No matching source in a small input list does not mean no commercial demand.",
            "No contact harvesting, automatic approval, Bitrix24 writes or outreach.",
        ],
    }


def _csv_safe(v: object) -> str:
    value = str(v or "").replace("\x00", "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def save(report: dict, json_path: str | Path, csv_path: str | Path) -> dict:
    output = Path(json_path).resolve()
    csv_output = Path(csv_path).resolve()
    root = Path("exports").resolve()
    if (not output.is_relative_to(root) or not csv_output.is_relative_to(root)
            or output.suffix.lower() != ".json" or csv_output.suffix.lower() != ".csv"
            or output == csv_output or output.exists() or csv_output.exists()):
        raise ValueError("Save as two new JSON/CSV files inside exports")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    with csv_output.open("x", encoding="utf-8-sig", newline="") as out:
        columns = ["inn", "company_name", "fns_category", "lead_status",
                   "source_request_count", "public_request_url", "crm_ready"]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for company in report["companies"]:
            requests = company["source_requests"]
            writer.writerow({
                "inn": company["inn"], "company_name": _csv_safe(company["company_name"]),
                "fns_category": company["fns_category"],
                "lead_status": company["lead_status"],
                "source_request_count": len(requests),
                "public_request_url": requests[0]["source_url"] if requests else "",
                "crm_ready": "NO",
            })
    return {"json": str(output), "csv": str(csv_output),
            "companies": len(report["companies"]), "crm_writes": "NO"}


def main() -> None:
    p = argparse.ArgumentParser(description="Strict offline manual 1C procurement request review")
    p.add_argument("--fns", default="exports/sme_first_moscow_50.json")
    p.add_argument("--evidence", required=True,
                   help="Local JSON of real public tender references, not a fake/sample dataset")
    p.add_argument("--output", default="exports/sme_public_1c_request_audit.json")
    p.add_argument("--csv", default="exports/sme_public_1c_request_audit.csv")
    args = p.parse_args()
    report = review(args.fns, args.evidence)
    files = save(report, args.output, args.csv)
    print("SME_PUBLIC_REQUEST_AUDIT=" + json.dumps({
        "counts": report["counts"], "files": files,
        "crm_writes": "NO", "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
