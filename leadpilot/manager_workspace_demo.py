"""Local manager task/feedback journal for a synthetic architecture demo only.

No real CRM task sender or user authentication. Demo roles do not
authorize access to company CRM, calling, email, or messaging.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re
import sqlite3

from .core import transaction
from .workflow_ops import initialize as initialize_workflow, _verify_company_signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_manager_tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL REFERENCES companies(id),
 signal_id INTEGER NOT NULL REFERENCES signals(id),
 assignee_label TEXT NOT NULL,
 due_on TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('OPEN','FOLLOW_UP','CLOSED_SIMULATED')),
 next_action TEXT NOT NULL,
 created_on TEXT NOT NULL,
 updated_on TEXT NOT NULL,
 UNIQUE(company_id,signal_id)
);
CREATE TABLE IF NOT EXISTS demo_manager_task_events(
 event_id TEXT NOT NULL UNIQUE,
 task_id INTEGER NOT NULL REFERENCES demo_manager_tasks(id),
 event_type TEXT NOT NULL,
 note TEXT NOT NULL,
 recorded_on TEXT NOT NULL,
 next_due TEXT,
 payload_json TEXT NOT NULL
);
"""
OUTCOMES = frozenset({"NEEDS_RESEARCH", "NO_INTEREST", "FOLLOW_UP", "INTERESTED"})
SYNTHETIC_SOURCES = frozenset({"synthetic-fixture", "synthetic-second"})


def initialize(db_path: str | Path) -> None:
    initialize_workflow(db_path)
    with transaction(db_path) as con:
        con.executescript(SCHEMA)


def _note(text: str) -> str:
    if not isinstance(text, str) or not 8 <= len(text.strip()) <= 350:
        raise ValueError("Demo journal note length must be 8..350")
    return text.strip()


def assign(db_path: str | Path, company_id: int, signal_id: int, *,
           assignee_label: str, next_action: str, due: date, today: date | None = None) -> dict:
    now = today or date.today()
    if (not isinstance(assignee_label, str)
            or not re.fullmatch(r"DEMO-MANAGER-[0-9]{1,3}", assignee_label)
            or type(due) is not date or due < now or (due - now).days > 30):
        raise ValueError("Demo-only assignee and future due date required")
    next_action = _note(next_action)
    with transaction(db_path) as con:
        company, signal = _verify_company_signal(con, company_id, signal_id)
        if signal["source_name"] not in SYNTHETIC_SOURCES:
            raise ValueError("Only synthetic sources can enter demo tasks")
        review = con.execute(
            "SELECT decision FROM workflow_reviews WHERE company_id=? AND signal_id=?",
            (company_id, signal_id)
        ).fetchone()
        stops = {r["channel"] for r in con.execute(
            "SELECT channel FROM suppressions WHERE company_id=?", (company_id,))}
        age = (now - date.fromisoformat(signal["signal_date"])).days
        if (review is None or review["decision"] != "relevant"
                or bool(company["existing_customer"]) or bool(company["manual_review"])
                or {"all", "new_customer"} & stops or not 0 <= age <= 30):
            return {"status": "HELD_BY_POLICY", "crm_writes": "NO"}
        old = con.execute(
            "SELECT id FROM demo_manager_tasks WHERE company_id=? AND signal_id=?",
            (company_id, signal_id)
        ).fetchone()
        if old:
            return {"status": "DEMO_TASK_ALREADY_EXISTS", "task_id": old["id"],
                    "crm_writes": "NO"}
        row = con.execute(
            "INSERT INTO demo_manager_tasks "
            "(company_id,signal_id,assignee_label,due_on,state,next_action,created_on,updated_on) "
            "VALUES(?,?,?,?,'OPEN',?,?,?)",
            (company_id, signal_id, assignee_label, due.isoformat(),
             next_action, now.isoformat(), now.isoformat())
        )
        return {"status": "DEMO_TASK_CREATED_LOCALLY", "task_id": row.lastrowid,
                "crm_writes": "NO"}


def record_simulated_result(db_path: str | Path, task_id: int, *,
                            event_id: str, outcome: str, note: str,
                            next_due: date | None = None,
                            today: date | None = None) -> dict:
    now = today or date.today()
    if (type(task_id) is not int or type(event_id) is not str
            or not re.fullmatch(r"DEMO-EVENT-[A-Za-z0-9_-]{3,64}", event_id)
            or outcome not in OUTCOMES or
            (next_due is not None and type(next_due) is not date)):
        raise ValueError("Invalid synthetic event")
    note = _note(note)
    if outcome == "FOLLOW_UP" and (next_due is None or not 0 < (next_due - now).days <= 45):
        raise ValueError("Follow-up requires future due date")
    if outcome != "FOLLOW_UP" and next_due is not None:
        raise ValueError("Only follow-up may have next_due")
    with transaction(db_path) as con:
        old = con.execute(
            "SELECT task_id,event_type,note,next_due FROM demo_manager_task_events "
            "WHERE event_id=?", (event_id,)
        ).fetchone()
        due_text = next_due.isoformat() if next_due else None
        if old:
            if (old["task_id"] != task_id or old["event_type"] != outcome
                    or old["note"] != note or old["next_due"] != due_text):
                raise ValueError("Event ID cannot be changed")
            return {"status": "DEMO_EVENT_ALREADY_RECORDED", "crm_writes": "NO"}
        task = con.execute("SELECT * FROM demo_manager_tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise ValueError("Unknown local task")
        if task["state"] == "CLOSED_SIMULATED":
            return {"status": "DEMO_TASK_ALREADY_CLOSED", "crm_writes": "NO"}
        company, signal = _verify_company_signal(con, task["company_id"], task["signal_id"])
        if signal["source_name"] not in SYNTHETIC_SOURCES:
            raise ValueError("Only synthetic data allowed")
        stops = {r["channel"] for r in con.execute(
            "SELECT channel FROM suppressions WHERE company_id=?", (task["company_id"],))}
        if (bool(company["existing_customer"]) or bool(company["manual_review"])
                or {"all", "new_customer"} & stops):
            return {"status": "DEMO_EVENT_POLICY_HOLD", "crm_writes": "NO"}
        next_state = ("FOLLOW_UP" if outcome == "FOLLOW_UP"
                      else "CLOSED_SIMULATED" if outcome in {"INTERESTED", "NO_INTEREST"}
                      else "OPEN")
        con.execute(
            "INSERT INTO demo_manager_task_events "
            "(event_id,task_id,event_type,note,recorded_on,next_due,payload_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (event_id, task_id, outcome, note, now.isoformat(), due_text,
             json.dumps({"synthetic": True, "real_contact": False}, sort_keys=True))
        )
        con.execute(
            "UPDATE demo_manager_tasks SET state=?,due_on=COALESCE(?,due_on),updated_on=? "
            "WHERE id=?", (next_state, due_text, now.isoformat(), task_id)
        )
        return {"status": "DEMO_RESULT_RECORDED_LOCALLY", "state": next_state,
                "crm_writes": "NO", "real_customer_contact": False}


def overview(db_path: str | Path) -> dict:
    with closing(sqlite3.connect(db_path)) as con:
        tasks = dict(con.execute(
            "SELECT state,COUNT(*) FROM demo_manager_tasks GROUP BY state").fetchall())
        events = dict(con.execute(
            "SELECT event_type,COUNT(*) FROM demo_manager_task_events GROUP BY event_type").fetchall())
    return {"mode": "LOCAL_SYNTHETIC_TASKS_ONLY", "tasks": tasks,
            "outcomes": events, "real_contacts": 0,
            "crm_writes": "NO", "validated_revenue": 0}
