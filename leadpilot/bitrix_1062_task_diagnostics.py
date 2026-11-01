"""Sanitized read-only diagnosis of Bitrix24 1062/task metadata permissions.

Never show response bodies, CRM fields, employee details or webhook secrets.
No mutations, no user search, no broad item scans and no arbitrary REST methods.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_task_metadata import audit_synthetic_task_link

METHODS = (
    ("crm.type.get", {"entityTypeId": 1062}),
    ("crm.enum.ownertype", {}),
    ("tasks.task.getFields", {}),
    ("crm.item.get", {
        "entityTypeId": 1062, "id": 3, "useOriginalUfNames": "Y",
    }),
)
ALLOWLIST = frozenset(name for name, _ in METHODS)
ERROR_CODES = frozenset({
    "ACCESS_DENIED", "INSUFFICIENT_SCOPE", "INVALID_SCOPE",
    "NO_AUTH_FOUND", "INVALID_CREDENTIALS", "ERROR_METHOD_NOT_FOUND",
    "ERROR_CORE", "INVALID_ARG_VALUE", "ERROR_NOT_FOUND",
    "NOT_FOUND", "INVALID_AUTH", "INVALID_REQUEST",
})
MAX_RESPONSE_BYTES = 512_000


def _error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "UNAVAILABLE"
    value = payload.get("error")
    if isinstance(value, str) and value.upper() in ERROR_CODES:
        return value.upper()
    return "OTHER_OR_NOT_PROVIDED"


def _inspect_one(client: httpx.Client, base: str, name: str, params: dict) -> dict:
    if name not in ALLOWLIST:
        return {"state": "METHOD_FORBIDDEN"}
    try:
        resp = client.post(base + "/" + name + ".json",
                           json=params, timeout=12.0, follow_redirects=False)
    except (httpx.HTTPError, ValueError, TypeError):
        return {"state": "NETWORK_OR_TRANSPORT_ERROR"}
    if len(resp.content) > MAX_RESPONSE_BYTES:
        return {"state": "OVERSIZED_RESPONSE"}
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return {"state": "NON_JSON_RESPONSE",
                "http_status": resp.status_code if resp.status_code in (200, 400, 401, 403, 404, 429, 500, 502, 503) else "OTHER"}
    if resp.status_code != 200:
        return {"state": "HTTP_ERROR",
                "http_status": resp.status_code if resp.status_code in (400, 401, 403, 404, 429, 500, 502, 503) else "OTHER",
                "api_error_code": _error_code(data)}
    if not isinstance(data, dict):
        return {"state": "RESPONSE_NOT_OBJECT"}
    if "error" in data:
        return {"state": "API_ERROR", "api_error_code": _error_code(data)}
    if "result" not in data:
        return {"state": "NO_RESULT_KEY"}
    result = data["result"]
    if not isinstance(result, (dict, list)):
        return {"state": "RESULT_UNEXPECTED_TYPE"}
    return {"state": "OK", "result_shape": "LIST" if isinstance(result, list) else "OBJECT"}


def diagnose_task_metadata(
    webhook: str, *, client: httpx.Client | None = None,
) -> dict:
    """At most 8 read-only calls; returning only finite status codes and flags.

    Four methods are independently probed; if all reachable, the already tested
    exact-card audit is repeated read-only to validate schema/gates. Missing
    rights or incompatible schemas always fail closed.
    """
    base = validate_webhook(webhook)
    owned = client is None
    if owned:
        client = httpx.Client(timeout=12.0, follow_redirects=False, trust_env=False)
    try:
        checks = {method: _inspect_one(client, base, method, params)
                  for method, params in METHODS}
        all_reachable = all(check["state"] == "OK" for check in checks.values())
        report = {
            "status": "BLOCKED_READ_ONLY",
            "portal": "serviseit.bitrix24.ru",
            "scope": "ONE_DEMO_1062_ITEM_3",
            "methods": checks,
            "methods_ok": sum(check["state"] == "OK" for check in checks.values()),
            "task_scope_missing_possible": (
                checks["tasks.task.getFields"]["state"] in ("API_ERROR", "HTTP_ERROR")
            ),
            "crm_scope_missing_possible": any(
                checks[m]["state"] in ("API_ERROR", "HTTP_ERROR")
                for m in ("crm.type.get", "crm.enum.ownertype", "crm.item.get")
            ),
            "metadata_gates": None,
            "task_created": False, "crm_writes": "NO",
            "customer_contact": "NO", "live_worker_enabled": False,
        }
        if all_reachable:
            try:
                result = audit_synthetic_task_link(webhook, client=client)
            except (ValueError, httpx.HTTPError, TypeError):
                report["metadata_gates"] = "SCHEMA_OR_SECOND_READ_BLOCKED"
            else:
                report["metadata_gates"] = {
                    name: result[name] for name in (
                        "owner_symbol_verified", "task_link_enabled",
                        "automation_confirmed_off", "task_fields_ready",
                        "exact_demo_card_readback",
                    )
                }
                if result["status"] == "LINKED_TASK_METADATA_READY":
                    report["status"] = "METADATA_READY_TASK_NOT_CREATED"
        return report
    finally:
        if owned:
            client.close()


def main() -> None:
    try:
        report = diagnose_task_metadata(os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", ""))
    except (ValueError, httpx.HTTPError, TypeError):
        report = {
            "status": "BLOCKED_WEBHOOK_OR_INPUT",
            "task_created": False, "crm_writes": "NO",
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("SERVICEIT_TASK_DIAGNOSTIC=" + report["status"])
    raise SystemExit(0 if report["status"] == "METADATA_READY_TASK_NOT_CREATED" else 2)


if __name__ == "__main__":
    main()
