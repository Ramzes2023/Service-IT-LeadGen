"""One-shot, explicitly approved synthetic-only Bitrix24 1062 POST adapter.

This is an API building block, NOT an enabled worker or a live CLI entrypoint.
Never use for actual companies or real source signals. The HTTP call might
succeed even if its response is lost: the caller must read back by exact
idempotency key before recording VERIFIED. Never retry an ambiguous POST.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_mapping import (
    ENTITY_TYPE_ID, FIELD_MAP, IDEMPOTENCY_FIELD,
    SyntheticPlanRejected, _validate_card,
)

CONFIRMATION = "AUTHORIZE ONE DEMO 1062"
MAX_BYTES = 256_000


def validate_synthetic_create(params: Any, *, today: date | None = None) -> str:
    """Prove that a request is the canonical *synthetic* 1062 mapping.

    Reject unknown fields, contacts, altered category/stage, fake company IDs,
    incorrectly hashed keys, non-demo source URLs and expired demo dates.
    """
    if not isinstance(params, dict) or set(params) != {
        "entityTypeId", "useOriginalUfNames", "fields",
    } or params["entityTypeId"] != ENTITY_TYPE_ID or type(params["entityTypeId"]) is not int \
            or params["useOriginalUfNames"] != "Y" or not isinstance(params["fields"], dict):
        raise SyntheticPlanRejected("CREATE_PARAMS_OUT_OF_SCOPE")
    fields = params["fields"]
    if any(not isinstance(fields.get(code), (str, int)) for code in FIELD_MAP.values()):
        raise SyntheticPlanRejected("CREATE_FIELDS_INVALID")
    logical = {logical: fields[code] for logical, code in FIELD_MAP.items()}
    key = fields.get(IDEMPOTENCY_FIELD)
    op = {
        "status": "DRY_RUN_INTENT_ONLY",
        "idempotency_key": key,
        "logical_fields": logical,
        "crm_entity_type": "UNCONFIRMED",
        "actual_crm_field_codes": "UNCONFIRMED",
        "crm_writes": "NO",
        "real_customer_contact": False,
    }
    try:
        canonical_key, canonical_fields = _validate_card(op, today or date.today())
    except (TypeError, ValueError, KeyError, AttributeError):
        raise SyntheticPlanRejected("CREATE_NOT_CANONICAL_SYNTHETIC") from None
    if fields != canonical_fields or canonical_key != key:
        raise SyntheticPlanRejected("CREATE_PAYLOAD_MISMATCH")
    return key


class Bitrix1062SyntheticAddOnly:
    """In-memory, one-shot client: exactly ONE call to crm.item.add maximum.

    Use httpx.MockTransport for acceptance. This object is never instantiated
    in an application service or automatically scheduled workflow.
    """
    def __init__(self, base_webhook: str, *, client: httpx.Client) -> None:
        self._base = validate_webhook(base_webhook)
        if not isinstance(client, httpx.Client):
            raise TypeError("HTTPX_CLIENT_REQUIRED")
        self._client = client
        self._used = False

    def __repr__(self) -> str:
        return "Bitrix1062SyntheticAddOnly(1062/21, ONE_SHOT)"

    def add_once(self, params: dict, *, approval: str,
                 rotated_webhook_confirmed: bool,
                 robots_disabled_confirmed: bool,
                 authorized_test_confirmed: bool,
                 today: date | None = None) -> dict:
        if (approval != CONFIRMATION
                or rotated_webhook_confirmed is not True
                or robots_disabled_confirmed is not True
                or authorized_test_confirmed is not True):
            return {"status": "WRITE_GATE_DENIED", "crm_writes": "NO"}
        if self._used:
            return {"status": "SECOND_POST_BLOCKED", "crm_writes": "NO"}
        key = validate_synthetic_create(params, today=today)
        self._used = True  # Set BEFORE I/O; timeouts do not re-arm this object.
        try:
            response = self._client.post(
                self._base + "/crm.item.add.json",
                json=params, follow_redirects=False, timeout=12.0,
            )
            if response.status_code != 200 or len(response.content) > MAX_BYTES:
                return {"status": "POST_RESULT_UNCERTAIN", "requires_readback": True,
                        "crm_writes": "UNKNOWN", "idempotency_key": key}
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                return {"status": "POST_RESULT_UNCERTAIN", "requires_readback": True,
                        "crm_writes": "UNKNOWN", "idempotency_key": key}
            result = payload.get("result")
            item = result.get("item") if isinstance(result, dict) else None
            candidate_id = item.get("id") if isinstance(item, dict) else None
            if type(candidate_id) is not int or candidate_id <= 0:
                return {"status": "POST_RESULT_UNCERTAIN", "requires_readback": True,
                        "crm_writes": "UNKNOWN", "idempotency_key": key}
            # API response cannot prove global uniqueness or durable write.
            return {"status": "POST_RESPONSE_UNVERIFIED", "candidate_id": candidate_id,
                    "idempotency_key": key, "requires_readback": True,
                    "crm_writes": "UNVERIFIED"}
        except (httpx.HTTPError, ValueError, TypeError):
            # Never print the exception: it may contain a webhook URL.
            return {"status": "POST_RESULT_UNCERTAIN", "requires_readback": True,
                    "crm_writes": "UNKNOWN", "idempotency_key": key}
