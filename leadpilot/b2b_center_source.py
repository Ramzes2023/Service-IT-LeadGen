"""Read-only B2B-Center commercial-procurement ingestion boundary.

IMPORTANT: Not a live API client. The official B2B-Center API has methods,
formats and tariff entitlements described only within an authenticated
participant account. Operator supplies normalized, explicitly authorized
exports; no scraping, undocumented API endpoints or Bitrix calls.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .verified_import import is_valid_inn

MODE = "B2B_CENTER_APPROVED_EXPORT_NORMALIZED_V1"
CHANNELS = {"APPROVED_API_EXPORT", "AUTHORIZED_OPERATOR_EXPORT"}
ALLOWED_REGIONS = {"Москва", "Московская область"}
CATEGORIES = {
    "1C_SUPPORT": ("1с", "1c", "бухгалтерия предприятия", "зуп", "унф"),
    "IT_OUTSOURCING": ("it-аутсорсинг", "ит-аутсорсинг", "it аутсорсинг", "аутсорсинг ит",
                       "аутсорсинг it", "поддержка рабочих мест", "техническая поддержка"),
    "KKT_MARKING": ("ккт", "касс", "офд", "честный знак", "маркировк", "егаис"),
    "TRADE_EQUIPMENT": ("торговое оборудование", "торгового оборудован", "тсд",
                        "сканер штрих", "весовое оборудование", "pos-терминал", "pos терминал"),
    "EDO_SIGNATURE": ("криптопро", "эдо", "диадок", "электронная подпись",
                       "электронной подписи", "сбис"),
    "CRM_BITRIX": ("битрикс24", "битрикс 24", "crm", "срм"),
    "SERVERS_NETWORKS": ("сервер", "локальной сети", "сетевой инфраструктур",
                         "компьютерной сети", "резервное копирование", "администрирование сети"),
    "INTEGRATIONS": ("интеграц", "api", "обмен данными", "обмен между"),
    "BOTS_AI": ("чат-бот", "чатбот", "ии-агент", "ии агент", "искусственн",
                "ai-агент", "ai агент"),
}
SERVICE_ACTIONS = ("настройк", "подключен", "внедрен", "сопровожд", "обслуживан",
                   "поддержк", "доработ", "интеграц", "разработ", "монтаж",
                   "установк", "администрир", "автоматизац", "модернизац",
                   "выполнени", "оказани", "проектирован", "ремонт")
NUMBER_PATH = re.compile(r"/search/number/l[0-9]+(?:-[0-9]+){1,8}/?\Z")
REQUIRED = {"procurement_id", "source_url", "source_origin", "procurement_class",
            "buyer_inn", "buyer_name", "buyer_inn_role", "buyer_region",
            "title", "description_excerpt", "published_on", "deadline_on"}


def _url(raw: object) -> str:
    if not isinstance(raw, str) or not 20 <= len(raw) <= 1500 or re.search(r"[\x00-\x20\\]", raw):
        raise ValueError("B2B_SOURCE_URL_INVALID")
    try:
        u = urlsplit(raw)
        if (u.scheme != "https" or (u.hostname or "").lower() != "www.b2b-center.ru"
                or u.username or u.password or u.port not in (None, 443) or u.fragment):
            raise ValueError()
        if NUMBER_PATH.fullmatch(u.path) and not u.query:
            return raw
        if u.path == "/market/view.html" and set(parse_qs(u.query)) == {"id"}:
            ids = parse_qs(u.query).get("id", [])
            if len(ids) == 1 and ids[0].isascii() and ids[0].isdigit() and 5 <= len(ids[0]) <= 12:
                return raw
    except (ValueError, TypeError):
        pass
    raise ValueError("B2B_SOURCE_URL_INVALID")


def _day(raw: object, field: str) -> date:
    if not isinstance(raw, str):
        raise ValueError("B2B_INVALID_" + field.upper())
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise ValueError("B2B_INVALID_" + field.upper()) from None
    if d.isoformat() != raw:
        raise ValueError("B2B_INVALID_" + field.upper())
    return d


def _text(raw: object, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(raw, str) or not minimum <= len(raw.strip()) <= maximum:
        raise ValueError("B2B_INVALID_" + field.upper())
    v = raw.strip()
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v):
        raise ValueError("B2B_INVALID_" + field.upper())
    return v


def classify(title: str, excerpt: str) -> tuple[list[str], bool]:
    t = (title + " " + excerpt).casefold().replace("ё", "е")
    categories = sorted(k for k, words in CATEGORIES.items() if any(w in t for w in words))
    return categories, bool(categories and any(word in t for word in SERVICE_ACTIONS))


def _read(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file() or not 0 < p.stat().st_size <= 256_000:
        raise ValueError("B2B_INPUT_NOT_FOUND_OR_SIZE_INVALID")
    doc = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or set(doc) != {"mode", "items"} or doc["mode"] != MODE:
        raise ValueError("B2B_INPUT_ENVELOPE_INVALID")
    if not isinstance(doc["items"], list) or not 1 <= len(doc["items"]) <= 100:
        raise ValueError("B2B_INPUT_ITEMS_INVALID")
    return doc["items"]


def review(source_file: str | Path, *, today: date | None = None,
           fns_file: str | Path | None = None) -> dict:
    now = today or date.today()
    fns, fns_snapshot = {}, None
    if fns_file is not None:
        from .sme_vacancy_audit import _read_fns_candidates
        fns, fns_snapshot = _read_fns_candidates(fns_file, now)
    records, seen = [], set()
    for raw in _read(source_file):
        if not isinstance(raw, dict) or set(raw) != REQUIRED:
            raise ValueError("B2B_RECORD_SCHEMA_INVALID")
        pid = _text(raw["procurement_id"], "procurement_id", 5, 24)
        if not re.fullmatch(r"[0-9]{5,24}", pid):
            raise ValueError("B2B_PROCUREMENT_ID_INVALID")
        url = _url(raw["source_url"])
        if pid in seen or url in seen:
            raise ValueError("B2B_DUPLICATE_NOTICE")
        seen.update((pid, url))
        if raw["source_origin"] not in CHANNELS:
            raise ValueError("B2B_SOURCE_ACCESS_NOT_APPROVED")
        klass = _text(raw["procurement_class"], "procurement_class", 5, 50)
        if klass not in {"COMMERCIAL_B2B_CENTER", "THIRD_PARTY_44FZ", "THIRD_PARTY_223FZ", "OTHER"}:
            raise ValueError("B2B_PROCUREMENT_CLASS_INVALID")
        inn = raw["buyer_inn"]
        if inn is not None and (not isinstance(inn, str) or len(inn) != 10 or not is_valid_inn(inn)):
            raise ValueError("B2B_BUYER_INN_INVALID")
        buyer = _text(raw["buyer_name"], "buyer_name", 3, 250)
        role = raw["buyer_inn_role"]
        if role not in {"BUYER_CONFIRMED_FROM_PROCEDURE", "ORGANIZER_ONLY", "UNVERIFIED"}:
            raise ValueError("B2B_BUYER_INN_ROLE_INVALID")
        if inn is None and role == "BUYER_CONFIRMED_FROM_PROCEDURE":
            raise ValueError("B2B_BUYER_INN_REQUIRED_FOR_CONFIRMED_ROLE")
        region = raw["buyer_region"]
        if region is not None and (not isinstance(region, str) or len(region) > 100):
            raise ValueError("B2B_BUYER_REGION_INVALID")
        title = _text(raw["title"], "title", 12, 500)
        excerpt = _text(raw["description_excerpt"], "description_excerpt", 20, 700)
        published = _day(raw["published_on"], "published_on")
        deadline = _day(raw["deadline_on"], "deadline_on") if raw["deadline_on"] is not None else None
        if deadline and deadline < published:
            raise ValueError("B2B_DEADLINE_BEFORE_PUBLICATION")
        categories, direct = classify(title, excerpt)
        holds = ["ORIGINAL_NOTICE_LIVE_VERIFICATION_PENDING", "LEGAL_ACCESS_AND_CONTACT_PENDING",
                 "CRM_DUPLICATES_AND_STOP_LIST_PENDING", "MANAGER_REVIEW_PENDING"]
        if klass != "COMMERCIAL_B2B_CENTER":
            holds.append("NON_COMMERCIAL_OR_EXTERNAL_AGGREGATED_NOTICE")
        if not 0 <= (now - published).days <= 30:
            holds.append("PUBLICATION_NOT_RECENT")
        if deadline is None:
            holds.append("DEADLINE_UNKNOWN")
        elif deadline < now:
            holds.append("DEADLINE_PASSED")
        if not direct:
            holds.append("OUTSIDE_TARGET_EXTERNAL_SERVICE")
        if not inn or role != "BUYER_CONFIRMED_FROM_PROCEDURE":
            holds.append("BUYER_IDENTITY_UNCONFIRMED")
        if region not in ALLOWED_REGIONS:
            holds.append("REGION_OUTSIDE_OR_UNVERIFIED")
        if inn not in fns:
            holds.append("FNS_SME_NOT_VERIFIED")
        elif region is not None and fns[inn]["region"] != region:
            holds.append("FNS_BUYER_REGION_CONFLICT")
        reject = {"NON_COMMERCIAL_OR_EXTERNAL_AGGREGATED_NOTICE", "PUBLICATION_NOT_RECENT",
                  "DEADLINE_UNKNOWN", "DEADLINE_PASSED", "OUTSIDE_TARGET_EXTERNAL_SERVICE",
                  "BUYER_IDENTITY_UNCONFIRMED", "REGION_OUTSIDE_OR_UNVERIFIED",
                  "FNS_SME_NOT_VERIFIED", "FNS_BUYER_REGION_CONFLICT"}
        status = "FNS_MATCHED_RESEARCH_CANDIDATE" if not reject.intersection(holds) else "HOLD_RESEARCH_ONLY"
        records.append({
            "procurement_id": pid, "source_url": url, "source_origin": raw["source_origin"],
            "source_verified_live": False, "buyer_name": buyer, "buyer_inn": inn,
            "buyer_inn_role": role, "buyer_region": region, "procurement_class": klass,
            "title": title, "description_excerpt": excerpt,
            "published_on": published.isoformat(), "deadline_on": deadline.isoformat() if deadline else None,
            "service_categories": categories, "explicit_service_request": direct,
            "fns_snapshot": fns_snapshot if inn in fns else None,
            "fns_category": fns[inn]["fns_category"] if inn in fns else None,
            "identity_verified_live": False, "score": None, "status": status, "holds": holds,
            "crm_ready": False, "outbound_authorized": False,
            "stable_signal_id": hashlib.sha256(
                f"b2b-center|{pid}|{inn or '-'}".encode()).hexdigest()[:24],
        })
    matched = sum(r["status"] == "FNS_MATCHED_RESEARCH_CANDIDATE" for r in records)
    return {
        "mode": "B2B_CENTER_PROCUREMENT_EVIDENCE_AUDIT", "run_on": now.isoformat(),
        "origin": MODE, "source": "b2b-center-direct-commercial",
        "counts": {"input_records": len(records), "fns_matched_research": matched,
                   "held": len(records) - matched, "qualified_real_leads": 0, "crm_ready": 0},
        "records": records, "source_live_api_connected": False,
        "api_documentation": "AVAILABLE_TO_AUTHORIZED_PARTICIPANT_ONLY",
        "crm_writes": "NO", "outbound_messages": "NO",
        "warnings": ["Authorized-normalized input; NOT a direct live B2B-Center API fetch.",
                     "B2B-Center search may aggregate public 44-FZ/223-FZ notices.",
                     "Even FNS-matched research is not a manager-approved qualified lead."],
    }


def _safe(value: object) -> str:
    s = str(value or "").replace("\x00", "")
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else s


def save(report: dict, json_path: str | Path, csv_path: str | Path) -> None:
    root = Path("exports").resolve()
    paths = Path(json_path).resolve(), Path(csv_path).resolve()
    if (any(not p.is_relative_to(root) or p.exists() for p in paths)
            or paths[0].suffix != ".json" or paths[1].suffix != ".csv"):
        raise ValueError("B2B_OUTPUT_MUST_BE_NEW_FILES_UNDER_EXPORTS")
    root.mkdir(exist_ok=True, parents=True)
    with paths[0].open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    fields = ["procurement_id", "buyer_name", "buyer_inn", "buyer_region", "title",
              "source_url", "deadline_on", "service_categories", "status", "holds", "crm_ready"]
    with paths[1].open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in report["records"]:
            writer.writerow({key: _safe("; ".join(row[key]) if isinstance(row[key], list)
                                     else row[key]) for key in fields})


def main() -> None:
    p = argparse.ArgumentParser(description="Approved B2B-Center export -> offline buyer gate")
    p.add_argument("--input", required=True, help="Normalized authorized API/export JSON")
    p.add_argument("--fns", help="Existing official FNS size-only verified JSON")
    p.add_argument("--output", default="exports/b2b_center_research.json")
    p.add_argument("--csv", default="exports/b2b_center_research.csv")
    args = p.parse_args()
    result = review(args.input, fns_file=args.fns)
    save(result, args.output, args.csv)
    print("B2B_CENTER_AUDIT=" + json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))
    print("B2B_SOURCE_LIVE_API=NOT_CONNECTED; CRM_WRITES=NO; OUTBOUND=NO")


if __name__ == "__main__":
    main()
