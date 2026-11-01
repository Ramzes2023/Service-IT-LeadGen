"""Bounded synthetic outbox -> CRM write/readback orchestration.

No scheduler, CLI, automatic activation, source connection, or customer writes.
This function requires an existing durable CLAIMED outbox entry, one-shot
synthetic add transport, exact-key read-only transport, and explicit local test
authorization. It MUST NOT be called for production company leads.

Never retry POST when outcome is ambiguous. Return UNCERTAIN and require a
separate read-only reconciliation pass. Source and manager approval for real
leads are deliberately not implemented.
"""
from __future__ import annotations

import json
import hmac
from pathlib import Path
import sqlite3

from .bitrix_1062_mapping import SyntheticPlanRejected, IDEMPOTENCY_FIELD
from .bitrix_1062_outbox import mark_uncertain, confirm_remote_readback
from .bitrix_1062_reconcile import inspect_remote_synthetic_key
from .bitrix_1062_rest_read import Bitrix1062ReadOnly
from .bitrix_1062_synthetic_add import (
    Bitrix1062SyntheticAddOnly, CONFIRMATION, validate_synthetic_create,
)


def _claimed_record(db_path: str | Path, claim: dict) -> tuple[str, dict]:
    """Read local outbox record and bind candidate to persisted claim."""
    if (not isinstance(claim, dict) or set(claim) != {
        "idempotency_key", "params_preview_only", "crm_writes", "state",
    } or claim["state"] != "CLAIMED" or claim["crm_writes"] != "NO"):
        raise SyntheticPlanRejected("CLAIM_SHAPE_INVALID")
    params = claim["params_preview_only"]
    key = validate_synthetic_create(params)
    if key != claim["idempotency_key"]:
        raise SyntheticPlanRejected("CLAIM_KEY_MISMATCH")
    db = sqlite3.connect(str(db_path), timeout=10)
    try:
        row = db.execute(
            "SELECT state,payload_json,attempts FROM bitrix_1062_outbox "
            "WHERE idempotency_key=?", (key,),
        ).fetchone()
    finally:
        db.close()
    if (row is None or row[0] != "CLAIMED" or row[2] != 1
            or json.loads(row[1]) != params):
        raise SyntheticPlanRejected("CLAIM_NOT_DURABLE_OR_ALREADY_ATTEMPTED")
    return key, params


def _hold(db_path: str | Path, key: str, reason: str) -> dict:
    mark_uncertain(db_path, key, reason_code=reason)
    return {
        "status": "UNCERTAIN_MANUAL_RECONCILIATION",
        "reason_code": reason,
        "key": key,
        "automatic_retry": False,
        "production_activated": False,
    }


def deliver_one_claimed_synthetic(
    db_path: str | Path,
    claim: dict,
    *,
    read_only: Bitrix1062ReadOnly,
    one_shot_add: Bitrix1062SyntheticAddOnly,
    approval: str,
    rotated_webhook_confirmed: bool,
    robots_disabled_confirmed: bool,
    authorized_test_confirmed: bool,
) -> dict:
    """One authorized DEMO attempt, with pre/post exact-key readback.

    This function has no company contacts, manager notifications, or source
    ingestion. A successful add changes test CRM and may leave an isolated
    card for an authorized operator to clean up separately; it is NOT a
    read-only operation. No automatic deletion is attempted.
    """
    key, params = _claimed_record(db_path, claim)
    if (not isinstance(read_only, Bitrix1062ReadOnly)
            or not isinstance(one_shot_add, Bitrix1062SyntheticAddOnly)):
        raise SyntheticPlanRejected("GATED_TRANSPORTS_REQUIRED")
    # A narrower read-only role must not search for duplicates that the
    # writer's identity could see differently. Require one exact credential.
    if not hmac.compare_digest(read_only._base, one_shot_add._base):
        return _hold(db_path, key, "WEBHOOK_IDENTITY_MISMATCH")
    if (approval != CONFIRMATION
            or rotated_webhook_confirmed is not True
            or robots_disabled_confirmed is not True
            or authorized_test_confirmed is not True):
        return _hold(db_path, key, "TEST_AUTHORIZATION_NOT_CONFIRMED")

    try:
        previous = inspect_remote_synthetic_key(read_only, key)
    except Exception:
        # Bitrix filter may reject a custom UF field; NEVER assume absence.
        return _hold(db_path, key, "EXACT_KEY_READ_PREFLIGHT_FAILED")

    if previous["status"] == "EXACT_SYNTHETIC_ITEM_VISIBLE":
        confirmed = confirm_remote_readback(db_path, key, crm_item=previous["item"])
        return {"status": "ALREADY_PRESENT_VERIFIED_NO_POST",
                "external_id": confirmed["external_id"],
                "production_activated": False, "crm_item_add_calls": 0}
    if previous["status"] != "NO_VISIBLE_MATCH_NOT_PROOF_OF_ABSENCE":
        return _hold(db_path, key, "REMOTE_DUPLICATE_OR_AMBIGUOUS")

    # The one-shot client itself validates the full canonical synthetic
    # 16-field payload and guards the method to crm.item.add only.
    try:
        attempted = one_shot_add.add_once(
            params, approval=approval,
            rotated_webhook_confirmed=rotated_webhook_confirmed,
            robots_disabled_confirmed=robots_disabled_confirmed,
            authorized_test_confirmed=authorized_test_confirmed,
        )
    except Exception:
        # Never expose exception text, which may include a secret URL.
        return _hold(db_path, key, "SYNTHETIC_ADD_EXCEPTION_UNCERTAIN")

    if attempted.get("status") != "POST_RESPONSE_UNVERIFIED":
        return _hold(db_path, key, "SYNTHETIC_ADD_RESULT_UNCERTAIN")

    try:
        remote = inspect_remote_synthetic_key(read_only, key)
        if remote["status"] != "EXACT_SYNTHETIC_ITEM_VISIBLE":
            return _hold(db_path, key, "POST_READBACK_INCOMPLETE")
        if remote["item"]["id"] != attempted["candidate_id"]:
            return _hold(db_path, key, "POST_READBACK_ID_CONFLICT")
        # The outbox compares actual category, entity, original key,
        # synthetic title, and stage before acknowledging VERIFIED.
        receipt = confirm_remote_readback(db_path, key, crm_item=remote["item"])
    except Exception:
        return _hold(db_path, key, "POST_READBACK_FAILED")
    return {"status": "SYNTHETIC_POST_VERIFIED_REQUIRES_CLEANUP",
            "external_id": receipt["external_id"],
            "crm_item_add_calls": 1,
            "production_activated": False,
            "requires_authorized_test_cleanup": True}
