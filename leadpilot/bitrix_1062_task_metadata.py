"""Read-only corporate preflight for ONE ServiceIT DEMO task association.

Methods permitted: crm.type.get, crm.enum.ownertype,
tasks.task.getFields, crm.item.get. Never return raw CRM data or webhook.
Does not look up employees or perform any task or CRM mutations.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_manager_task_preview import (
    _off, _positive, _yes, ENTITY, ITEM_ID, ITEM_TITLE,
    CATEGORY, STAGE, KEY_CODE, UF,
)
from .bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from hashlib import sha256

ALLOWED = ("crm.type.get", "crm.enum.ownertype",
           "tasks.task.getFields", "crm.item.get")


class TaskMetadataReadError(ValueError):
    """Sanitized diagnostic; never include webhook or untrusted body."""


def _call(client: httpx.Client, base: str, method: str, params: dict) -> Any:
    if method not in ALLOWED:
        raise TaskMetadataReadError("READ_METHOD_FORBIDDEN")
    try:
        response = client.post(
            base + "/" + method + ".json", json=params,
            timeout=12.0, follow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > 512_000:
            raise TaskMetadataReadError("READ_HTTP_UNAVAILABLE")
        result = response.json()
        if (not isinstance(result, dict) or "error" in result
                or "result" not in result):
            raise TaskMetadataReadError("READ_RESPONSE_UNAVAILABLE")
        return result["result"]
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        if isinstance(exc, TaskMetadataReadError):
            raise
        raise TaskMetadataReadError("READ_FAILED") from None


def audit_synthetic_task_link(
    webhook: str, *, client: httpx.Client | None = None,
) -> dict:
    """Perform four bounded read-only REST calls and return sanitized booleans."""
    base = validate_webhook(webhook)
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=12, trust_env=False, follow_redirects=False)
    try:
        type_result = _call(client, base, "crm.type.get", {"entityTypeId": ENTITY})
        owner_result = _call(client, base, "crm.enum.ownertype", {})
        task_result = _call(client, base, "tasks.task.getFields", {})
        item_result = _call(client, base, "crm.item.get", {
            "entityTypeId": ENTITY, "id": ITEM_ID, "useOriginalUfNames": "Y",
        })
        typ = type_result.get("type") if isinstance(type_result, dict) else None
        fields = task_result.get("fields") if isinstance(task_result, dict) else None
        item = item_result.get("item") if isinstance(item_result, dict) else None
        if not isinstance(typ, dict) or not isinstance(fields, dict) or not isinstance(item, dict):
            raise TaskMetadataReadError("SCHEMA_UNEXPECTED")

        types = owner_result if isinstance(owner_result, list) else []
        found = [v for v in types if isinstance(v, dict)
                 and str(v.get("ID")) == str(ENTITY)]
        symbol_ok = (
            len(found) == 1
            and found[0].get("SYMBOL_CODE_SHORT") == f"T{ENTITY:x}"
        )
        binding_on = (
            str(typ.get("entityTypeId")) == str(ENTITY)
            and _yes(typ.get("isUseInUserfieldEnabled"))
            and isinstance(typ.get("linkedUserFields"), dict)
            and _yes(typ["linkedUserFields"].get("TASKS_TASK|UF_CRM_TASK"))
        )
        automation_off = (
            _off(typ.get("isAutomationEnabled"))
            and _off(typ.get("isBizProcEnabled"))
        )
        task_schema = all(code in fields for code in ("TITLE", "RESPONSIBLE_ID", "UF_CRM_TASK"))
        crm_link_type = (
            task_schema and isinstance(fields["UF_CRM_TASK"], dict)
            and fields["UF_CRM_TASK"].get("type") == "crm"
        )
        expected_key = "DEMO-IDEMP-" + sha256(
            f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()
        ).hexdigest()[:32]
        item_exact = (
            str(item.get("id")) == str(ITEM_ID)
            and str(item.get("entityTypeId")) == str(ENTITY)
            and str(item.get("categoryId")) == str(CATEGORY)
            and item.get("stageId") == STAGE
            and item.get("title") == ITEM_TITLE
            and ALL_FIELD_CODES.issubset(item.keys())
            and item.get(KEY_CODE) == expected_key
            and item.get(UF["source"]) == "synthetic-fixture"
            and item.get(UF["signal"]) == "DEMO-ITREQ-001"
            and item.get(UF["inn"]) == "7700000009"
            and item.get(UF["name"]) == "DEMO COMPANY-001"
            and item.get(UF["url"]) == "https://example.invalid/serviceit-demo/request/001"
            and item.get("contactIds") in (None, [])
            and item.get("companyId") in (None, 0, "0")
        )
        safe = all((symbol_ok, binding_on, automation_off, crm_link_type, item_exact))
        return {
            "status": "LINKED_TASK_METADATA_READY" if safe else "LINKED_TASK_METADATA_BLOCKED",
            "portal": "serviseit.bitrix24.ru",
            "entity_type_id": ENTITY, "demo_item_id": ITEM_ID,
            "owner_symbol_verified": bool(symbol_ok),
            "task_link_enabled": bool(binding_on),
            "automation_confirmed_off": bool(automation_off),
            "task_fields_ready": bool(crm_link_type),
            "exact_demo_card_readback": bool(item_exact),
            "methods_called": list(ALLOWED),
            "manager_employee_id_verified": False,
            "task_created": False, "crm_writes": "NO", "customer_contacts": 0,
            "safe_to_create_task_now": False,
        }
    finally:
        if own_client:
            client.close()


def main() -> None:
    hook = os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", "")
    try:
        data = audit_synthetic_task_link(hook)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        if data["status"] != "LINKED_TASK_METADATA_READY":
            raise SystemExit(2)
    except (ValueError, httpx.HTTPError):
        print("SERVICEIT_TASK_METADATA=BLOCKED_READONLY; CRM_WRITES=NO")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
