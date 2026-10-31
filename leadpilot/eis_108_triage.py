"""Offline triage of saved EIS 1C notice review: hints, never sales leads.

Classify public-sector-looking buyer names and license/ITS mixed tenders
without querying external sources, changing upstream review, or allowing CRM.
No guess is sufficient to establish exact buyer INN or micro/small status.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import re

from .eis_rss import SOURCE_NAME, _url

MAX_BYTES = 3 * 1024 * 1024
PUBLIC_PREFIX = re.compile(
    r"^(?:ЗАКОНОДАТЕЛЬНОЕ\s+СОБРАНИЕ\b|АДМИНИСТРАЦИЯ\b|"
    r"КОМИТЕТ\s+(?:ФИНАНСОВ|ПО\s+УПРАВЛЕНИЮ)\b|"
    r"ДЕПАРТАМЕНТ\b|УПРАВЛЕНИЕ\s+(?:ФЕДЕРАЛЬНОЙ|ЗАПИСИ|"
    r"ГОСУДАРСТВЕННОЙ|ФИНАНСОВ|ОБРАЗОВАНИЯ)\b|"
    r"МИНИСТЕРСТВО\b|ПРАВИТЕЛЬСТВО\b|"
    r"ФЕДЕРАЛЬНАЯ\s+СЛУЖБА\b|"
    r"ГОСУДАРСТВЕННОЕ\s+СОБРАНИЕ\b)"
)
PUBLIC_MARKER = re.compile(
    r"(?:\b(?:ФГБУ|ФКУ|ФБУ|ГБУ|ГКУ|МБУ|МКУ|ГАУ|МАУ|"
    r"ГБУЗ|ГКУЗ|ФГБОУ|ГБОУ|ГУП|МУП|ОГКУ|ОГБУ|КГБУ|"
    r"МКОУ|МБОУ|УФСИН|ФСИН)\b|"
    r"\b(?:ГОСУДАРСТВЕННОЕ|МУНИЦИПАЛЬНОЕ|КАЗЕННОЕ|КАЗЁННОЕ|"
    r"КАЗЕННЫЙ|КАЗЁННЫЙ|БЮДЖЕТНОЕ)\s+"
    r"(?:[А-ЯЁ-]+\s+){0,3}УЧРЕЖДЕНИЕ\b)"
)
COMPANY_FORM = re.compile(
    r"^(?:ООО\b|АО\b|ПАО\b|ЗАО\b|ОАО\b|ИП\b|"
    r"ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ\b|"
    r"АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b)"
)
LICENSE = re.compile(r"ЛИЦЕНЗ|ПРАВ[АО]\s+ИСПОЛЬЗОВАНИЯ")
SERVICE = re.compile(r"СОПРОВОЖД|ТЕХНИЧЕСК(?:АЯ|ОЙ)\s+ПОДДЕРЖ|"
                     r"ДОРАБОТ|АДАПТАЦ|МОДИФИКАЦ|НАСТРОЙК|"
                     r"КОНСУЛЬТАЦИОНН|ОБСЛУЖИВАН")
ITS = re.compile(r"(?:\bИТС\b|ИНФОРМАЦИОННО[- ]ТЕХНОЛОГИЧЕСК)")


def _buyer_hint(name: str) -> str:
    n = " ".join(name.upper().replace("Ё", "Е").split())
    if not n:
        return "BUYER_NAME_MISSING"
    if PUBLIC_PREFIX.search(n) or PUBLIC_MARKER.search(n):
        return "POSSIBLE_PUBLIC_SECTOR_BUYER"
    if COMPANY_FORM.search(n):
        return "LEGAL_FORM_LOOKS_COMMERCIAL_SIZE_UNVERIFIED"
    return "BUYER_LEGAL_FORM_UNVERIFIED"


def _procurement_hint(title: str) -> str:
    t = " ".join(title.upper().replace("Ё", "Е").split())
    licensing = bool(LICENSE.search(t))
    service = bool(SERVICE.search(t))
    its = bool(ITS.search(t))
    if licensing and (service or its):
        return "MIXED_LICENSE_AND_SERVICE_REVIEW"
    if licensing:
        return "LICENSE_SUPPLY_NOT_VERIFIED_EXTERNAL_SUPPORT"
    if its:
        return "ITS_SUBSCRIPTION_OR_PACKAGE_REVIEW"
    if service:
        return "EXPLICIT_1C_SERVICE_REVIEW"
    return "OTHER_OR_UNCLEAR_1C_PURCHASE_REVIEW"


def triage(report: dict) -> dict:
    if (not isinstance(report, dict)
            or report.get("mode") != "EIS_PREQUALIFICATION_EVIDENCE_REVIEW_ONLY"
            or report.get("source") != SOURCE_NAME
            or report.get("crm_writes") != "NO"
            or report.get("outbound_messages") != "NO"):
        raise ValueError("EIS_TRIAGE_SOURCE_NOT_TRUSTED")
    counts = report.get("counts")
    rows = report.get("items")
    if not isinstance(counts, dict) or not isinstance(rows, list):
        raise ValueError("EIS_TRIAGE_INVALID_REPORT")
    total = counts.get("unique_notices_to_review")
    if type(total) is not int or not 0 <= total <= 500 or total != len(rows):
        raise ValueError("EIS_TRIAGE_INVALID_COUNT")
    if (counts.get("qualified_b2b_leads") != 0
            or counts.get("crm_ready") != 0
            or counts.get("exact_inn_fns_verified") != 0):
        raise ValueError("EIS_TRIAGE_PREAPPROVAL_FORBIDDEN")
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("EIS_TRIAGE_BAD_ROW")
        url = row.get("official_notice_url")
        if not isinstance(url, str) or _url(url, feed=False) != url or url in seen:
            raise ValueError("EIS_TRIAGE_UNTRUSTED_OR_DUPLICATE_URL")
        seen.add(url)
        title, buyer = row.get("title"), row.get("buyer_name_rss_unverified")
        if (not isinstance(title, str) or not 1 <= len(title) <= 700
                or not isinstance(buyer, str) or len(buyer) > 250
                or row.get("buyer_inn") not in ("", None)
                or row.get("fns_size") != "UNVERIFIED"
                or row.get("crm_ready") is not False
                or row.get("review_status") != "RESEARCH_ONLY_NOT_A_QUALIFIED_LEAD"):
            raise ValueError("EIS_TRIAGE_UNVERIFIED_INPUT_REQUIRED")
        published = row.get("published_on")
        if not isinstance(published, str):
            raise ValueError("EIS_TRIAGE_DATE_INVALID")
        if published:
            try:
                date.fromisoformat(published)
            except ValueError:
                raise ValueError("EIS_TRIAGE_DATE_INVALID") from None
        buyer_hint = _buyer_hint(buyer)
        subject_hint = _procurement_hint(title)
        # A suggested legal form is NOT proof of SME status or that the
        # organisation actually purchased anything. Remains human review.
        group = ("PUBLIC_BUYER_REVIEW_SEPARATELY"
                 if buyer_hint == "POSSIBLE_PUBLIC_SECTOR_BUYER"
                 else "FNS_BUYER_INN_AND_GEOGRAPHY_REVIEW_REQUIRED")
        out.append({
            "notice_id": row.get("notice_id"),
            "official_notice_url": url,
            "published_on": published,
            "procurement_subject": title,
            "buyer_name_rss_unverified": buyer,
            "buyer_evidence_hint": buyer_hint,
            "purchase_evidence_hint": subject_hint,
            "manual_work_queue": group,
            "review_holds": (
                "OFFICIAL_BUYER_INN;FNS_SIZE;GEOGRAPHY_MOSCOW_MO;"
                "ACTUAL_PROCUREMENT_SCOPE;ACTIVE_DEADLINE;"
                "CONTACT_LEGAL_BASIS;CRM_DUPLICATE_STOP_LIST;MANAGER_APPROVAL"),
            "qualified_lead": False, "crm_ready": False,
            "outbound_authorized": False,
        })
    out.sort(key=lambda r: (r["manual_work_queue"] == "PUBLIC_BUYER_REVIEW_SEPARATELY",
                            r["purchase_evidence_hint"] != "EXPLICIT_1C_SERVICE_REVIEW",
                            r["published_on"] or "9999", r["notice_id"] or ""))
    def count(key):
        categories = sorted(set(x[key] for x in out))
        return {category: sum(x[key] == category for x in out) for category in categories}
    return {
        "mode": "EIS_108_COMMERCIALLY_SAFE_TRIAGE_ONLY",
        "source": SOURCE_NAME,
        "counts": {"unique_notices": len(out),
                   "buyer_hints": count("buyer_evidence_hint"),
                   "purchase_hints": count("purchase_evidence_hint"),
                   "public_sector_text_hints": sum(
                       x["buyer_evidence_hint"] == "POSSIBLE_PUBLIC_SECTOR_BUYER" for x in out),
                   "confirmed_small_business_buyers": 0,
                   "qualified_b2b_leads": 0,
                   "crm_ready": 0},
        "rows": out,
        "crm_writes": "NO", "outbound_messages": "NO",
    }


def _excel(value: object) -> str:
    value = str(value if value is not None else "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def save(report: dict, json_file: str, csv_file: str) -> tuple[Path, Path]:
    json_target, csv_target = Path(json_file).resolve(), Path(csv_file).resolve()
    root = Path("exports").resolve()
    if (json_target == csv_target
            or json_target.suffix != ".json" or csv_target.suffix != ".csv"
            or any(not p.is_relative_to(root) or p.exists() for p in (json_target, csv_target))):
        raise ValueError("EIS_TRIAGE_OUTPUT_FILES_INVALID")
    for path in (json_target, csv_target):
        path.parent.mkdir(parents=True, exist_ok=True)
    with json_target.open("x", encoding="utf8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    cols = ("notice_id", "published_on", "procurement_subject",
            "buyer_name_rss_unverified", "buyer_evidence_hint",
            "purchase_evidence_hint", "manual_work_queue",
            "official_notice_url", "review_holds", "qualified_lead",
            "crm_ready", "outbound_authorized")
    with csv_target.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({column: _excel(row[column]) for column in cols})
    return json_target, csv_target


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify 108 official EIS notices for human review only")
    parser.add_argument("--review-json", required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--csv", required=True)
    a = parser.parse_args()
    try:
        source = Path(a.review_json)
        if not source.is_file() or not 1 <= source.stat().st_size <= MAX_BYTES:
            raise ValueError("EIS_TRIAGE_REVIEW_FILE_MISSING")
        prior = json.loads(source.read_text(encoding="utf8-sig"))
        result = triage(prior)
        files = save(result, a.json, a.csv)
    except (OSError, UnicodeError, ValueError) as exc:
        print("EIS_TRIAGE=BLOCKED; REASON=" + str(exc).splitlines()[0][:90])
        raise SystemExit(2)
    print("EIS_TRIAGE_COUNTS=" + json.dumps(result["counts"], ensure_ascii=True, sort_keys=True))
    print("EIS_TRIAGE_PREVIEW=" + json.dumps([
        {"buyer": x["buyer_name_rss_unverified"], "buy_hint": x["buyer_evidence_hint"],
         "scope_hint": x["purchase_evidence_hint"], "official_notice_url": x["official_notice_url"]}
        for x in result["rows"][:12]], ensure_ascii=True))
    print("EIS_TRIAGE_JSON=" + str(files[0]))
    print("EIS_TRIAGE_CSV=" + str(files[1]))
    print("EIS_TRIAGE_FINISHED=YES; QUALIFIED_B2B_LEADS=0; CRM_WRITES=NO")


if __name__ == "__main__":
    main()
