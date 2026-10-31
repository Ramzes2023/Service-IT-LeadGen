"""Offline exact-INN evidence audit for FNS-verified micro/small businesses.

Use only local FNS size-only JSON and saved official Trudvsem vacancy pages.
Vacancies imply an internal hiring signal, never intent to buy outsourced 1C.
No network, contact collection, lead scoring, messaging or CRM writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from .verified_import import REGIONS, read_snapshots, is_valid_inn

ALLOWED_REGIONS = {"Москва", "Московская область"}
PAGES = {0, 1, 2, 3, 4}
API_SOURCE = "trudvsem-open-vacancies"
FNS_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def _read_fns_candidates(filename: str | Path, today: date) -> tuple[dict[str, dict], str]:
    doc = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    if (not isinstance(doc, dict) or doc.get("mode") != "SME_FIRST_FNS_SIZE_ONLY"
            or doc.get("crm_writes") != "NO" or doc.get("outbound_messages") != "NO"):
        raise ValueError("Expected read-only SME_FIRST_FNS_SIZE_ONLY report")
    counts = doc.get("counts")
    rows = doc.get("companies")
    if (not isinstance(counts, dict) or counts.get("crm_ready") != 0
            or not isinstance(rows, list) or not 1 <= len(rows) <= 100
            or counts.get("size_eligible_for_research") != len(rows)):
        raise ValueError("FNS report count mismatch or prior authorization")
    try:
        snapshot = date.fromisoformat(doc["snapshot"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid FNS snapshot date") from None
    if not 0 <= (today - snapshot).days <= 60:
        raise ValueError("FNS snapshot stale or future dated")
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid FNS size-only company")
        inn = row.get("inn")
        if (not is_valid_inn(inn) or inn in selected
                or row.get("fns_category") not in {"MICRO", "SMALL"}
                or row.get("region") not in ALLOWED_REGIONS
                or row.get("fns_source") != FNS_URL
                or row.get("fns_snapshot") != snapshot.isoformat()
                or row.get("business_size_verified_from_export") is not True
                or row.get("qualification") != "SIZE_ONLY_RESEARCH"
                or row.get("demand_for_1c") != "NOT_CHECKED"
                or row.get("lead_score") is not None
                or row.get("crm_ready") is not False
                or row.get("outbound_authorized") is not False):
            raise ValueError("Unverified, duplicate or pre-approved FNS company")
        if not isinstance(row.get("company_name"), str) or not row["company_name"].strip():
            raise ValueError("Missing FNS legal name")
        selected[inn] = row
    return selected, snapshot.isoformat()


def _valid_url(raw: str) -> bool:
    if not isinstance(raw, str):
        return False
    try:
        url = urlsplit(raw)
        host = (url.hostname or "").lower()
        return (url.scheme == "https" and not url.username and not url.password
                and url.port in (None, 443) and bool(url.path.strip("/"))
                and (host == "trudvsem.ru" or host.endswith(".trudvsem.ru")))
    except (ValueError, TypeError):
        return False


def _read_vacancy_pages(folders: list[str | Path], offsets: list[int],
                        today: date) -> tuple[list[dict], dict]:
    if (not isinstance(folders, list) or not isinstance(offsets, list)
            or not 1 <= len(folders) <= 5 or len(folders) != len(offsets)
            or len(set(offsets)) != len(offsets)
            or any(type(offset) is not int or offset not in PAGES for offset in offsets)):
        raise ValueError("Provide 1..5 folders with unique page indices 0..4")
    all_items = []
    stats = Counter()
    for offset, filename in sorted(zip(offsets, folders)):
        folder = Path(filename)
        for region_code in ("77", "50"):
            path = folder / f"trudvsem_{region_code}.json"
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError(f"Missing or oversized source snapshot for region {region_code} page {offset}")
            record = json.loads(path.read_text(encoding="utf-8-sig"))
            if (not isinstance(record, dict)
                    or record.get("source") != API_SOURCE
                    or record.get("region") != REGIONS[region_code]
                    or record.get("term") != "1С"
                    or type(record.get("offset")) is not int or record["offset"] != offset
                    or type(record.get("fetched")) is not int
                    or type(record.get("valid_signals")) is not int
                    or not isinstance(record.get("signals"), list)
                    or not (0 <= record["valid_signals"] == len(record["signals"])
                            <= record["fetched"] <= 50)):
                raise ValueError("Incorrect or incomplete official vacancy page metadata")
            stats["raw_fetched"] += record["fetched"]
            stats["normalized_signals"] += record["valid_signals"]
        accepted, region_stats = read_snapshots(folder, today=today)
        if any(count and key.startswith(("missing_file_", "bad_schema_", "corrupt_file_"))
               for key, count in region_stats.items()):
            raise ValueError("Malformed or missing source snapshot; no demand claims")
        stats["validated_signals"] += len(accepted)
        stats["discarded_by_snapshot_validator"] += (
            region_stats.get("raw_records", 0) - len(accepted))
        all_items.extend(accepted)
    stats["pages_checked"] = len(folders)
    return all_items, dict(stats)


def audit(fns_file: str | Path, folders: list[str | Path], offsets: list[int], *,
          today: date | None = None) -> dict:
    now = today or date.today()
    companies, snapshot = _read_fns_candidates(fns_file, now)
    signals, src_audit = _read_vacancy_pages(folders, offsets, now)
    matches: dict[str, dict[str, dict]] = {}
    counters = Counter()
    for signal in signals:
        if signal["signal_type"] != "1c_vacancy":
            counters["not_a_1c_vacancy"] += 1
            continue
        if not _valid_url(signal["source_url"]):
            counters["untrusted_or_http_url"] += 1
            continue
        if not 0 <= (now - date.fromisoformat(signal["signal_date"])).days <= 45:
            counters["old_vacancy"] += 1
            continue
        company = companies.get(signal["inn"])
        if company is None:
            counters["inn_not_in_verified_sme_50"] += 1
            continue
        if signal["region"] != company["region"]:
            counters["region_mismatch"] += 1
            continue
        matches.setdefault(signal["inn"], {})[signal["source_url"]] = {
            "published_on": signal["signal_date"], "title": signal["evidence"],
            "vacancy_url": signal["source_url"],
            "employer_name_as_published": signal["name"],
            "interpretation": "Hiring signal only; outsourcing demand not established",
        }
    result = []
    for inn in sorted(companies):
        comp = companies[inn]
        evidence = list(matches.get(inn, {}).values())
        evidence.sort(key=lambda row: (row["published_on"], row["vacancy_url"]), reverse=True)
        if evidence:
            status = "PUBLIC_1C_VACANCY_REVIEW"
            next_action = "Review live vacancy and whether external 1C support is actually sought"
        else:
            status = "NO_MATCH_IN_SAMPLED_VACANCIES"
            next_action = "Search other demand sources; this does not imply no demand"
        result.append({
            "inn": inn, "company_name": comp["company_name"],
            "region": comp["region"], "fns_category": comp["fns_category"],
            "fns_snapshot": snapshot, "okved_public": comp["okved_public"],
            "review_status": status, "next_action": next_action,
            "vacancy_signals": evidence, "vacancy_signal_count": len(evidence),
            "outsourced_1c_demand_confirmed": False,
            "company_identity_needs_manual_check": bool(evidence),
            "crm_duplicate_check": "NOT_PERFORMED",
            "legal_review": "NOT_PERFORMED",
            "contact_channel": "NOT_COLLECTED",
            "lead_score": None,
            "crm_ready": False, "outbound_authorized": False,
        })
    found = sum(r["vacancy_signal_count"] > 0 for r in result)
    return {
        "mode": "SME_FIRST_OFFLINE_VACANCY_AUDIT",
        "date": now.isoformat(), "fns_snapshot": snapshot,
        "source_description": "Only local bounded Trudvsem pages, not the full labour market",
        "source_audit": src_audit,
        "counts": {**dict(counters), "fns_size_verified_companies": len(companies),
                   "companies_with_1c_vacancy_signal": found,
                   "companies_without_match_in_sample": len(companies) - found,
                   "real_outsourcing_demand_confirmed": 0, "crm_ready": 0},
        "companies": result,
        "crm_writes": "NO", "outbound_messages": "NO",
        "warnings": [
            "Vacancy is a hiring signal, not proof of a contract opportunity for 1C outsourcing.",
            "Absence of matching vacancy from four sample pages is NOT proof of no demand.",
            "Do not email/call, approve sales leads, or write Bitrix24 based on this report.",
        ],
    }


def _excel_safe(value: object) -> str:
    s = str(value or "").replace("\x00", "")
    if s.lstrip().startswith(("=", "+", "-", "@")) or s.startswith(("\t", "\r", "\n")):
        return "'" + s
    return s


def save(report: dict, json_path: str | Path, csv_path: str | Path) -> dict:
    json_out, csv_out = Path(json_path).resolve(), Path(csv_path).resolve()
    root = Path("exports").resolve()
    if (not json_out.is_relative_to(root) or not csv_out.is_relative_to(root)
            or json_out.suffix.lower() != ".json" or csv_out.suffix.lower() != ".csv"
            or json_out == csv_out or json_out.exists() or csv_out.exists()):
        raise ValueError("Use new, separate JSON and CSV files under exports/")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    columns = ["inn", "company_name", "region", "fns_category",
               "review_status", "vacancy_signal_count",
               "recent_vacancy_url", "next_action", "crm_ready"]
    with json_out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    with csv_out.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for r in report["companies"]:
            writer.writerow({
                "inn": r["inn"], "company_name": _excel_safe(r["company_name"]),
                "region": r["region"], "fns_category": r["fns_category"],
                "review_status": r["review_status"],
                "vacancy_signal_count": r["vacancy_signal_count"],
                "recent_vacancy_url": (
                    r["vacancy_signals"][0]["vacancy_url"] if r["vacancy_signals"] else ""),
                "next_action": _excel_safe(r["next_action"]),
                "crm_ready": "NO",
            })
    return {"json": str(json_out), "csv": str(csv_out),
            "company_count": len(report["companies"]), "crm_writes": "NO"}


def main() -> None:
    p = argparse.ArgumentParser(description="Audit FNS micro/small firms against existing 1C vacancy pages")
    p.add_argument("--fns", default="exports/sme_first_moscow_50.json")
    p.add_argument("--folders", nargs="+", default=["exports/round2_offset0", "exports/round2_offset1"])
    p.add_argument("--offsets", nargs="+", type=int, default=[0, 1])
    p.add_argument("--output", default="exports/sme_moscow_50_demand_audit.json")
    p.add_argument("--csv", default="exports/sme_moscow_50_demand_audit.csv")
    a = p.parse_args()
    report = audit(a.fns, a.folders, a.offsets)
    files = save(report, a.output, a.csv)
    print("SME_VACANCY_AUDIT=" + json.dumps({
        "counts": report["counts"], "files": files,
        "crm_writes": "NO", "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
