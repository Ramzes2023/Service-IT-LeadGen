"""Offline-first lead qualification. No unsolicited messages, no automatic CRM writes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
import json
import re
import sqlite3

SCORE_VERSION = "mvp-2026-10-v1"
SERVICES = {
    "1c_vacancy": "Сопровождение и внедрение 1С",
    "new_store": "1С + ККТ + торговое оборудование",
    "it_vacancy": "IT-аутсорсинг",
    "procurement": "IT-сопровождение / тендерное предложение",
    "crm_need": "Битрикс24 / CRM-интеграции",
    "unknown": "Требуется ручная квалификация",
}
SIGNAL_POINTS = {"1c_vacancy": 25, "new_store": 25, "it_vacancy": 20, "procurement": 20, "crm_need": 20, "unknown": 0}


def normalize_inn(value: str) -> str:
    inn = re.sub(r"\D", "", value or "")
    if len(inn) not in (10, 12):
        raise ValueError("ИНН должен содержать 10 или 12 цифр (в демо контрольная сумма не проверяется)")
    return inn


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    return "+" + digits if digits else ""


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def normalize_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].removeprefix("www.")
    return domain


def region_points(region: str) -> int:
    s = (region or "").strip().lower()
    if any(x in s for x in ("москва", "московская область", "московская обл")):
        return 20
    if "ингуш" in s or "назран" in s or "магас" in s:
        return 10
    return 0


def iso_date(value: str | None) -> str:
    result = (value or date.today().isoformat()).strip()
    date.fromisoformat(result)
    return result

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS companies (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 inn TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL,
 region TEXT NOT NULL,
 industry TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '',
 email TEXT NOT NULL DEFAULT '',
 domain TEXT NOT NULL DEFAULT '',
 existing_customer INTEGER NOT NULL DEFAULT 0,
 manual_review INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_companies_phone ON companies(phone);
CREATE INDEX IF NOT EXISTS idx_companies_email ON companies(email);
CREATE TABLE IF NOT EXISTS signals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL REFERENCES companies(id),
 source_name TEXT NOT NULL,
 source_url TEXT NOT NULL,
 signal_type TEXT NOT NULL,
 evidence TEXT NOT NULL,
 signal_date TEXT NOT NULL,
 discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id, source_url, signal_type)
);
CREATE TABLE IF NOT EXISTS suppressions (
 company_id INTEGER NOT NULL REFERENCES companies(id),
 channel TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(company_id, channel)
);
CREATE TABLE IF NOT EXISTS qualifications (
 company_id INTEGER NOT NULL UNIQUE REFERENCES companies(id),
 score INTEGER NOT NULL,
 score_version TEXT NOT NULL,
 priority TEXT NOT NULL,
 recommended_service TEXT NOT NULL,
 explanation TEXT NOT NULL,
 ai_status TEXT NOT NULL DEFAULT 'not_configured',
 ai_suggestion TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER REFERENCES companies(id),
 event_type TEXT NOT NULL,
 payload TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_name TEXT NOT NULL,
 source_url TEXT NOT NULL,
 employer_name TEXT NOT NULL,
 inn TEXT NOT NULL DEFAULT '',
 region TEXT NOT NULL,
 signal_type TEXT NOT NULL,
 evidence TEXT NOT NULL,
 signal_date TEXT NOT NULL,
 reason TEXT NOT NULL,
 discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(source_name, source_url)
);
CREATE TABLE IF NOT EXISTS source_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_name TEXT NOT NULL,
 status TEXT NOT NULL,
 fetched INTEGER NOT NULL,
 accepted INTEGER NOT NULL,
 staged INTEGER NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS crm_links (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id),
 external_lead_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def initialize(db_path: str | Path):
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as con:
        con.executescript(SCHEMA)


@contextmanager
def transaction(db_path):
    con = sqlite3.connect(db_path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def audit(con, company_id, event_type, payload):
    con.execute("INSERT INTO audit_events(company_id,event_type,payload) VALUES (?,?,?)",
                (company_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def ingest(db_path, item: dict) -> dict:
    """Upsert by INN; never merge companies on phone, email, or domain."""
    inn = normalize_inn(str(item.get("inn", "")))
    name = (item.get("name") or "").strip()
    region = (item.get("region") or "").strip()
    if not name or not region:
        raise ValueError("Необходимо указать название и регион")
    phone = normalize_phone(item.get("phone", ""))
    email = normalize_email(item.get("email", ""))
    domain = normalize_domain(item.get("domain", ""))
    source_url = (item.get("source_url") or "").strip()
    evidence = (item.get("evidence") or "").strip()
    source_name = (item.get("source_name") or "").strip()
    signal_type = (item.get("signal_type") or "unknown").strip()
    if signal_type not in SERVICES:
        raise ValueError(f"Недопустимый тип сигнала: {signal_type}")
    if not source_url or not evidence or not source_name:
        raise ValueError("У сигнала нужны источник, URL и текст доказательства")
    if not source_url.startswith(("https://", "http://")):
        raise ValueError("URL источника должен начинаться с https:// или http://")
    event_date = iso_date(item.get("signal_date"))
    existing = bool(item.get("existing_customer", False))
    with transaction(db_path) as con:
        found = con.execute("SELECT * FROM companies WHERE inn=?", (inn,)).fetchone()
        if found:
            company_id = found["id"]
            # Never overwrite established identity. Fill missing contact metadata only.
            con.execute("""UPDATE companies SET
                phone=CASE WHEN phone='' THEN ? ELSE phone END,
                email=CASE WHEN email='' THEN ? ELSE email END,
                domain=CASE WHEN domain='' THEN ? ELSE domain END,
                existing_customer=MAX(existing_customer,?)
                WHERE id=?""", (phone, email, domain, int(existing), company_id))
            was_new = False
        else:
            collisions = con.execute("""SELECT DISTINCT inn FROM companies
                WHERE (phone!='' AND phone=?) OR (email!='' AND email=?)""", (phone, email)).fetchall()
            manual_review = bool(collisions)
            cur = con.execute("""INSERT INTO companies(inn,name,region,industry,phone,email,domain,existing_customer,manual_review)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
                (inn, name, region, (item.get("industry") or "").strip(), phone, email, domain,
                 int(existing), int(manual_review)))
            company_id = cur.lastrowid
            was_new = True
            if collisions:
                other_inns = [r["inn"] for r in collisions]
                # Quarantine both sides until the ambiguous shared contact is reviewed.
                con.execute("UPDATE companies SET manual_review=1 WHERE inn IN (" +
                            ",".join("?" for _ in other_inns) + ")", other_inns)
                audit(con, company_id, "potential_duplicate_manual_review",
                      {"other_inns": other_inns, "reason": "shared phone/email"})
                for other_inn in other_inns:
                    other_row = con.execute("SELECT id FROM companies WHERE inn=?", (other_inn,)).fetchone()
                    audit(con, other_row["id"], "potential_duplicate_manual_review",
                          {"other_inn": inn, "reason": "shared phone/email"})
        cursor = con.execute("""INSERT OR IGNORE INTO signals
            (company_id,source_name,source_url,signal_type,evidence,signal_date)
            VALUES(?,?,?,?,?,?)""", (company_id, source_name, source_url, signal_type, evidence, event_date))
        audit(con, company_id, "ingest", {"new_company": was_new, "new_signal": cursor.rowcount == 1,
                                          "source_url": source_url, "signal_type": signal_type})
    return {"inn": inn, "company_id": company_id, "new_company": was_new,
            "new_signal": cursor.rowcount == 1}


