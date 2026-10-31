"""Opt-in, low-volume read-only adapters for official public signals.

The first adapter targets the documented Trudvsem open vacancy API. Its live
availability/terms must be checked in the deployment environment. No contact
harvesting, outreach, web crawling or CRM writes are performed here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import urlparse
import sqlite3
import httpx

from .core import ingest, initialize, qualify

API_BASE = "https://opendata.trudvsem.ru/api/v1/vacancies/region"
REGIONS = {"77": "Москва", "50": "Московская область", "06": "Республика Ингушетия"}
KEYWORDS = ("1С", "системный администратор", "IT-инженер")


def _date(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip().replace("Z", "+00:00")
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return ""


def _source_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    p = urlparse(value.strip())
    # URL stays the actual published URL, not an invented vacancy link.
    # Some older official records use http:// links; we record but never fetch them.
    if p.scheme not in ("https", "http") or not p.hostname:
        return ""
    host = p.hostname.lower()
    if host != "trudvsem.ru" and not host.endswith(".trudvsem.ru"):
        return ""
    return value.strip()


def _signal_type(title: str) -> str | None:
    t = title.lower().replace("ё", "е")
    if "1с" in t or "1c" in t:
        return "1c_vacancy"
    if any(s in t for s in ("системн", "сисадмин", "it-инженер", "сетевой инженер")):
        return "it_vacancy"
    return None


def parse_vacancy(item: dict, region_code: str) -> dict | None:
    """Only company-level public facts; never import applicant/HR contacts."""
    v = item.get("vacancy") if isinstance(item, dict) else None
    if not isinstance(v, dict):
        return None
    org = v.get("company", {})
    if not isinstance(org, dict):
        return None
    title = str(v.get("job-name") or "").strip()[:160]
    signal = _signal_type(title)
    name = str(org.get("name") or "").strip()[:250]
    published = _date(v.get("creation-date") or v.get("date_modify"))
    url = _source_url(v.get("vac_url"))
    if not (name and signal and published and url):
        return None
    inn = str(org.get("inn") or "").strip()
    if not (inn.isdigit() and len(inn) in (10, 12)):
        inn = ""
    return {
        "source_name": "trudvsem-open-vacancies",
        "source_url": url,
        "name": name,
        "inn": inn,
        "region": REGIONS[region_code],
        "industry": "", # No industry claim from vacancy title.
        "phone": "", "email": "", "domain": "",
        "signal_type": signal,
        "evidence": f"Опубликована вакансия: {title}",
        "signal_date": published,
        "existing_customer": False,
    }


def _parse_response(data: object) -> list:
    if not isinstance(data, dict):
        raise ValueError("Trudvsem response is not JSON object")
    if str(data.get("status", "200")) != "200":
        raise RuntimeError("Trudvsem API reported an error")
    body = data.get("results", {})
    if not isinstance(body, dict) or not isinstance(body.get("vacancies"), list):
        raise ValueError("Trudvsem response schema differs from expected")
    return body["vacancies"]


def scan(*, region="77", term="1С", limit=20, offset=0, client=None) -> dict:
    """One bounded GET; caller decides whether to import public results."""
    if region not in REGIONS:
        raise ValueError("Unsupported region code")
    if term not in KEYWORDS:
        raise ValueError("Unsupported term")
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 50):
        raise ValueError("Maximum 50 items per bounded scan")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 4:
        raise ValueError("Offset must be a page index from 0 to 4")
    # Bounded allowlisted destination; no arbitrary URL input or HTTP downgrade.
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=False)
    try:
        url = f"{API_BASE}/{region}"
        response = client.get(url, params={"text": term, "offset": offset, "limit": limit},
                              headers={"Accept": "application/json", "User-Agent": "ServiceITLeadGenResearch/0.3"})
        if response.status_code != 200:
            raise RuntimeError(f"Trudvsem source unavailable (HTTP {response.status_code}); no data imported")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("API_NON_JSON_RESPONSE") from None
        try:
            records = _parse_response(payload)
        except ValueError:
            raise RuntimeError("API_SCHEMA_MISMATCH") from None
        normalized = [parse_vacancy(r, region) for r in records]
        valid = [r for r in normalized if r is not None]
        return {"source": "trudvsem-open-vacancies", "region": REGIONS[region],
                "term": term, "offset": offset, "fetched": len(records), "valid_signals": len(valid),
                "with_inn": sum(bool(r["inn"]) for r in valid),
                "signals": valid, "mode": "PUBLIC_SIGNAL_READ_ONLY"}
    finally:
        if own:
            client.close()


def import_scan(db_path, result: dict) -> dict:
    """Import only verified-INN companies. Missing-INN candidates stay staged;
    no synthetic identifiers or CRM cards are created.
    """
    initialize(db_path)
    accepted = staged = 0
    for item in result["signals"]:
        if item["inn"]:
            ingest(db_path, item)
            accepted += 1
        else:
            with sqlite3.connect(db_path) as c:
                c.execute("""INSERT OR IGNORE INTO source_candidates
                (source_name,source_url,employer_name,inn,region,signal_type,evidence,signal_date,reason)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                    item["source_name"],item["source_url"],item["name"],"",item["region"],
                    item["signal_type"],item["evidence"],item["signal_date"],"needs_verified_inn"))
            staged += 1
    with sqlite3.connect(db_path) as c:
        c.execute("""INSERT INTO source_runs(source_name,status,fetched,accepted,staged,note)
                     VALUES(?,?,?,?,?,?)""", ("trudvsem-open-vacancies", "parsed",
                    result["fetched"], accepted, staged, "No CRM writes; no outreach"))
    qualify(db_path)
    return {"accepted": accepted, "staged_needs_inn": staged,
            "crm_writes": False, "outreach": False}


def staged_candidates(db_path):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM source_candidates ORDER BY id DESC LIMIT 100")]
