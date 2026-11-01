"""Durable local handoff outbox for synthetic ServiceIT Bitrix24 1062 intents.

No REST client, webhook, credentials, workers or real-source activation.
A claimed operation is never automatically put back into the queue: after
an ambiguous HTTP response it requires explicit reconciliation against the
CRM by its idempotency key. Never assume exactly-once from a POST call.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .bitrix_1062_mapping import (
    ALL_FIELD_CODES, CATEGORY_ID, ENTITY_TYPE_ID, IDEMPOTENCY_FIELD,
    INITIAL_STAGE, SyntheticPlanRejected,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bitrix_1062_outbox (
  idempotency_key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('QUEUED','CLAIMED','UNCERTAIN','VERIFIED')),
  attempts INTEGER NOT NULL DEFAULT 0,
  external_id INTEGER,
  last_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bitrix_1062_outbox_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL,
  event TEXT NOT NULL,
  detail_code TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bitrix_1062_outbox_state_idx
ON bitrix_1062_outbox(state,created_at,idempotency_key);
"""
KEY = re.compile(r"DEMO-IDEMP-[0-9a-f]{32}\Z")
SAFE_REASON = re.compile(r"[A-Z][A-Z0-9_]{2,63}\Z")
_SAFE_STATES = {"QUEUED", "CLAIMED", "UNCERTAIN", "VERIFIED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _event(db: sqlite3.Connection, key: str, action: str, code: str) -> None:
    db.execute(
        "INSERT INTO bitrix_1062_outbox_audit "
        "(idempotency_key,event,detail_code,created_at) VALUES(?,?,?,?)",
        (key, action, code, _now()),
    )


def initialize(db_path: str | Path) -> None:
    """Initialize only a caller-chosen local database; never touch Bitrix."""
    with closing(_connect(db_path)) as db:
        db.executescript(SCHEMA)
        db.commit()


def _validate_plan(plan: dict) -> list[tuple[str, str, str]]:
    if not isinstance(plan, dict) or plan.get("status") != "BITRIX_1062_MAPPING_PREVIEW_ONLY":
        raise SyntheticPlanRejected("NOT_AN_OFFLINE_1062_PLAN")
    if (plan.get("portal") != "serviseit.bitrix24.ru"
            or plan.get("entity_type_id") != ENTITY_TYPE_ID
            or plan.get("category_id") != CATEGORY_ID
            or plan.get("field_count") != 16
            or plan.get("crm_writes") != "NO"
            or plan.get("live_adapter_activated") is not False
            or plan.get("source_connections") != 0):
        raise SyntheticPlanRejected("PLAN_OUT_OF_SCOPE")
    ops = plan.get("requests_not_sent")
    if (not isinstance(ops, list) or len(ops) > 250
            or plan.get("operation_count") != len(ops)):
        raise SyntheticPlanRejected("PLAN_COUNT_INVALID")
    validated = []
    seen: set[str] = set()
    for op in ops:
        if not isinstance(op, dict) or set(op) != {
            "operation", "idempotency_key", "method_if_separately_authorized", "params"
        }:
            raise SyntheticPlanRejected("OPERATION_SHAPE_INVALID")
        key, params = op["idempotency_key"], op["params"]
        if (not isinstance(key, str) or not KEY.fullmatch(key) or key in seen
                or op["operation"] != "PREVIEW_ONLY_NOT_EXECUTABLE"
                or op["method_if_separately_authorized"] != "crm.item.add"):
            raise SyntheticPlanRejected("OPERATION_IDENTITY_INVALID")
        seen.add(key)
        if (not isinstance(params, dict) or set(params) != {
                "entityTypeId", "useOriginalUfNames", "fields"
        } or params["entityTypeId"] != ENTITY_TYPE_ID
                or params["useOriginalUfNames"] != "Y"):
            raise SyntheticPlanRejected("OPERATION_TARGET_INVALID")
        fields = params["fields"]
        if (not isinstance(fields, dict)
                or set(fields) != ALL_FIELD_CODES | {"title", "categoryId", "stageId"}
                or fields["categoryId"] != CATEGORY_ID
                or fields["stageId"] != INITIAL_STAGE
                or fields.get(IDEMPOTENCY_FIELD) != key
                or not isinstance(fields["title"], str)
                or not re.fullmatch(r"DEMO ServiceIT AI — DEMO-[A-Za-z0-9_-]{4,40}", fields["title"])
                or not isinstance(fields["UF_CRM_13_SVCIT_SIGNAL_ID"], str)
                or not fields["title"].endswith(fields["UF_CRM_13_SVCIT_SIGNAL_ID"])
                or fields["UF_CRM_13_SVCIT_SIGNAL_SOURCE"] not in
                   ("synthetic-fixture", "synthetic-second")
                or fields["UF_CRM_13_SVCIT_SIGNAL_KIND"] != "demo-synthetic"\n                or not isinstance(fields.get("UF_CRM_13_SVCIT_COMPANY_INN"), str)\n                or not re.fullmatch(r"\\d{10}|\\d{12}", fields["UF_CRM_13_SVCIT_COMPANY_INN"])\n                or key != "DEMO-IDEMP-" + sha256((\n                    "serviceit-bitrix-intent-v1\\x00" + fields["UF_CRM_13_SVCIT_COMPANY_INN"]\n                    + "\\x00" + fields["UF_CRM_13_SVCIT_SIGNAL_ID"]\n                ).encode("utf-8")).hexdigest()[:32]):
            raise SyntheticPlanRejected("OPERATION_FIELDS_INVALID")
        payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = sha256(payload.encode("utf-8")).hexdigest()
        validated.append((key, payload, digest))
    return validated


def enqueue_synthetic_plan(db_path: str | Path, plan: dict) -> dict:
    """Atomically queue synthetic intents. Identical replay = skip, changed = fail."""
    records = _validate_plan(plan)
    inserted = skipped = 0
    with closing(_connect(db_path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            for key, payload, digest in records:
                old = db.execute(
                    "SELECT payload_sha256 FROM bitrix_1062_outbox WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
                if old is not None:
                    if old["payload_sha256"] != digest:
                        raise SyntheticPlanRejected("IDEMPOTENCY_PAYLOAD_CONFLICT")
                    skipped += 1
                    continue
                t = _now()
                db.execute(
                    "INSERT INTO bitrix_1062_outbox "
                    "(idempotency_key,payload_json,payload_sha256,state,created_at,updated_at) "
                    "VALUES(?,?,?,'QUEUED',?,?)", (key, payload, digest, t, t),
                )
                _event(db, key, "QUEUED", "SYNTHETIC_OFFLINE_ONLY")
                inserted += 1
    return {"queued": inserted, "duplicate_skipped": skipped, "crm_writes": "NO"}


def claim_next(db_path: str | Path) -> dict | None:
    """Reserve one operation once. A crash leaves CLAIMED for manual recovery."""
    with closing(_connect(db_path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute(
                "SELECT idempotency_key,payload_json FROM bitrix_1062_outbox "
                "WHERE state='QUEUED' ORDER BY created_at,idempotency_key LIMIT 1"
            ).fetchone()
            if item is None:
                return None
            key = item["idempotency_key"]
            db.execute(
                "UPDATE bitrix_1062_outbox SET state='CLAIMED',attempts=attempts+1,"
                "updated_at=? WHERE idempotency_key=? AND state='QUEUED'",
                (_now(), key),
            )
            _event(db, key, "CLAIMED", "DO_NOT_AUTO_RETRY")
            return {"idempotency_key": key, "params_preview_only": json.loads(item["payload_json"]),
                    "crm_writes": "NO", "state": "CLAIMED"}


def mark_uncertain(db_path: str | Path, key: str, *, reason_code: str) -> dict:
    """Unknown POST outcome: lock key until exact-key remote reconciliation."""
    if (not isinstance(key, str) or not KEY.fullmatch(key)\n            or not isinstance(reason_code, str) or not SAFE_REASON.fullmatch(reason_code)):
        raise SyntheticPlanRejected("INVALID_UNCERTAIN_MARKER")
    with closing(_connect(db_path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT state FROM bitrix_1062_outbox WHERE idempotency_key=?", (key,)
            ).fetchone()
            if old is None or old["state"] not in ("CLAIMED", "UNCERTAIN"):
                raise SyntheticPlanRejected("UNCERTAIN_STATE_CONFLICT")
            db.execute(
                "UPDATE bitrix_1062_outbox SET state='UNCERTAIN',last_reason=?,updated_at=? "
                "WHERE idempotency_key=?", (reason_code, _now(), key)
            )
            _event(db, key, "UNCERTAIN", reason_code)
    return {"state": "UNCERTAIN", "requires_manual_reconciliation": True, "crm_writes": "NO"}


def confirm_remote_readback(db_path: str | Path, key: str, *, crm_item: dict) -> dict:
    """Accept one externally read-back isolated CRM item; never mutate remote CRM.

    The caller must independently obtain this item from crm.item.get; this
    method does not itself verify transport, authorization or webhook trust.
    """
    if not isinstance(key, str) or not KEY.fullmatch(key) or not isinstance(crm_item, dict):
        raise SyntheticPlanRejected("READBACK_SHAPE_INVALID")
    with closing(_connect(db_path)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute(
                "SELECT state,payload_json,external_id FROM bitrix_1062_outbox "
                "WHERE idempotency_key=?", (key,)
            ).fetchone()
            if record is None or record["state"] not in ("CLAIMED", "UNCERTAIN", "VERIFIED"):
                raise SyntheticPlanRejected("READBACK_STATE_INVALID")
            expected = json.loads(record["payload_json"])["fields"]
            item_id = crm_item.get("id")
            if (type(item_id) is not int or item_id <= 0
                    or crm_item.get("entityTypeId") != ENTITY_TYPE_ID
                    or crm_item.get("categoryId") != CATEGORY_ID
                    or crm_item.get(IDEMPOTENCY_FIELD) != key
                    or crm_item.get("title") != expected["title"]
                    or crm_item.get("stageId") not in {
                        INITIAL_STAGE, "DT1062_21:PREPARATION",
                        "DT1062_21:CLIENT", "DT1062_21:SUCCESS", "DT1062_21:FAIL"
                    }):
                raise SyntheticPlanRejected("REMOTE_READBACK_MISMATCH")
            if record["external_id"] is not None and record["external_id"] != item_id:
                raise SyntheticPlanRejected("REMOTE_IDENTITY_CONFLICT")
            db.execute(
                "UPDATE bitrix_1062_outbox SET state='VERIFIED',external_id=?,"
                "last_reason=NULL,updated_at=? WHERE idempotency_key=?",
                (item_id, _now(), key),
            )
            _event(db, key, "VERIFIED", "EXTERNALLY_READ_BACK")
    return {"state": "VERIFIED", "external_id": item_id, "crm_writes": "NO"}


def overview(db_path: str | Path) -> dict:
    """Local non-secret counts; don't mistake VERIFIED for production delivery."""
    with closing(_connect(db_path)) as db:
        states = {name: 0 for name in sorted(_SAFE_STATES)}
        for row in db.execute("SELECT state,COUNT(*) AS count FROM bitrix_1062_outbox GROUP BY state"):
            states[row["state"]] = row["count"]
        audit_events = db.execute("SELECT COUNT(*) FROM bitrix_1062_outbox_audit").fetchone()[0]
    return {"mode": "SYNTHETIC_LOCAL_OUTBOX_ONLY", "states": states, "audit_events": audit_events,
            "crm_writes": "NO", "live_transport": False, "source_connections": 0}