def suppress(db_path, inn, channel, reason):
    if channel not in ("all", "phone", "email", "messenger", "new_customer"):
        raise ValueError("Недопустимый канал")
    with transaction(db_path) as con:
        company = con.execute("SELECT id FROM companies WHERE inn=?", (normalize_inn(inn),)).fetchone()
        if not company:
            raise ValueError("Компания не найдена")
        con.execute("INSERT OR REPLACE INTO suppressions(company_id,channel,reason) VALUES(?,?,?)",
                    (company["id"], channel, reason))
        audit(con, company["id"], "suppressed", {"channel": channel, "reason": reason})


def score_company(company: dict, signals: list[dict], today: date | None = None):
    now = today or date.today()
    latest = None
    recent = []
    for s in signals:
        age = (now - date.fromisoformat(s["signal_date"])).days
        if age < 0:
            # Future-dated evidence cannot be scored as fresh.
            continue
        if age <= 90:
            recent.append((s, age))
    if recent:
        recent.sort(key=lambda x: (SIGNAL_POINTS[x[0]["signal_type"]], -x[1]), reverse=True)
        latest, age = recent[0]
    else:
        age = None
    rp = region_points(company["region"])
    segment = 15 if any(x in (company["industry"] or "").lower() for x in
                        ("торгов", "магаз", "медицин", "образован", "бухгалтер", "услуг", "офис", "производ")) else 0
    signal = SIGNAL_POINTS[latest["signal_type"]] if latest else 0
    fresh = (15 if age <= 14 else 10 if age <= 45 else 5) if age is not None else 0
    service = 15 if latest and latest["signal_type"] != "unknown" else 0
    contact = 10 if company["phone"] or company["email"] else 0
    value = min(100, rp + segment + signal + fresh + service + contact)
    explanation = {"region": rp, "segment": segment, "signal": signal,
                   "freshness": fresh, "service_fit": service, "contact": contact,
                   "signal_id": latest["id"] if latest else None,
                   "days_since_signal": age,
                   "rules": SCORE_VERSION}
    return value, SERVICES[latest["signal_type"]] if latest else SERVICES["unknown"], explanation


