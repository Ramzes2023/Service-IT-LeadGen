"""Offline manager decisions using official FNS batch-export reconciliation.

Never accepts an unverified size claim as CRM readiness and never writes CRM.
Inputs are independent local JSON reports; validation is deliberately fail-closed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

ALLOWED_FNS = {"MICRO", "SMALL", "MEDIUM", "NOT_SME", "NOT_FOUND"}
FNS_SOURCE = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def _read(path: str | Path, mode: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("mode") != mode:
        raise ValueError(f"Wrong input mode: expected {mode}")
    return value


def _by_inn(items: object, label: str) -> dict[str, dict]:
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError(f"{label}: invalid record count")
    found = {}
    for record in items:
        if not isinstance(record, dict):
            raise ValueError(f"{label}: invalid row")
        inn = record.get("inn")
        if (not isinstance(inn, str) or len(inn) not in (10, 12)
                or not inn.isascii() or not inn.isdigit() or inn in found):
            raise ValueError(f"{label}: missing or duplicate INN")
        found[inn] = record
    return found


def reconcile_for_manager(manager_path: str | Path,
                          fns_path: str | Path,
                          *, today: date | None = None) -> dict:
    """Join by exact INN, with mandatory review holds for every result."""
    now = today or date.today()
    manager = _read(manager_path, "MANAGER_READINESS_OFFLINE")
    fns = _read(fns_path, "FNS_OFFICIAL_GUI_RECONCILIATION")
    if (manager.get("crm_writes") != "NO" or manager.get("outbound_messages") != "NO"
            or manager.get("crm_ready") != 0 or
            fns.get("crm_writes") != "NO" or fns.get("outreach") != "NOT_AUTHORIZED"):
        raise ValueError("Upstream reports must be strictly read-only")
    if (not isinstance(manager.get("counts"), dict) or
            not isinstance(fns.get("counts"), dict) or
            fns["counts"].get("crm_ready") != 0):
        raise ValueError("Missing zero-CRM readiness counters")
    man = _by_inn(manager.get("companies"), "manager")
    official = _by_inn(fns.get("items"), "fns")
    if man.keys() != official.keys():
        raise ValueError("Manager and official FNS INNs differ; no partial join")
    if manager["counts"].get("candidates") != len(man) or fns["counts"].get("total") != len(official):
        raise ValueError("Input company counts do not match")
    try:
        snapshot = date.fromisoformat(fns["snapshot"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid FNS snapshot date") from None
    if snapshot > now:
        raise ValueError("Future FNS snapshot")
    is_stale = (now - snapshot).days > 60
    records, buckets = [], Counter()
    for inn, lead in man.items():
        result = official[inn]
        code = result.get("fns_status")
        if code not in ALLOWED_FNS or result.get("snapshot") != snapshot.isoformat():
            raise ValueError("FNS status or row snapshot invalid")
        if result.get("source") != FNS_SOURCE:
            raise ValueError("Unexpected FNS source URL")
        if (lead.get("status") != "HOLD_FOR_REVIEW"
                or lead.get("crm_ready") is not False
                or lead.get("outbound_authorized") is not False
                or result.get("crm_ready") is not False
                or result.get("outbound_authorized") is not False):
            raise ValueError("Upstream lead unexpectedly marked approved")
        public_hint = lead.get("public_research_action")
        if public_hint is not None and public_hint not in ("LEGAL_REVIEW", "DEFER_SCOPE", "VERIFY_FNS"):
            raise ValueError("Unknown research hint")
        if public_hint == "LEGAL_REVIEW":
            decision = "LEGAL_HOLD"
            next_step = "Сначала отдельная проверка применимых ограничений, без коммерческих контактов"
        elif code in ("MICRO", "SMALL"):
            decision = "SIZE_MATCH_ONLY"
            next_step = "Проверить реальные потребности 1С, корпоративный контакт, дубли и разрешения"
        elif code == "MEDIUM":
            decision = "OUTSIDE_SMALL_BUSINESS_PILOT"
            next_step = "Не включать в очередь первого пилота малого бизнеса"
        elif code == "NOT_SME":
            decision = "EXCLUDED_FROM_SME_REGISTER_REVIEW"
            next_step = "Уточнить правовой статус и причину отсутствия действующей МСП-категории"
        else:
            decision = "NOT_FOUND_SIZE_UNKNOWN"
            next_step = "Не считать крупным; проверить актуальный статус и реестр ФНС вручную"
        if is_stale and decision != "LEGAL_HOLD":
            decision = "STALE_FNS_RECHECK"
            next_step = "Повторить проверку ФНС: срез старше 60 дней"
        buckets[code] += 1
        buckets[decision] += 1
        records.append({
            "inn": inn,
            "company": str(lead.get("company") or ""),
            "region": str(lead.get("region") or ""),
            "lead_score": lead.get("score"),
            "fns_status": code,
            "fns_snapshot": snapshot.isoformat(),
            "fns_origin": fns.get("origin", "User-supplied XLSX/TXT"),
            "decision": decision,
            "next_action": next_step,
            "legal_review_hint": public_hint == "LEGAL_REVIEW",
            "crm_ready": False,
            "outbound_authorized": False,
            "size_check_alone_is_sufficient": False,
        })
    # Detect source counter tampering or partial downloads; don't trust caller totals.
    expected = {"micro": buckets["MICRO"], "small": buckets["SMALL"],
                "medium": buckets["MEDIUM"], "not_sme": buckets["NOT_SME"],
                "not_found": buckets["NOT_FOUND"]}
    expected["found"] = len(records) - buckets["NOT_FOUND"]
    if any(fns["counts"].get(k) != v for k, v in expected.items()):
        raise ValueError("FNS counts conflict with item-level records")
    records.sort(key=lambda x: (
        {"LEGAL_HOLD": 0, "SIZE_MATCH_ONLY": 1,
         "NOT_FOUND_SIZE_UNKNOWN": 2, "STALE_FNS_RECHECK": 3,
         "OUTSIDE_SMALL_BUSINESS_PILOT": 4,
         "EXCLUDED_FROM_SME_REGISTER_REVIEW": 5}[x["decision"]], x["inn"]))
    return {
        "mode": "FNS_MANAGER_GATE_OFFLINE",
        "snapshot": snapshot.isoformat(),
        "fns_snapshot_stale": is_stale,
        "counts": {**expected, "total": len(records),
                   "legal_hold": buckets["LEGAL_HOLD"],
                   "size_match_only": buckets["SIZE_MATCH_ONLY"],
                   "crm_ready": 0},
        "companies": records,
        "crm_writes": "NO", "outbound_messages": "NO",
        "notes": [
            "FNS downloaded data is not cryptographically authenticated in this pipeline.",
            "NOT_FOUND means unknown size, not large enterprise.",
            "SMALL or MICRO does not prove willingness to purchase 1C services.",
            "No CRM duplicate check and no consent/other legal basis for outreach were established."
        ]
    }


def save_report(report: dict, path: str | Path) -> str:
    output = Path(path).resolve()
    if output.suffix.lower() != ".json" or not output.is_relative_to(Path("exports").resolve()):
        raise ValueError("Output must be JSON inside exports")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return str(output)


def main() -> None:
    p = argparse.ArgumentParser(description="Join FNS evidence into an offline manager report")
    p.add_argument("--manager", default="exports/mvp_manager_readiness_with_hints.json")
    p.add_argument("--fns", default="exports/fns_official_reconciliation.json")
    p.add_argument("--output", default="exports/mvp_fns_manager_gate.json")
    a = p.parse_args()
    result = reconcile_for_manager(a.manager, a.fns)
    target = save_report(result, a.output)
    print("FNS_MANAGER_GATE=" + json.dumps({
        "path": target, "counts": result["counts"],
        "crm_writes": "NO", "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
