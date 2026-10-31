"""Offline manager readiness report joining independent 1C pilot review files.

This module NEVER marks any prospect as CRM-ready, alters human reviews,
queries a website, or sends CRM/contact requests.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .enrichment_review import audit_checklist
from .pilot_scope_review import audit as audit_scope


def _load(path: str | Path, expected_mode: str) -> dict:
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if (not isinstance(obj, dict) or obj.get("mode") != expected_mode
            or not isinstance(obj.get("items" if expected_mode != "OFFLINE_MVP_RESEARCH"
                                     else "candidates"), list)):
        raise ValueError("Wrong or missing offline review file")
    if expected_mode == "OFFLINE_MVP_RESEARCH" and obj.get("crm_ready") != 0:
        raise ValueError("Research input must be explicitly non-CRM-ready")
    return obj


def _index(rows: list[dict], expected_status: str | None = None) -> dict[str, dict]:
    result = {}
    if len(rows) > 100:
        raise ValueError("Too many companies for this pilot")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid row")
        inn = row.get("inn")
        if (not isinstance(inn, str) or len(inn) not in (10, 12) or
                not inn.isascii() or not inn.isdigit() or inn in result):
            raise ValueError("Invalid or duplicated company INN")
        if expected_status and row.get("status") != expected_status:
            raise ValueError("Unexpected research candidate status")
        result[inn] = row
    return result


def assemble(research: str | Path, enrichment: str | Path,
             scope: str | Path) -> dict:
    """All three inputs must cover exactly the same INNs or report fails closed."""
    candidates = _index(
        _load(research, "OFFLINE_MVP_RESEARCH")["candidates"],
        "RESEARCH_ONLY_NOT_CRM_READY")
    checklist = _index(_load(enrichment, "HUMAN_EVIDENCE_CHECKLIST")["items"])
    reviewed = _index(_load(scope, "PILOT_SCOPE_REVIEW")["items"])
    if not candidates or candidates.keys() != checklist.keys() or candidates.keys() != reviewed.keys():
        raise ValueError("Review files cover different companies or have no candidates")

    enrichment_results = _index(audit_checklist(enrichment)["details"])
    scope_results = _index(audit_scope(scope)["details"])
    if enrichment_results.keys() != candidates.keys() or scope_results.keys() != candidates.keys():
        raise ValueError("Audit output differs from research companies")

    rows, reasons = [], Counter()
    for inn, cand in candidates.items():
        original = checklist[inn]
        scope_status = scope_results[inn]["status"]
        evidence_status = enrichment_results[inn]["status"]
        missing = []
        if scope_status == "PENDING":
            missing.append("Проверить соответствие сегменту малого бизнеса и 1С")
        elif scope_status == "INVALID_MANUAL_EVIDENCE":
            missing.append("Исправить решение по сегменту и приложить источник")
        elif scope_status == "SMALL_CLAIM_REQUIRES_FNS_VERIFICATION":
            missing.append("Подтвердить категорию МСП в официальном реестре ФНС по ИНН")
        elif scope_status in ("EXCLUSION_REVIEWED_NOT_CRM_ACTION", "DEFERRED"):
            missing.append("Не передавать менеджеру в текущем пилоте")
        else:
            missing.append("Уточнить решение о целевом сегменте")

        if evidence_status != "MANUAL_EVIDENCE_PRESENT_NOT_CRM_READY":
            missing.append("Сверить МСП и официальный корпоративный канал с датами и URL")
        if original.get("crm_duplicate_check") != "NOT_PERFORMED":
            # No CRM check is accepted from an unauthenticated offline JSON file.
            missing.append("Состояние CRM-дубликатов не подтверждено независимой проверкой")
        else:
            missing.append("Проверить CRM-дубликаты после выдачи ограниченных прав")
        missing.append("Согласовать право и способ первого контакта; наличие телефона не согласие")
        if cand.get("ai_status") != "REVIEW_HYPOTHESIS":
            missing.append("Проверить AI-гипотезу и предлагаемую услугу")
        if not isinstance(cand.get("lead_score"), int):
            raise ValueError("Score is missing or malformed")

        for reason in missing:
            reasons[reason] += 1
        rows.append({
            "inn": inn, "company": str(cand.get("company_name") or ""),
            "region": str(cand.get("region") or ""),
            "score": cand["lead_score"], "score_version": str(cand.get("score_version") or ""),
            "vacancy_source": str((cand.get("signal") or {}).get("url") or ""),
            "scope": scope_status, "evidence": evidence_status,
            "status": "HOLD_FOR_REVIEW", "crm_ready": False,
            "outbound_authorized": False,
            "next_steps": missing,
        })
    rows.sort(key=lambda x: (-x["score"], x["inn"]))
    return {
        "mode": "MANAGER_READINESS_OFFLINE",
        "crm_writes": "NO", "outbound_messages": "NO", "crm_ready": 0,
        "counts": {"candidates": len(rows),
                   "scope_pending": sum(r["scope"] == "PENDING" for r in rows),
                   "scope_reviewed_hold": sum(r["scope"] == "EXCLUSION_REVIEWED_NOT_CRM_ACTION" for r in rows),
                   "with_manual_evidence": sum(
                       r["evidence"] == "MANUAL_EVIDENCE_PRESENT_NOT_CRM_READY" for r in rows),
                   "ready_for_crm": 0},
        "missing_reasons": dict(reasons), "companies": rows,
        "note": "Local files are untrusted evidence; no CRM permissions or marketing consent verified."
    }


def write_report(report: dict, target: str | Path) -> Path:
    dest = Path(target).resolve()
    if not dest.is_relative_to(Path("exports").resolve()) or dest.suffix.lower() != ".json":
        raise ValueError("Output must be a JSON file under exports/")
    if dest.exists():
        raise FileExistsError("Won't overwrite prior report")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def main():
    parser = argparse.ArgumentParser(description="Offline manager readiness report for pilot")
    parser.add_argument("--research", default="exports/mvp_research_review.json")
    parser.add_argument("--enrichment", default="exports/mvp_enrichment_checklist.json")
    parser.add_argument("--scope", default="exports/mvp_scope_review.json")
    parser.add_argument("--output", default="exports/mvp_manager_readiness.json")
    args = parser.parse_args()
    report = assemble(args.research, args.enrichment, args.scope)
    path = write_report(report, args.output)
    print("MANAGER_READINESS=" + json.dumps({
        "file": str(path), "counts": report["counts"], "crm_writes": "NO",
        "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