def qualify(db_path, today=None):
    now = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
    with transaction(db_path) as con:
        companies = con.execute("SELECT * FROM companies ORDER BY id").fetchall()
        result = []
        for row in companies:
            c = dict(row)
            signs = [dict(s) for s in con.execute("SELECT * FROM signals WHERE company_id=?", (c["id"],))]
            stops = {s["channel"] for s in con.execute(
                "SELECT channel FROM suppressions WHERE company_id=?", (c["id"],))}
            value, service, explanation = score_company(c, signs, now)
            if c["existing_customer"]:
                status = "existing_customer"
            elif c["manual_review"]:
                status = "manual_review"
            elif "all" in stops or "new_customer" in stops:
                status = "suppressed"
            elif not explanation["signal_id"]:
                status = "expired_or_missing_signal"
            elif value >= 80:
                status = "high"
            elif value >= 60:
                status = "medium"
            elif value >= 40:
                status = "nurture"
            else:
                status = "low"
            old_q = con.execute("SELECT explanation FROM qualifications WHERE company_id=?", (c["id"],)).fetchone()
            changed_signal = (old_q is not None and json.loads(old_q["explanation"]).get("signal_id") != explanation["signal_id"])
            con.execute("""INSERT INTO qualifications(company_id,score,score_version,priority,recommended_service,explanation)
              VALUES(?,?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET
              score=excluded.score,score_version=excluded.score_version,priority=excluded.priority,
              recommended_service=excluded.recommended_service,explanation=excluded.explanation,
              updated_at=CURRENT_TIMESTAMP""",
              (c["id"], value, SCORE_VERSION, status, service, json.dumps(explanation, ensure_ascii=False)))
            if changed_signal:
                con.execute("UPDATE qualifications SET ai_status='not_configured',ai_suggestion='' WHERE company_id=?", (c["id"],))
            audit(con, c["id"], "qualified", {"score": value, "status": status, "version": SCORE_VERSION})
            result.append({"inn": c["inn"], "score": value, "priority": status})
    return result


def get_leads(db_path, only_queue=False):
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT c.*,q.score,q.priority,q.score_version,q.recommended_service,
          q.explanation,q.ai_status,q.ai_suggestion
          FROM companies c JOIN qualifications q ON q.company_id=c.id
          ORDER BY q.score DESC, c.id ASC""").fetchall()
        leads = []
        for r in rows:
            lead = dict(r)
            lead["explanation"] = json.loads(lead["explanation"])
            lead["signals"] = [dict(s) for s in con.execute(
                "SELECT * FROM signals WHERE company_id=? ORDER BY signal_date DESC", (lead["id"],))]
            lead["suppressed_channels"] = [s["channel"] for s in con.execute(
                "SELECT channel FROM suppressions WHERE company_id=?", (lead["id"],))]
            if not only_queue or lead["priority"] in ("high", "medium"):
                leads.append(lead)
        for lead in leads:
            if lead.get("ai_suggestion"):
                try:
                    lead["ai_suggestion"] = json.loads(lead["ai_suggestion"])
                except (TypeError, json.JSONDecodeError):
                    lead["ai_suggestion"] = {}
            else:
                lead["ai_suggestion"] = {}
    return leads


def save_ai_analysis(db_path, company_id: int, signal_id: int, result: dict):
    """Store only an AI hypothesis for the currently scored signal.

    The score never changes; possible stale AI results are rejected.
    """
    with transaction(db_path) as con:
        row = con.execute("SELECT explanation FROM qualifications WHERE company_id=?", (company_id,)).fetchone()
        if not row or json.loads(row["explanation"]).get("signal_id") != signal_id:
            raise ValueError("Qualification missing or active signal changed; requalify")
        con.execute("UPDATE qualifications SET ai_status=?, ai_suggestion=?, updated_at=CURRENT_TIMESTAMP WHERE company_id=?",
                    ("review_required", json.dumps(result, ensure_ascii=False), company_id))
        audit(con, company_id, "ai_hypothesis", {"signal_id": signal_id,
              "provider": result.get("provider"), "model": result.get("model"),
              "confidence": result.get("confidence"), "no_score_change": True})


def metrics(db_path):
    leads = get_leads(db_path)
    counts = {}
    for lead in leads:
        counts[lead["priority"]] = counts.get(lead["priority"], 0) + 1
    with sqlite3.connect(db_path) as con:
        staged_count = con.execute("SELECT COUNT(*) FROM source_candidates").fetchone()[0]
        real_signal_count = con.execute("SELECT COUNT(*) FROM signals WHERE source_name='trudvsem-open-vacancies'").fetchone()[0]
    return {"staged_needs_inn": staged_count, "real_signal_count": real_signal_count,
            "companies": len(leads), "manager_queue": counts.get("high", 0) + counts.get("medium", 0),
            "high": counts.get("high", 0), "medium": counts.get("medium", 0),
            "suppressed": counts.get("suppressed", 0), "manual_review": counts.get("manual_review", 0),
            "by_status": counts, "mode": "DEMO_ONLY", "real_crm_connected": False,
            "source_collection": "Synthetic fixtures plus bounded official API when imported"}
