"""Reconcile FNS batch-search XLSX and not-found TXT against an offline pilot list.

Strict, read-only import of a tiny official GUI export. No website calls, no CRM
writes, no contact harvesting. It does not validate FNS signatures.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import re
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CATEGORIES = {
    "Микропредприятие": "MICRO",
    "Малое предприятие": "SMALL",
    "Среднее предприятие": "MEDIUM",
    "Не является субъектом МСП": "NOT_SME",
}
SOURCE_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"
MAX_ZIP_BYTES = 12 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024


def _inn(value: object) -> str:
    s = str(value).strip() if value is not None else ""
    if not s.isascii() or not s.isdigit() or len(s) not in (10, 12):
        raise ValueError("FNS export contains malformed INN")
    return s


def _xml(zipfile: ZipFile, name: str) -> bytes:
    info = zipfile.getinfo(name)
    if info.file_size > MAX_XML_BYTES or info.file_size <= 0:
        raise ValueError("Unsafe XLSX XML member size")
    return zipfile.read(name)


def _cell_values(xml: bytes, shared: list[str]) -> dict[tuple[int, str], str]:
    root = fromstring(xml)
    if root.tag != "{%s}worksheet" % NS["s"]:
        raise ValueError("Unsupported XLSX worksheet format")
    cells = {}
    for cell in root.findall(".//s:sheetData/s:row/s:c", NS):
        ref = cell.get("r", "")
        m = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", ref)
        if not m:
            raise ValueError("Malformed XLSX cell reference")
        if cell.find("s:f", NS) is not None:
            raise ValueError("Formulas not permitted in an FNS export")
        kind = cell.get("t")
        if kind == "inlineStr":
            inline = cell.find("s:is", NS)
            value = "".join(inline.itertext()) if inline is not None else ""
        elif kind == "s":
            node = cell.find("s:v", NS)
            if node is None:
                raise ValueError("Malformed shared string reference")
            try:
                value = shared[int(node.text)]
            except (IndexError, ValueError, TypeError):
                raise ValueError("Invalid shared string") from None
        elif kind in (None, "n", "str"):
            node = cell.find("s:v", NS)
            value = node.text or "" if node is not None else ""
        else:
            raise ValueError("Unexpected Excel cell encoding")
        cells[(int(m.group(2)), m.group(1))] = value.strip()
    return cells


def read_fns_xlsx(path: str | Path, *, today: date | None = None) -> tuple[date, dict[str, dict]]:
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".xlsx" or p.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("Expected a small XLSX file downloaded from the FNS batch-search UI")
    with ZipFile(p) as z:
        names = set(z.namelist())
        if "xl/worksheets/sheet1.xml" not in names:
            raise ValueError("No first FNS worksheet")
        if any("/externalLinks/" in x or "vbaProject" in x or x.endswith(".bin") for x in names):
            raise ValueError("Unexpected active/external workbook content")
        if any(v.file_size > MAX_XML_BYTES or v.file_size > 250 * max(v.compress_size, 1)
               for v in z.infolist()):
            raise ValueError("Dangerous XLSX compression/size")
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = fromstring(_xml(z, "xl/sharedStrings.xml"))
            shared = ["".join(el.itertext()) for el in root.findall("s:si", NS)]
        cells = _cell_values(_xml(z, "xl/worksheets/sheet1.xml"), shared)
    label = cells.get((1, "A"), "")
    m = re.search(r"по состоянию на (\d{2}\.\d{2}\.\d{4})", label)
    if not m or "Единый реестр" not in label:
        raise ValueError("Cannot establish FNS snapshot date from worksheet title")
    snapshot = datetime.strptime(m.group(1), "%d.%m.%Y").date()
    if snapshot > (today or date.today()):
        raise ValueError("Future dated FNS export")
    headers = {cells.get((3, col), ""): col for col in (
        "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K",
        "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V")}
    required = ("ИНН", "Категория", "Наименование / ФИО",
                "Дата включения в реестр", "Дата исключения из реестра")
    if any(col not in headers for col in required):
        raise ValueError("FNS column names changed; manual schema review required")
    rows = {}
    indexed_rows = sorted({r for r, _ in cells if r >= 4})
    if len(indexed_rows) > 100:
        raise ValueError("Unexpected bulk export; max 100 records per pilot")
    for line in indexed_rows:
        fields = {col: cells.get((line, headers[col]), "") for col in required}
        if not any(fields.values()):
            continue
        inn = _inn(fields["ИНН"])
        category = fields["Категория"]
        if category not in CATEGORIES or inn in rows or not fields["Наименование / ФИО"]:
            raise ValueError("Unexpected, conflicting or duplicate FNS record")
        if category != "Не является субъектом МСП" and fields["Дата исключения из реестра"]:
            raise ValueError("Active SME category conflicts with exclusion date")
        rows[inn] = {
            "inn": inn, "name": fields["Наименование / ФИО"],
            "fns_status": CATEGORIES[category], "category_label": category,
            "included_on": fields["Дата включения в реестр"] or None,
            "excluded_on": fields["Дата исключения из реестра"] or None,
            "source": SOURCE_URL, "snapshot": snapshot.isoformat(),
        }
    if not rows:
        raise ValueError("No FNS records in downloaded Excel")
    return snapshot, rows


def read_not_found(path: str | Path) -> set[str]:
    p = Path(path)
    if not p.is_file() or p.stat().st_size > 20000:
        raise ValueError("Expected small TXT of missing INNs")
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    if not 1 <= len(lines) <= 100:
        raise ValueError("Invalid missing INNs count")
    out = {_inn(line) for line in lines}
    if len(out) != len(lines):
        raise ValueError("Duplicate INN in not-found TXT")
    return out


def _local_scope(path: str | Path) -> set[str]:
    obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != "PILOT_SCOPE_REVIEW":
        raise ValueError("Expected existing local PILOT_SCOPE_REVIEW")
    items = obj.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Invalid local review companies")
    inns = [_inn(item.get("inn")) for item in items if isinstance(item, dict)]
    if len(inns) != len(items) or len(set(inns)) != len(items):
        raise ValueError("Invalid or duplicated INNs in local scope")
    return set(inns)


def reconcile(xlsx: str | Path, missing_txt: str | Path, review: str | Path,
              *, today: date | None = None) -> dict:
    snapshot, found = read_fns_xlsx(xlsx, today=today)
    absent = read_not_found(missing_txt)
    shortlist = _local_scope(review)
    if found.keys() & absent:
        raise ValueError("Same INN both found and not-found")
    if set(found) | absent != shortlist:
        raise ValueError("Official results and shortlisted INNs differ")
    counts = Counter(x["fns_status"] for x in found.values())
    records = []
    for inn in sorted(shortlist):
        if inn in found:
            row = dict(found[inn])
        else:
            row = {"inn":inn, "name":None, "fns_status":"NOT_FOUND",
                   "category_label":None, "included_on":None, "excluded_on":None,
                   "source": SOURCE_URL, "snapshot":snapshot.isoformat()}
        row.update({"crm_ready":False,"outbound_authorized":False,
                    "manual_review_required":True})
        records.append(row)
    return {
        "mode":"FNS_OFFICIAL_GUI_RECONCILIATION",
        "snapshot":snapshot.isoformat(),
        "origin":"User-supplied official GUI XLSX/TXT; digital signature not independently verified",
        "counts":{"total":len(shortlist),"found":len(found),"not_found":len(absent),
                  "micro":counts["MICRO"],"small":counts["SMALL"],
                  "medium":counts["MEDIUM"],"not_sme":counts["NOT_SME"],
                  "crm_ready":0},
        "crm_writes":"NO","outreach":"NOT_AUTHORIZED", "items":records,
        "warning":"Missing from FNS does NOT establish large business status. The small SME record does NOT authorize contact."
    }


def save(report: dict, output: str | Path) -> Path:
    dest = Path(output).resolve()
    if not dest.is_relative_to(Path("exports").resolve()) or dest.suffix.lower() != ".json":
        raise ValueError("Output must be JSON inside exports")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return dest


def main():
    ap = argparse.ArgumentParser(description="Validate offline official FNS XLSX + TXT against 15 INNs")
    ap.add_argument("--xlsx",required=True)
    ap.add_argument("--not-found",required=True)
    ap.add_argument("--scope",default="exports/mvp_scope_review.json")
    ap.add_argument("--output",default="exports/fns_official_reconciliation.json")
    args=ap.parse_args()
    report=reconcile(args.xlsx,args.not_found,args.scope)
    target=save(report,args.output)
    print("FNS_OFFICIAL_IMPORT=" + json.dumps({
        "output":str(target),"counts":report["counts"],
        "crm_writes":"NO","outreach":"NOT_AUTHORIZED"},ensure_ascii=False))


if __name__ == "__main__":
    main()
