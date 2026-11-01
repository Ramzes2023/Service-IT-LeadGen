"""Offline binding of persisted manager decisions to exact synthetic 1062 intents.

Reads an existing local workflow_ops SQLite database. Neither this module nor
its dependencies creates CRM records or contacts customers. A local
reviewer_role / demo label is NOT verified company-user identity and CANNOT
authorize live CRM writes.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
from hashlib import sha256
from pathlib import Path
import sqlite3

from .bitrix_1062_mapping import (
    FIELD_MAP, SyntheticPlanRejected, build_synthetic_1062_plan,
)
from .bitrix_1062_manager_bridge import build_synthetic_1062_manager_plan
from .bitrix_synthetic_text_guard import synthetic_action_is_safe


def build_persisted_synthetic_manager_plan(
    db_path: str | Path,
    offline_report: dict,
    *,
    reviewer_label: str,
    today: date | None = None,
) -> dict:
    """Join persisted local reviews to their original synthetic signal identity.

    Conflicting/missing underlying signals fail closed. Missing or policy-held
    reviews remain unreviewed. Only a restricted in-memory preview is returned.
    """
    now = today or date.today()
    mapped = build_synthetic_1062_plan(offline_report, today=now)
    if not isinstance(reviewer_label, str):
        raise SyntheticPlanRejected("DEMO_REVIEWER_LABEL_INVALID")
    actions = []
    held = []
    try:
        with closing(sqlite3.connect(str(db_path))) as con:
            con.row_factory = sqlite3.Row
            for intent in mapped["requests_not_sent"]:
                fields = intent["params"]["fields"]
                key = intent["idempotency_key"]
                identity = (
                    fields[FIELD_MAP["company_inn"]],
                    fields[FIELD_MAP["signal_source"]],
                    fields[FIELD_MAP["signal_url"]],
                )
                rows = con.execute(
                    """SELECT c.id AS company_id,c.name,c.region,
                              c.existing_customer,c.manual_review,
                              s.id AS signal_row_id,s.signal_date,
                              r.decision,r.reviewer_role,r.reason,r.reviewed_on
                       FROM companies c
                       JOIN signals s ON s.company_id=c.id
                       LEFT JOIN workflow_reviews r
                         ON r.company_id=c.id AND r.signal_id=s.id
                       WHERE c.inn=? AND s.source_name=? AND s.source_url=?""",
                    identity,
                ).fetchall()
                if len(rows) != 1:
                    raise SyntheticPlanRejected("PERSISTED_SIGNAL_IDENTITY_NOT_UNIQUE")
                row = rows[0]
                if (row["name"] != fields[FIELD_MAP["company_name"]]
                        or row["region"] != fields[FIELD_MAP["region"]]
                        or row["signal_date"] != fields[FIELD_MAP["signal_date"]]):
                    raise SyntheticPlanRejected("PERSISTED_SIGNAL_EVIDENCE_MISMATCH")
                stops = con.execute(
                    "SELECT channel FROM suppressions WHERE company_id=?",
                    (row["company_id"],),
                ).fetchall()
                if (row["existing_customer"] or row["manual_review"] or stops):
                    held.append({"idempotency_key": key, "reason": "COMPANY_POLICY_HOLD"})
                    continue
                if row["decision"] is None:
                    continue
                reason = row["reason"]
                if not synthetic_action_is_safe(reason):
                    raise SyntheticPlanRejected("PERSISTED_REVIEW_NOTE_UNSAFE")
                token = sha256(
                    f"{key}\x00{row['signal_row_id']}\x00{row['reviewed_on']}".encode("utf-8")
                ).hexdigest()[:20]
                actions.append({
                    "idempotency_key": key,
                    "event_id": "DEMO-REVIEW-" + token,
                    "decision": row["decision"],
                    "reviewer_role": row["reviewer_role"],
                    "reviewer_label": reviewer_label,
                    "reason": reason,
                    "reviewed_on": row["reviewed_on"],
                })
    except sqlite3.Error:
        raise SyntheticPlanRejected("PERSISTED_WORKFLOW_NOT_READY") from None

    output = build_synthetic_1062_manager_plan(offline_report, actions, today=now)
    output["policy_held_count"] = len(held)
    output["policy_held_not_sent"] = held
    output["status"] = "PERSISTED_SYNTHETIC_MANAGER_PREVIEW_ONLY"
    output["local_review_is_real_crm_authorization"] = False
    output["crm_writes"] = "NO"
    output["customer_contact_authorized"] = False
    return output
