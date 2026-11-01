"""Offline manager review bridge for the verified ServiceIT Bitrix24 1062 schema.

Consumes the existing workflow_ops decision vocabulary (relevant, irrelevant,
needs_research) and the synthetic-only Bitrix mapping. Emits staged, NON-
EXECUTABLE updates. Does not read live CRM records or send HTTP requests.

A locally entered review DOES NOT prove the human's Bitrix role or authorize
customer contact. Real manager identity, item binding, and CRM rights remain
separate acceptance gates.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Mapping

from .bitrix_1062_mapping import (
    build_synthetic_1062_plan, SyntheticPlanRejected,
    ENTITY_TYPE_ID, CATEGORY_ID, IDEMPOTENCY_FIELD,
)

DECISION_TO_STAGE = {
    "relevant": "DT1062_21:SUCCESS",
    "irrelevant": "DT1062_21:FAIL",
    "needs_research": "DT1062_21:PREPARATION",
}
DECISION_TO_LABEL = {
    "relevant": "DEMO_RELEVANT_NOT_A_SALE",
    "irrelevant": "DEMO_REJECTED",
    "needs_research": "DEMO_RESEARCH_REQUIRED",
}
REVIEW_FIELD = "UF_CRM_13_SVCIT_REVIEW_DECISION"
EVIDENCE_FIELD = "UF_CRM_13_SVCIT_EVIDENCE_STATUS"
STOP_FIELD = "UF_CRM_13_SVCIT_STOP_REASON"
SAFE_REVIEWER = re.compile(r"DEMO-MANAGER-[0-9]{1,3}\Z")
SAFE_EVENT = re.compile(r"DEMO-REVIEW-[A-Za-z0-9_-]{4,50}\Z")


def _reject(code: str) -> None:
    raise SyntheticPlanRejected(code)


def _check_review(review: Mapping[str, Any], today: date) -> dict:
    if not isinstance(review, dict) or set(review) != {
        "idempotency_key", "event_id", "decision", "reviewer_role",
        "reviewer_label", "reason", "reviewed_on",
    }:
        _reject("MANAGER_REVIEW_SHAPE_INVALID")
    key = review["idempotency_key"]
    event = review["event_id"]
    reviewer = review["reviewer_label"]
    decision = review["decision"]
    role = review["reviewer_role"]
    reason = review["reason"]
    if (not isinstance(key, str) or not key.startswith("DEMO-IDEMP-")
            or len(key) != 43 or not isinstance(event, str) or not SAFE_EVENT.fullmatch(event)
            or not isinstance(reviewer, str) or not SAFE_REVIEWER.fullmatch(reviewer)
            or role not in ("manager", "supervisor")
            or decision not in DECISION_TO_STAGE):
        _reject("MANAGER_REVIEW_NOT_SYNTHETIC")
    if (not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 200
            or reason != reason.strip()
            or any(x in reason.lower() for x in ("@", "http://", "https://"))
            or any(ord(c) < 32 or ord(c) == 127 for c in reason)):
        _reject("MANAGER_REASON_UNSAFE")
    try:
        reviewed = date.fromisoformat(review["reviewed_on"])
    except (ValueError, TypeError):
        _reject("MANAGER_REVIEW_DATE_INVALID")
    if not 0 <= (today - reviewed).days <= 30:
        _reject("MANAGER_REVIEW_DATE_OUT_OF_RANGE")
    return dict(review)


def build_synthetic_1062_manager_plan(
    offline_report: Mapping[str, Any],
    reviews: list[dict],
    *,
    today: date | None = None,
) -> dict:
    """Prepare a manager decision for each approved synthetic intent, no I/O.

    The stage update can only be applied after independently reading the
    actual CRM item id, its idempotency key, category, and current stage
    CLIENT. This function deliberately contains no item ID and no transport.
    """
    now = today or date.today()
    base = build_synthetic_1062_plan(offline_report, today=now)
    if not isinstance(reviews, list) or len(reviews) > 250:
        _reject("MANAGER_REVIEWS_INVALID")
    planned = {item["idempotency_key"]: item for item in base["requests_not_sent"]}
    if len(planned) != len(base["requests_not_sent"]):
        _reject("MANAGER_PLAN_DUPLICATED_SIGNAL")
    seen_events: set[str] = set()
    seen_signals: set[str] = set()
    actions = []
    for raw in reviews:
        review = _check_review(raw, now)
        key, event = review["idempotency_key"], review["event_id"]
        if key not in planned:
            _reject("MANAGER_REVIEW_NOT_LINKED_TO_SIGNAL")
        if key in seen_signals or event in seen_events:
            _reject("MANAGER_REVIEW_REPLAY_OR_CONFLICT")
        seen_signals.add(key)
        seen_events.add(event)
        original = planned[key]["params"]["fields"]
        if original.get(IDEMPOTENCY_FIELD) != key:
            _reject("MANAGER_IDEMPOTENCY_MISMATCH")
        decision = review["decision"]
        fields = {
            "stageId": DECISION_TO_STAGE[decision],
            REVIEW_FIELD: DECISION_TO_LABEL[decision],
            EVIDENCE_FIELD: "DEMO_MANUAL_REVIEW_ONLY",
            STOP_FIELD: "DEMO_REJECTED" if decision == "irrelevant" else "DEMO_NONE",
        }
        actions.append({
            "operation": "PREVIEW_ONLY_NOT_EXECUTABLE",
            "event_id": event,
            "idempotency_key": key,
            "reviewer_label": review["reviewer_label"],
            "reviewer_role": review["reviewer_role"],
            "reason": review["reason"],
            "reviewed_on": review["reviewed_on"],
            "expected_current_stage": "DT1062_21:CLIENT",
            "expected_category_id": CATEGORY_ID,
            "expected_field_value": {IDEMPOTENCY_FIELD: key},
            "method_if_separately_authorized": "crm.item.update",
            "params_template_without_item_id": {
                "entityTypeId": ENTITY_TYPE_ID,
                "useOriginalUfNames": "Y",
                "fields": fields,
            },
            "real_manager_authorization_verified": False,
        })
    return {
        "status": "BITRIX_1062_MANAGER_PREVIEW_ONLY",
        "entity_type_id": ENTITY_TYPE_ID,
        "category_id": CATEGORY_ID,
        "signal_count": base["operation_count"],
        "reviewed_count": len(actions),
        "unreviewed_count": len(planned) - len(actions),
        "actions_not_sent": actions,
        "crm_writes": "NO",
        "live_manager_identity_verified": False,
        "customer_contact_authorized": False,
        "source_connections": 0,
        "live_adapter_activated": False,
    }
