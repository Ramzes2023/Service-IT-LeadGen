"""Offline qualification gates for the 1C Moscow/MO pilot specified by Service IT.

A published vacancy is a *signal*, not a verified intention to buy.
This module never contacts CRM, AI, prospects, or VALYQON.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from .core import SCORE_VERSION, get_leads, score_company

PILOT_REGIONS = {"москва", "московская область"}
PILOT_SIGNAL = "1c_vacancy"
SOURCE_HOSTS = {"trudvsem.ru", "example.invalid"}  # second is synthetic fixture only


def trustworthy_source(signal: dict) -> bool:
    try:
        url = urlsplit(signal.get("source_url", ""))
        host = (url.hostname or "").lower()
        return (url.scheme == "https" and not url.username and not url.password
                and any(host == h or host.endswith("." + h) for h in SOURCE_HOSTS)
                and bool(url.path.strip("/")) and bool(signal.get("evidence", "").strip()))
    except ValueError:
        return False


def collect_pilot_candidates(db_path: str | Path, *, today: date | None = None,
                             limit: int = 50) -> dict:
    """Produce read-only research candidates; CRM-ready count stays zero until live dedup."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    now = today or date.today()
    counters = Counter()
    items = []
    for lead in get_leads(db_path):
        counters["companies_checked"] += 1
        if lead["region"].strip().lower() not in PILOT_REGIONS:
            counters["other_region"] += 1
            continue
        if lead["existing_customer"] or lead["manual_review"] or lead["suppressed_channels"]:
            counters["stopped_existing_or_suppressed"] += 1
            continue
        score, service, calculation = score_company(lead, lead["signals"], now)
        if (lead["score"] != score or lead["score_version"] != SCORE_VERSION or
                lead["explanation"].get("signal_id") != calculation.get("signal_id")):
            counters["outdated_score"] += 1
            continue
        evidence = next((s for s in lead["signals"] if s["id"] == calculation["signal_id"]), None)
        if not evidence or evidence["signal_type"] != PILOT_SIGNAL:
            counters["outside_1c_pilot"] += 1
            continue
        if not trustworthy_source(evidence):
            counters["unverified_source"] += 1
            continue
        if lead["priority"] not in ("high", "medium"):
            counters["low_priority"] += 1
            continue
        requirements = ["crm_duplicate_check_pending", "small_business_size_unverified"]
        if lead["phone"] or lead["email"]:
            requirements.append("contact_provenance_and_permission_unverified")
        else:
            requirements.append("corporate_contact_enrichment_pending")
        ai = lead.get("ai_suggestion") or {}
        if not isinstance(ai, dict) or ai.get("quality_status") != "complete":
            requirements.append("human_ai_review_required")
        items.append({
            "status": "RESEARCH_ONLY_NOT_CRM_READY", "local_company_id": lead["id"],
            "company_name": lead["name"], "inn": lead["inn"], "region": lead["region"],
            "size": "UNVERIFIED", "service": service,
            "lead_score": score, "score_version": SCORE_VERSION,
            "score_factors": {k: v for k, v in calculation.items() if k != "signal_id"},
            "signal": {"type": evidence["signal_type"], "url": evidence["source_url"],
                       "date": evidence["signal_date"], "source": evidence["source_name"],
                       "evidence": evidence["evidence"]},
            "ai_status": "REVIEW_HYPOTHESIS" if ai.get("quality_status") == "complete"
                         else "MISSING_OR_INCOMPLETE",
            "missing_checks": requirements,
            "crm_lifecycle": {"before_confirmed_interest": "CRM_LEAD",
                              "after_confirmed_interest": "DEAL_IN_SANDBOX_CATEGORY"},
            "manager_action": "Проверить CRM-дубли, размер компании, законный контакт и потребность.",
            "outbound_authorized": False
        })
        counters["candidates"] += 1
        if len(items) >= limit:
            break
    return {"mode": "OFFLINE_MVP_RESEARCH", "pilot": "1C / Moscow and Moscow Oblast",
            "crm_writes": "NO", "messages_sent": "NO", "valyqon_changes": "NONE",
            "crm_ready": 0, "today": now.isoformat(),
            "counts": dict(counters), "candidates": items}


def export_review(db_path: str | Path, destination: str | Path, *, limit: int = 50,
                  today: date | None = None) -> dict:
    target = Path(destination).resolve()
    if not target.is_relative_to(Path("exports").resolve()) or target.suffix != ".json":
        raise ValueError("Review export must be JSON under exports/")
    report = collect_pilot_candidates(db_path, today=today, limit=limit)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"candidates": len(report["candidates"]), "crm_ready": 0,
            "crm_writes": "NO", "file": str(target)}


def main():
    parser = argparse.ArgumentParser(description="1C MVP offline research qualification")
    parser.add_argument("--db", default="data/research.sqlite3")
    parser.add_argument("--output", default="exports/mvp_research_review.json")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print("MVP_REVIEW=" + json.dumps(export_review(args.db, args.output, limit=args.limit),
                                      ensure_ascii=False))


if __name__ == "__main__":
    main()
