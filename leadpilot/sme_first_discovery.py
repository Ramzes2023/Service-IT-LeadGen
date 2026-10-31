"""SME-first pilot intake from a small, manually filtered OFFICIAL FNS GUI XLSX.

This does not query FNS, discover demand, collect contacts, or score businesses.
The FNS category (micro/small) is a strict PRECONDITION for further research,
not proof of a service requirement or authorization for outreach.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re
from zipfile import ZipFile

from .fns_gui_reconcile import (
    NS, SOURCE_URL, _cell_values, _xml, read_fns_xlsx,
)
from .verified_import import is_valid_inn

SME = {"MICRO", "SMALL"}
ALLOWED_REGION = {"77": "Москва", "50": "Московская область"}


def _previous(path: str | Path, mode: str) -> set[str]:
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != mode:
        raise ValueError("Unexpected previous FNS report mode")
    counts = obj.get("counts")
    if not isinstance(counts, dict) or counts.get("crm_ready") != 0:
        raise ValueError("Previous FNS report is not read-only/complete")
    if obj.get("crm_writes") != "NO":
        raise ValueError("Previous FNS report has unexpected CRM state")
    field = "items" if mode == "FNS_OFFICIAL_GUI_RECONCILIATION" else "companies"
    rows = obj.get(field)
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 100 or
            counts.get("total") != len(rows)):
        raise ValueError("Previous FNS report has invalid row count")
    inns: set[str] = set()
    valid_status = {"MICRO", "SMALL", "MEDIUM", "NOT_SME", "NOT_FOUND"}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed previous FNS row")
        inn = row.get("inn")
        if not is_valid_inn(inn) or inn in inns or row.get("fns_status") not in valid_status:
            raise ValueError("Invalid previous FNS INN or status")
        if row.get("crm_ready") is not False or row.get("outbound_authorized") is not False:
            raise ValueError("Previous FNS row unexpectedly approved")
        inns.add(inn)
    return inns


def _extra_columns(path: Path) -> dict[str, dict]:
    """Read only official FNS subject kind, region and OKVED. No contact fields."""
    with ZipFile(path) as z:
        if "xl/worksheets/sheet1.xml" not in z.namelist():
            raise ValueError("FNS worksheet missing")
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            from defusedxml.ElementTree import fromstring
            root = fromstring(_xml(z, "xl/sharedStrings.xml"))
            shared = ["".join(el.itertext()) for el in root.findall("s:si", NS)]
        cells = _cell_values(_xml(z, "xl/worksheets/sheet1.xml"), shared)
    header = {value: col for (row, col), value in cells.items() if row == 3}
    required = ("ИНН", "Тип субъекта", "Регион", "Основной вид деятельности")
    if any(key not in header for key in required):
        raise ValueError("FNS export must contain INN, legal-entity type, region and OKVED")
    result = {}
    for line in sorted({row for row, _ in cells if row >= 4}):
        inn = cells.get((line, header["ИНН"]), "")
        if not inn:
            continue
        if inn in result:
            raise ValueError("Repeated INN in extended FNS export")
        result[inn] = {
            "kind": cells.get((line, header["Тип субъекта"]), ""),
            "region_raw": cells.get((line, header["Регион"]), ""),
            "okved": cells.get((line, header["Основной вид деятельности"]), ""),
        }
    return result


def prepare(xlsx: str | Path, old_fns: str | Path, new_fns: str | Path, *,
            today: date | None = None, max_results: int = 50) -> dict:
    now = today or date.today()
    if type(max_results) is not int or not 1 <= max_results <= 100:
        raise ValueError("max_results must be 1..100")
    known_old = _previous(old_fns, "FNS_OFFICIAL_GUI_RECONCILIATION")
    known_new = _previous(new_fns, "FNS_NEW_THREE_OFFLINE_RECONCILIATION")
    if known_old & known_new:
        raise ValueError("Previous FNS batches overlap, cannot establish unique exclusions")
    known = known_old | known_new
    snapshot, records = read_fns_xlsx(xlsx, today=now)
    if not 0 <= (now - snapshot).days <= 60:
        raise ValueError("FNS discovery snapshot is stale or future dated")
    extras = _extra_columns(Path(xlsx))
    if records.keys() != extras.keys():
        raise ValueError("FNS workbook row metadata is inconsistent")

    counters = Counter()
    candidates = []
    for inn, official in records.items():
        counters["rows_in_fns_export"] += 1
        if not is_valid_inn(inn):
            raise ValueError("Invalid official INN checksum")
        if inn in known:
            counters["already_checked_18"] += 1
            continue
        if official["fns_status"] not in SME:
            counters["not_micro_or_small"] += 1
            continue
        extra = extras[inn]
        # This first MVP deals with legal entities only, not personal profiles of IPs.
        if extra["kind"].strip() != "Юридическое лицо":
            counters["individual_or_unknown_subject_skipped"] += 1
            continue
        m = re.match(r"^(77|50)\s*-\s*(.+)$", extra["region_raw"].strip())
        if (m is None or
                (m.group(1) == "77" and "москва" not in m.group(2).casefold()) or
                (m.group(1) == "50" and "область" not in m.group(2).casefold())):
            counters["outside_pilot_region_or_invalid"] += 1
            continue
        if not extra["okved"].strip() or len(extra["okved"]) > 250:
            counters["missing_or_invalid_okved"] += 1
            continue
        candidates.append({
            "inn": inn,
            "company_name": official["name"],
            "fns_category": official["fns_status"],
            "fns_category_label": official["category_label"],
            "region": ALLOWED_REGION[m.group(1)],
            "okved_public": extra["okved"].strip(),
            "fns_snapshot": snapshot.isoformat(),
            "fns_source": SOURCE_URL,
            "business_size_verified_from_export": True,
            "demand_for_1c": "NOT_CHECKED",
            "contact_channels": "NOT_COLLECTED",
            "legal_review": "NOT_PERFORMED",
            "crm_duplicates": "NOT_CHECKED",
            "qualification": "SIZE_ONLY_RESEARCH",
            "lead_score": None,
            "crm_ready": False,
            "outbound_authorized": False,
        })
    candidates.sort(key=lambda x: (x["region"], x["inn"]))
    if len(candidates) > max_results:
        raise ValueError("More eligible SMEs than max_results: narrow FNS filters, do not truncate silently")

    return {
        "mode": "SME_FIRST_FNS_SIZE_ONLY",
        "snapshot": snapshot.isoformat(),
        "origin": "Locally supplied FNS GUI export; digital signature not independently verified",
        "counts": {**dict(counters), "previously_checked_in_two_batches": len(known),
                   "size_eligible_for_research": len(candidates), "crm_ready": 0},
        "companies": candidates,
        "crm_writes": "NO",
        "outbound_messages": "NO",
        "warning": "FNS category is not evidence of 1C demand, buyer intent or permission to contact.",
    }


def save(report: dict, output: str | Path, inn_list: str | Path) -> dict:
    json_path = Path(output).resolve()
    txt_path = Path(inn_list).resolve()
    exports = Path("exports").resolve()
    if (not json_path.is_relative_to(exports)
            or not txt_path.is_relative_to(exports)
            or json_path.suffix.lower() != ".json"
            or txt_path.suffix.lower() != ".txt"
            or json_path == txt_path
            or json_path.exists() or txt_path.exists()):
        raise ValueError("Use two new JSON/TXT output paths inside exports/")
    if not report["companies"]:
        return {"saved": False, "reason": "NO_ELIGIBLE_MICRO_OR_SMALL", "crm_writes": "NO"}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with txt_path.open("x", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(item["inn"] for item in report["companies"]) + "\n")
    return {"saved": True, "json": str(json_path), "inn_list": str(txt_path),
            "count": len(report["companies"]), "crm_writes": "NO"}


def main() -> None:
    p = argparse.ArgumentParser(description="FNS micro/small Moscow/MO legal entities: research intake")
    p.add_argument("--xlsx", required=True, help="Locally saved FNS extended-search XLSX; limit to 50-100 entries")
    p.add_argument("--old-fns", default="exports/fns_official_reconciliation.json")
    p.add_argument("--new-fns", default="exports/round2_fns_three_reconciliation.json")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--output", default="exports/sme_first_size_only.json")
    p.add_argument("--inn-list", default="exports/sme_first_inns.txt")
    args = p.parse_args()
    report = prepare(args.xlsx, args.old_fns, args.new_fns, max_results=args.limit)
    status = save(report, args.output, args.inn_list)
    print("SME_FIRST_DISCOVERY=" + json.dumps({
        "counts": report["counts"], "files": status,
        "crm_writes": "NO", "outbound_messages": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
