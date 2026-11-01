"""Offline-only mapping from approved synthetic signal intents to Bitrix24 1062.

This module never imports an HTTP client, reads environment secrets, or issues
CRM calls. Output describes a *candidate* crm.item.add request, NOT approval
for or execution of a write. Live delivery, manager authorization and global
idempotency require separate validation before real-source onboarding.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .bitrix_synthetic_text_guard import synthetic_action_is_safe

PORTAL = "serviseit.bitrix24.ru"
ENTITY_TYPE_ID = 1062
CATEGORY_ID = 21
TYPE_ID = 13
CONTRACT_VERSION = "serviceit-bitrix-intent-v1"
INITIAL_STAGE = "DT1062_21:NEW"
SYNTHETIC_SOURCES = frozenset({"synthetic-fixture", "synthetic-second"})

FIELD_MAP = {
    "company_inn": "UF_CRM_13_SVCIT_COMPANY_INN",
    "company_name": "UF_CRM_13_SVCIT_COMPANY_NAME",
    "region": "UF_CRM_13_SVCIT_REGION",
    "signal_id": "UF_CRM_13_SVCIT_SIGNAL_ID",
    "signal_date": "UF_CRM_13_SVCIT_SIGNAL_DATE",
    "signal_source": "UF_CRM_13_SVCIT_SIGNAL_SOURCE",
    "signal_url": "UF_CRM_13_SVCIT_SIGNAL_URL",
    "suggested_service": "UF_CRM_13_SVCIT_SUGGESTED_SERVICE",
    "lead_score": "UF_CRM_13_SVCIT_LEAD_SCORE",
    "score_version": "UF_CRM_13_SVCIT_SCORE_VERSION",
    "manager_next_action": "UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION",
}
EXTRA_FIELDS = {
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS": "Синтетический тест — не реальная потребность",
    "UF_CRM_13_SVCIT_REVIEW_DECISION": "Ожидает проверки менеджером",
    "UF_CRM_13_SVCIT_STOP_REASON": "",
    "UF_CRM_13_SVCIT_SIGNAL_KIND": "demo-synthetic",
}
IDEMPOTENCY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
ALL_FIELD_CODES = frozenset((*FIELD_MAP.values(), *EXTRA_FIELDS.keys(), IDEMPOTENCY_FIELD))
assert len(ALL_FIELD_CODES) == 16


class SyntheticPlanRejected(ValueError):
    """Invalid or unsafe offline input; no CRM calls have been made."""


def _reject(reason: str) -> None:
    raise SyntheticPlanRejected(reason)


def _is_valid_inn(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{10}|\d{12}", value):
        return False
    nums = [int(x) for x in value]
    if len(nums) == 10:
        return (sum(a * b for a, b in zip(nums, (2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11) % 10 == nums[9]
    c11 = (sum(a * b for a, b in zip(nums, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11) % 10
    c12 = (sum(a * b for a, b in zip(nums, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11) % 10
    return c11 == nums[10] and c12 == nums[11]


def _validate_card(operation: Mapping[str, Any], today: date) -> tuple[str, dict[str, Any]]:
    if set(operation) != {"status", "idempotency_key", "logical_fields", "crm_entity_type", "actual_crm_field_codes", "crm_writes", "real_customer_contact"}:
        _reject("OFFLINE_OPERATION_SHAPE_CHANGED")
    if operation["status"] != "DRY_RUN_INTENT_ONLY" or operation["crm_writes"] != "NO" or operation["real_customer_contact"] is not False:
        _reject("NON_SYNTHETIC_INTENT_FORBIDDEN")
    if operation["crm_entity_type"] != "UNCONFIRMED" or operation["actual_crm_field_codes"] != "UNCONFIRMED":
        _reject("OFFLINE_CONTRACT_MISMATCH")
    f = operation["logical_fields"]
    if not isinstance(f, dict) or set(f) != set(FIELD_MAP):
        _reject("LOGICAL_FIELD_SCHEMA_MISMATCH")
    inn, sid = f["company_inn"], f["signal_id"]
    if not _is_valid_inn(inn) or not isinstance(sid, str) or not re.fullmatch(r"DEMO-[A-Za-z0-9_-]{4,40}", sid):
        _reject("DEMO_IDENTITY_INVALID")
    key = "DEMO-IDEMP-" + sha256(f"{CONTRACT_VERSION}\x00{inn}\x00{sid}".encode()).hexdigest()[:32]
    if operation["idempotency_key"] != key:
        _reject("IDEMPOTENCY_MISMATCH")
    name, region, source, url = (f[k] for k in ("company_name", "region", "signal_source", "signal_url"))
    if (not isinstance(name, str) or not re.fullmatch(r"DEMO[ -][\w .-]{4,80}", name)
            or not synthetic_action_is_safe(name)
            or "\n" in name or "\r" in name or "@" in name or region not in {"Москва", "Московская область"}
            or source not in SYNTHETIC_SOURCES or not isinstance(url, str)):
        _reject("SYNTHETIC_SOURCE_OR_NAME_INVALID")
    try:
        u = urlsplit(url)
        if (u.scheme != "https" or u.hostname != "example.invalid" or u.username or u.password
                or u.port not in (None, 443) or u.query or u.fragment
                or not synthetic_action_is_safe(u.path)
                or not re.fullmatch(r'/serviceit-demo/[A-Za-z0-9/_-]{1,100}', u.path)
                or not u.path.startswith("/serviceit-demo/")):
            _reject("SYNTHETIC_URL_INVALID")
        dt = date.fromisoformat(f["signal_date"])
    except (TypeError, ValueError, AttributeError):
        _reject("SYNTHETIC_DATE_OR_URL_INVALID")
    if not 0 <= (today - dt).days <= 30:
        _reject("SYNTHETIC_SIGNAL_DATE_OUT_OF_RANGE")
    score, ver, svc, action = (f[k] for k in ("lead_score", "score_version", "suggested_service", "manager_next_action"))
    if (type(score) is not int or not 0 <= score <= 100 or not isinstance(ver, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", ver)
            or svc not in {"1С", "IT-аутсорсинг", "ККТ"}
            or not isinstance(action, str) or not 8 <= len(action) <= 120
            or not synthetic_action_is_safe(action)):
        _reject("SIGNAL_VALUES_INVALID")
    fields: dict[str, Any] = {
        "title": f"DEMO ServiceIT AI — {sid}",
        "categoryId": CATEGORY_ID,
        "stageId": INITIAL_STAGE,
    }
    fields.update({code: f[logical] for logical, code in FIELD_MAP.items()})
    fields.update(EXTRA_FIELDS)
    fields[IDEMPOTENCY_FIELD] = key
    if frozenset(k for k in fields if k.startswith("UF_CRM")) != ALL_FIELD_CODES:
        _reject("FIELD_MAPPING_INCOMPLETE")
    return key, fields


def build_synthetic_1062_plan(report: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Make a deterministic, network-free preview from the offline handoff report."""
    if (not isinstance(report, dict) or report.get("contract_version") != CONTRACT_VERSION
            or report.get("mode") != "SYNTHETIC_OFFLINE_ONLY" or report.get("portal") != PORTAL
            or report.get("crm_writes") != "NO" or report.get("production_activated") is not False
            or report.get("outbound_messages") != 0 or report.get("real_customer_records_read") != 0):
        _reject("REPORT_NOT_OFFLINE_SYNTHETIC")
    ops = report.get("operations")
    if not isinstance(ops, list) or len(ops) > 250:
        _reject("OPERATIONS_MISSING_OR_TOO_MANY")
    requests = []
    seen: set[str] = set()
    now = today or date.today()
    for op in ops:
        if not isinstance(op, dict) or op.get("status") not in {"DRY_RUN_INTENT_ONLY", "DUPLICATE_SIMULATION_SKIPPED", "HOLD"}:
            _reject("UNKNOWN_OPERATION_STATUS")
        if op.get("status") != "DRY_RUN_INTENT_ONLY":
            if op.get("crm_writes") != "NO":
                _reject("BLOCKED_OPERATION_CLAIMS_WRITE")
            continue
        key, fields = _validate_card(op, now)
        if key in seen:
            _reject("REPEATED_INTENT_WITHOUT_OFFLINE_DEDUP")
        seen.add(key)
        requests.append({
            "operation": "PREVIEW_ONLY_NOT_EXECUTABLE",
            "idempotency_key": key,
            "method_if_separately_authorized": "crm.item.add",
            "params": {"entityTypeId": ENTITY_TYPE_ID, "useOriginalUfNames": "Y", "fields": fields},
        })
    return {
        "status": "BITRIX_1062_MAPPING_PREVIEW_ONLY",
        "portal": PORTAL,
        "entity_type_id": ENTITY_TYPE_ID,
        "category_id": CATEGORY_ID,
        "field_count": len(ALL_FIELD_CODES),
        "operation_count": len(requests),
        "requests_not_sent": requests,
        "crm_writes": "NO",
        "manager_approval_for_real_leads": "NOT_GRANTED",
        "live_adapter_activated": False,
        "source_connections": 0,
    }
