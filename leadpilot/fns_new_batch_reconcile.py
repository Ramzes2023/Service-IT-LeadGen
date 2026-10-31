"""Validate a NEW manual FNS batch against the fresh 1C employer precheck.

The prior 15-company pilot FNS report must NEVER be used as this batch's
reference. Input files stay local; no network, CRM write, contact or AI action.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from .fns_gui_reconcile import read_fns_xlsx, read_not_found, SOURCE_URL
from .verified_import import is_valid_inn

MODE = "NEW_1C_FNS_MULTIPAGE_PRECHECK_ONLY"
FNS_CODES = {"MICRO", "SMALL", "MEDIUM", "NOT_SME"}


def _read_precheck(path: str | Path, now: date) -> tuple[dict[str, dict], dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != MODE:
        raise ValueError("Expected new multi-page precheck, not old 15-company FNS scope")
    counts = obj.get("counts")
    rows = obj.get("candidates")
    if (not isinstance(counts, dict) or not isinstance(rows, list)
            or not 1 <= len(rows) <= 100
            or counts.get("selected_for_fns") != len(rows)
            or counts.get("truncated") is not False
            or counts.get("ready_for_crm") != 0
            or obj.get("crm_writes") != "NO"
            or obj.get("outbound_messages") != "NO"
            or obj.get("fns_source") != SOURCE_URL
            or obj.get("identity_conflicts_hold") != []):
        raise ValueError("Incomplete or unsafe precheck; do not approve FNS batch")
    try:
        created = date.fromisoformat(obj["date"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("Invalid precheck date") from None
    if (now - created).days < 0 or (now - created).days > 60:
        raise ValueError("Precheck is future dated or stale")
    by_inn = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed precheck candidate")
        inn = row.get("inn")
        if (not is_valid_inn(inn) or inn in by_inn or
                row.get("business_size") != "UNVERIFIED"
                or row.get("crm_ready") is not False
                or row.get("outbound_authorized") is not False
                or row.get("qualified_as_customer") is not False
                or not isinstance(row.get("employer"), str)
                or len(row["employer"].strip()) < 2):
            raise ValueError("Invalid INN or pre-approved employer in precheck")
        by_inn[inn] = row
    return by_inn, obj


def reconcile_new(precheck: str | Path, *,
                  xlsx: str | Path | None = None,
                  not_found: str | Path | None = None,
                  checked_on: str | None = None,
                  today: date | None = None) -> dict:
    """Use independent local UI exports. No Excel required if ALL are not found."""
    now = today or date.today()
    expected, precheck_meta = _read_precheck(precheck, now)
    if xlsx is None and not_found is None:
        raise ValueError("Supply actual FNS XLSX, not-found TXT, or both")
    if xlsx is not None:
        snapshot, found = read_fns_xlsx(xlsx, today=now)
    else:
        if checked_on is None:
            raise ValueError("An operator-verified --checked-on date is required for TXT-only FNS results")
        try:
            snapshot = date.fromisoformat(checked_on)
        except (ValueError, TypeError):
            raise ValueError("Invalid operator-verified FNS date") from None
        if snapshot > now or (now - snapshot).days > 60:
            raise ValueError("Stale or future dated TXT-only FNS check")
        found = {}
    if checked_on is not None and date.fromisoformat(checked_on) != snapshot:
        raise ValueError("FNS XLSX snapshot and checked-on dates do not match")
    missing = read_not_found(not_found) if not_found is not None else set()
    if found.keys() & missing:
        raise ValueError("Same INN in both found and not-found FNS lists")
    if set(found) | missing != set(expected):
        raise ValueError("FNS exports must cover exactly the new shortlist; missing/extra INNs")
    if len(found) + len(missing) != len(expected):
        raise ValueError("FNS batch does not have complete unique coverage")

    stats = Counter()
    companies = []
    for inn in sorted(expected):
        prospect = expected[inn]
        item = found.get(inn)
        code = item["fns_status"] if item is not None else "NOT_FOUND"
        if item is not None and code not in FNS_CODES:
            raise ValueError("Unsupported FNS category")
        if code in {"MICRO", "SMALL"}:
            next_step = "Проверить интерес к внешнему 1С, юридические риски, корпоративные контакты и CRM-дубли"
            triage = "SME_SIZE_ONLY_HOLD"
        elif code == "MEDIUM":
            triage = "OUTSIDE_SMALL_PILOT"
            next_step = "Среднее предприятие: отложить для отдельного сегмента"
        elif code == "NOT_SME":
            triage = "NOT_CURRENT_SME_REVIEW"
            next_step = "Не числится действующим субъектом МСП; проверить статус организации"
        else:
            triage = "SIZE_UNKNOWN_NOT_FOUND"
            next_step = "Отсутствие записи не означает крупную компанию; проверить статус вручную"
        stats[code] += 1
        stats[triage] += 1
        companies.append({
            "inn": inn, "employer_from_vacancy": prospect["employer"],
            "official_fns_name": item["name"] if item else None,
            "fns_status": code, "fns_snapshot": snapshot.isoformat(),
            "fns_source": SOURCE_URL,
            "official_fns_category_seen_in_xlsx": item is not None,
            "source_signal_url": prospect["signal_url"],
            "source_signal_date": prospect["signal_date"],
            "decision": triage, "next_action": next_step,
            "manual_verification_required": True,
            "crm_ready": False, "outbound_authorized": False,
        })
    return {
        "mode": "FNS_NEW_THREE_OFFLINE_RECONCILIATION",
        "snapshot": snapshot.isoformat(),
        "origin": ("User-supplied FNS GUI XLSX/TXT; not cryptographically authenticated"
                   if xlsx is not None else
                   "Operator-supplied FNS GUI missing-INN TXT; date manually attested, not signed"),
        "counts": {"total": len(companies), "found": len(found),
                   "not_found": len(missing), "micro": stats["MICRO"],
                   "small": stats["SMALL"], "medium": stats["MEDIUM"],
                   "not_sme": stats["NOT_SME"], "size_match_only_hold": stats["SME_SIZE_ONLY_HOLD"],
                   "crm_ready": 0},
        "companies": companies,
        "crm_writes": "NO", "outbound_messages": "NO",
        "note": "No SME result implies intent to buy. No CRM duplicates/contacts/legal review completed."
    }


def save(report: dict, target: str | Path) -> str:
    output = Path(target).resolve()
    if output.suffix.lower() != ".json" or not output.is_relative_to(Path("exports").resolve()):
        raise ValueError("Output must be a JSON file in exports/")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return str(output)


def main() -> None:
    p = argparse.ArgumentParser(description="Join new three INNs with manually exported FNS results")
    p.add_argument("--precheck", default="exports/round2_combined_precheck.json")
    p.add_argument("--xlsx", default=None)
    p.add_argument("--not-found", default=None)
    p.add_argument("--checked-on", default=None,
                   help="Required for TXT-only FNS check, YYYY-MM-DD")
    p.add_argument("--output", default="exports/round2_fns_three_reconciliation.json")
    args = p.parse_args()
    obj = reconcile_new(args.precheck, xlsx=args.xlsx, not_found=args.not_found,
                        checked_on=args.checked_on)
    filename = save(obj, args.output)
    print("FNS_NEXT_BATCH=" + json.dumps(
        {"output": filename, "counts": obj["counts"],
         "crm_writes": "NO", "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
