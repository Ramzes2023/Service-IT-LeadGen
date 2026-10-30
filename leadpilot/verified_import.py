"""Safely audit/import SAVED official Trudvsem scan JSON files into research DB.

No network, no GigaChat, no Bitrix24 writes. Only public employer-level
signals. Validation is intentionally stricter than leadpilot.sources v0.3.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlparse
import sqlite3

from leadpilot.core import ingest, initialize, qualify

REGIONS = {"77": "Москва", "50": "Московская область", "06": "Республика Ингушетия"}
SRC = "trudvsem-open-vacancies"
TYPES = {"1c_vacancy", "it_vacancy"}


def is_valid_inn(raw: object) -> bool:
    if not isinstance(raw, str) or len(raw) not in (10, 12) or not raw.isascii() or not raw.isdigit():
        return False
    if len(set(raw)) == 1:  # obvious placeholders such as 0000000000
        return False
    d = [int(x) for x in raw]
    if len(d) == 10:
        return sum(a * b for a, b in zip(d[:9], (2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10 == d[9]
    return (sum(a * b for a, b in zip(d[:10], (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10 == d[10]
            and sum(a * b for a, b in zip(d[:11], (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10 == d[11])


def eligible(raw: object, expected_region: str, today: date) -> tuple[dict | None, str]:
    if not isinstance(raw, dict):
        return None, "malformed_record"
    required = ("source_name", "source_url", "name", "region", "signal_type", "evidence", "signal_date", "inn")
    if any(not isinstance(raw.get(k), str) for k in required):
        return None, "missing_fields"
    if raw["source_name"] != SRC or raw["region"] != REGIONS[expected_region]:
        return None, "wrong_source_or_region"
    u = urlparse(raw["source_url"])
    if u.scheme not in ("http", "https") or not u.hostname or not (u.hostname == "trudvsem.ru" or u.hostname.endswith(".trudvsem.ru")):
        return None, "untrusted_link"
    if u.username or u.password or not u.path.strip("/"):
        return None, "invalid_link"
    if raw["signal_type"] not in TYPES:
        return None, "signal_type"
    name = raw["name"].strip()
    ev = raw["evidence"].strip()
    if not (3 <= len(name) <= 250) or not ev.startswith("Опубликована вакансия: ") or not (24 <= len(ev) <= 350):
        return None, "invalid_evidence"
    try:
        when = date.fromisoformat(raw["signal_date"])
    except ValueError:
        return None, "invalid_date"
    days = (today - when).days
    if days < 0 or days > 90:
        return None, "not_fresh"
    if not is_valid_inn(raw["inn"]):
        return None, "invalid_or_missing_inn"
    # Explicit allowlist: reject contact fields and untrusted JSON extras.
    item = {"source_name": SRC, "source_url": raw["source_url"],
            "name": name, "inn": raw["inn"], "region": raw["region"],
            "industry": "", "phone": "", "email": "", "domain": "",
            "signal_type": raw["signal_type"], "evidence": ev,
            "signal_date": raw["signal_date"], "existing_customer": False}
    return item, "ok"


def read_snapshots(folder: Path, today: date | None = None) -> tuple[list[dict], dict]:
    today = today or date.today()
    found = Counter()
    dedupe = set()
    accepted = []
    for region in ("77", "50"):
        path = folder / f"trudvsem_{region}.json"
        if not path.is_file():
            found[f"missing_file_{region}"] += 1
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            found[f"corrupt_file_{region}"] += 1
            continue
        if not isinstance(data, dict) or data.get("source") != SRC or data.get("region") != REGIONS[region] or not isinstance(data.get("signals"), list):
            found[f"bad_schema_{region}"] += 1
            continue
        for candidate in data["signals"]:
            found["raw_records"] += 1
            item, reason = eligible(candidate, region, today)
            if item is None:
                found[reason] += 1
                continue
            key = (item["inn"], item["source_url"], item["signal_type"])
            if key in dedupe:
                found["duplicate_in_snapshot"] += 1
                continue
            dedupe.add(key)
            accepted.append(item)
            found["validated_records"] += 1
    found["unique_companies"] = len({i["inn"] for i in accepted})
    return accepted, dict(found)


def run(folder: Path, db: Path | None, do_import: bool, today: date | None = None) -> dict:
    items, stats = read_snapshots(folder, today)
    result = {"mode": "LOCAL_IMPORT" if do_import else "AUDIT_ONLY", "audit": stats,
              "crm_writes": "NO", "valyqon_changes": "NONE", "external_requests": "NO"}
    if do_import:
        if not items:
            raise ValueError("No validated public records to import; database unchanged")
        if db is None:
            raise ValueError("Research database path required")
        initialize(db)
        added_signals = added_companies = 0
        for item in items:
            res = ingest(db, item)
            added_signals += int(res["new_signal"])
            added_companies += int(res["new_company"])
        qualify(db, today=today)
        with sqlite3.connect(db) as con:
            total = con.execute("SELECT COUNT(*) FROM signals WHERE source_name=?", (SRC,)).fetchone()[0]
            con.execute("""INSERT INTO source_runs(source_name,status,fetched,accepted,staged,note)
                VALUES(?,?,?,?,?,?)""", (SRC, "validated_saved_snapshot", stats.get("raw_records", 0),
                len(items), stats.get("invalid_or_missing_inn", 0),
                "Research DB only; checksum + URL + freshness checked; no CRM writes"))
        result["import"] = {"new_companies": added_companies, "new_signals": added_signals,
                            "total_public_signals_in_research_db": total, "crm_writes": "NO"}
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--snapshots", default="exports")
    p.add_argument("--db", default="data/research.sqlite3")
    p.add_argument("--import-to-research", action="store_true")
    args = p.parse_args()
    try:
        res = run(Path(args.snapshots), Path(args.db), args.import_to_research)
    except ValueError as exc:
        print("VERIFIED_IMPORT_FAILED=" + str(exc))
        raise SystemExit(2)
    print("RESEARCH_AUDIT=" + json.dumps(res, ensure_ascii=False, sort_keys=True))
    print("RESEARCH_STEP_DONE=YES; CRM_WRITES=NO")

if __name__ == "__main__":
    main()
