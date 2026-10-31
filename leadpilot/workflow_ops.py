"""Local MVP operations: durable manager review and simulated CRM handoff.

This module is deliberately disconnected from real Bitrix24, phones, bots,
email and sources. It provides a trustworthy, repeatable non-source acceptance
layer without claiming company approvals or permissions.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3

from .core import initialize as initialize_core, transaction

DECISIONS = frozenset({"relevant", "irrelevant", "needs_research"})
REASONS_MAX = 500
HOLD_STOPS = frozenset({"all", "new_customer"})
MODE = "SIMULATION_ONLY_NO_EXTERNAL_CRM"
SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_reviews (
 company_id INTEGER NOT NULL REFERENCES companies(id),
 signal_id INTEGER NOT NULL REFERENCES signals(id),
 decision TEXT NOT NULL CHECK(decision IN ('relevant','irrelevant','needs_research')),
 reviewer_role TEXT NOT NULL CHECK(reviewer_role IN ('manager','supervisor')),
 reason TEXT NOT NULL,
 reviewed_on TEXT NOT NULL,
 PRIMARY KEY(company_id,signal_id)
);
CREATE TABLE IF NOT EXISTS workflow_outbox (
 company_id INTEGER NOT NULL REFERENCES companies(id),
 signal_id INTEGER NOT NULL REFERENCES signals(id),
 status TEXT NOT NULL CHECK(status IN ('QUEUED','RETRY','SIMULATED')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 last_error_code TEXT,
 external_id TEXT,
 PRIMARY KEY(company_id,signal_id)
);
CREATE TABLE IF NOT EXISTS workflow_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER REFERENCES companies(id),
 signal_id INTEGER REFERENCES signals(id),
 event_type TEXT NOT NULL,
 event_json TEXT NOT NULL,
 created_on TEXT NOT NULL
);
"""

def initialize(db_path: str | Path) -> None:
    """Explicit local-only setup; does not alter existing company schemas."""
    initialize_core(db_path)
    with transaction(db_path) as con:
        con.executescript(SCHEMA)


def _today(value: date | None) -> date:
    return value or date.today()


def _audit(con, company_id: int, signal_id: int | None,
           event: str, facts: dict, now: date) -> None:
    con.execute(
        "INSERT INTO workflow_audit(company_id,signal_id,event_type,event_json,created_on) "
        "VALUES(?,?,?,?,?)",
        (company_id, signal_id, event, json.dumps(facts, sort_keys=True), now.isoformat()),
    )


def _verify_company_signal(con, company_id: int, signal_id: int):
    if type(company_id) is not int or type(signal_id) is not int:
        raise ValueError("Numeric company and signal IDs required")
    company = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    signal = con.execute(
        "SELECT * FROM signals WHERE id=? AND company_id=?", (signal_id, company_id)
    ).fetchone()
    if company is None or signal is None:
        raise ValueError("Company and associated signal not found")
    return company, signal


def review(db_path: str | Path, company_id: int, signal_id: int, *,
           decision: str, reviewer_role: str, reason: str,
           today: date | None = None) -> dict:
    """Record non-binding human feedback; NEVER an authorization to contact."""
    if decision not in DECISIONS or reviewer_role not in {"manager", "supervisor"}:
        raise ValueError("Invalid manager decision or role")
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= REASONS_MAX:
        raise ValueError("Detailed reason required")
    now = _today(today)
    with transaction(db_path) as con:
        _verify_company_signal(con, company_id, signal_id)
        previous = con.execute(
            "SELECT decision,reason FROM workflow_reviews WHERE company_id=? AND signal_id=?",
            (company_id, signal_id),
        ).fetchone()
        if previous is not None:
            if previous["decision"] != decision or previous["reason"] != reason.strip():
                # Immutable audit trail: changing a previously reviewed decision
                # requires a new explicit correction workflow outside this demo.
                raise ValueError("Review immutable; correction needs supervisor audit")
            return {"status": "ALREADY_RECORDED", "crm_writes": "NO"}
        con.execute(
            "INSERT INTO workflow_reviews "
            "(company_id,signal_id,decision,reviewer_role,reason,reviewed_on) "
            "VALUES(?,?,?,?,?,?)",
            (company_id, signal_id, decision, reviewer_role, reason.strip(), now.isoformat()),
        )
        _audit(con, company_id, signal_id, "manager_review",
               {"decision": decision, "role": reviewer_role}, now)
        return {"status": "RECORDED_LOCAL_ONLY", "crm_writes": "NO"}


def stage_simulation(db_path: str | Path, company_id: int, signal_id: int,
                     *, today: date | None = None) -> dict:
    """Stage a local simulation only after an unchanged review and safe gates."""
    now = _today(today)
    with transaction(db_path) as con:
        company, signal = _verify_company_signal(con, company_id, signal_id)
        decision = con.execute(
            "SELECT decision FROM workflow_reviews WHERE company_id=? AND signal_id=?",
            (company_id, signal_id),
        ).fetchone()
        stops = {
            r["channel"] for r in con.execute(
                "SELECT channel FROM suppressions WHERE company_id=?", (company_id,)
            )
        }
        try:
            age = (now - date.fromisoformat(signal["signal_date"])).days
        except ValueError:
            raise ValueError("Invalid source signal date") from None
        if (decision is None or decision["decision"] != "relevant"
                or bool(company["existing_customer"]) or bool(company["manual_review"])
                or stops & HOLD_STOPS or not 0 <= age <= 30):
            _audit(con, company_id, signal_id, "handoff_blocked",
                   {"review": decision["decision"] if decision else "missing",
                    "existing_customer": bool(company["existing_customer"]),
                    "manual_review": bool(company["manual_review"]),
                    "stops": sorted(stops), "freshness_days": age}, now)
            return {"status": "HOLD", "reason": "REVIEW_OR_SAFETY_GATE", "crm_writes": "NO"}
        con.execute(
            "INSERT OR IGNORE INTO workflow_outbox(company_id,signal_id,status) "
            "VALUES(?,?,'QUEUED')", (company_id, signal_id)
        )
        _audit(con, company_id, signal_id, "simulated_handoff_staged",
               {"mode": MODE}, now)
        return {"status": "QUEUED_SIMULATION_ONLY", "crm_writes": "NO"}


def deliver_simulation(db_path: str | Path, *, service_available: bool,
                       today: date | None = None) -> dict:
    """Deterministically replay simulated operations. Never calls a real API."""
    if type(service_available) is not bool:
        raise ValueError("Explicit simulated service availability required")
    now = _today(today)
    complete = retry = held = 0
    with transaction(db_path) as con:
        entries = con.execute(
            "SELECT company_id,signal_id FROM workflow_outbox "
            "WHERE status IN ('QUEUED','RETRY') ORDER BY company_id,signal_id"
        ).fetchall()
        for entry in entries:
            cid, sid = entry["company_id"], entry["signal_id"]
            company, signal = _verify_company_signal(con, cid, sid)
            reviewed = con.execute(
                "SELECT decision FROM workflow_reviews WHERE company_id=? AND signal_id=?",
                (cid, sid)
            ).fetchone()
            stops = {r["channel"] for r in con.execute(
                "SELECT channel FROM suppressions WHERE company_id=?", (cid,))}
            age = (now - date.fromisoformat(signal["signal_date"])).days
            if (reviewed is None or reviewed["decision"] != "relevant"
                    or company["existing_customer"] or company["manual_review"]
                    or stops & HOLD_STOPS or not 0 <= age <= 30):
                held += 1
                _audit(con, cid, sid, "replay_blocked",
                       {"gate": "safety_or_freshness_changed"}, now)
                # Keep the unsent entry for explicit manual review,
                # rather than fabricating a successful transmission.
                continue
            if not service_available:
                con.execute(
                    "UPDATE workflow_outbox SET status='RETRY',attempts=attempts+1,"
                    "last_error_code='SIMULATED_DOWN' WHERE company_id=? AND signal_id=?",
                    (cid, sid)
                )
                _audit(con, cid, sid, "simulated_retry", {"code": "SIMULATED_DOWN"}, now)
                retry += 1
            else:
                # Deterministic stable reference in SIMULATION namespace only.
                eid = "SIM-LEAD-" + str(cid) + "-SIGNAL-" + str(sid)
                con.execute(
                    "UPDATE workflow_outbox SET status='SIMULATED',attempts=attempts+1,"
                    "last_error_code=NULL,external_id=? "
                    "WHERE company_id=? AND signal_id=?", (eid, cid, sid)
                )
                _audit(con, cid, sid, "simulated_delivered",
                       {"external_id": eid, "mode": MODE}, now)
                complete += 1
    return {"simulated_completed": complete, "simulated_retry": retry,
            "held": held, "crm_writes": "NO", "outbound_messages": "NO",
            "mode": MODE}


def report(db_path: str | Path) -> dict:
    """Aggregated, non-identifying manager + outbox KPIs."""
    with sqlite3.connect(db_path) as con:
        r = dict(con.execute(
            "SELECT decision,COUNT(*) FROM workflow_reviews GROUP BY decision"
        ).fetchall())
        q = dict(con.execute(
            "SELECT status,COUNT(*) FROM workflow_outbox GROUP BY status"
        ).fetchall())
        return {"mode": MODE, "reviews": r, "outbox": q,
                "crm_writes": "NO", "customer_contact_attempts": 0,
                "contracts_verified": 0, "revenue_verified": 0,
                "meaning": "Synthetic/local operations only, no company CRM permissions"}
