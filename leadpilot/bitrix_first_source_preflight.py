"""Read-only, exact-portal Bitrix24 smart-process preflight for Service IT.

A user invitation is not an API credential. This probe cannot create a Lead,
Deal, smart-process item, Contact, task or marketing communication. It confirms
only that a restricted, separate research smart-process is visible over REST.
No existing company/lead/CRM record is read.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

from .bitrix_sandbox import validate_webhook, PreflightError

EXPECTED_PORTAL = "serviseit.bitrix24.ru"
EXPECTED_PROCESS = "LeadGen AI — Тестирование"
READ_METHODS = frozenset({"crm.type.list", "crm.category.list", "crm.item.fields"})
MAX_RESPONSE_BYTES = 1024 * 1024


def _call(client: httpx.Client, base: str, method: str, payload: dict) -> object:
    if method not in READ_METHODS:
        raise PreflightError("Method outside safe read-only allowlist")
    try:
        response = client.post(base + "/" + method, json=payload, follow_redirects=False,
                               headers={"Accept": "application/json"})
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            raise PreflightError(method + ": access/transport/size failure")
        data = response.json()
        if (not isinstance(data, dict) or "error" in data
                or "result" not in data):
            raise PreflightError(method + ": permission/API failure")
        return data["result"]
    except (httpx.HTTPError, ValueError):
        raise PreflightError(method + ": transport/JSON failure") from None


def probe(webhook: str, *, expected_portal: str = EXPECTED_PORTAL,
          expected_process: str = EXPECTED_PROCESS,
          client: httpx.Client | None = None) -> dict:
    """No CRM writes and no real entity reads, even when access exists."""
    base = validate_webhook(webhook)
    host = (urlsplit(base).hostname or "").lower()
    if not expected_portal or host != expected_portal.lower():
        raise PreflightError("CRM host differs from explicitly approved Service IT portal")
    if not expected_process or len(expected_process) > 120:
        raise PreflightError("Invalid dedicated test-process name")
    report = {
        "portal": host, "mode": "READ_ONLY", "test_process": expected_process,
        "status": "NOT_VERIFIED", "smart_process_entity_type_id": None,
        "category_ids": [], "field_count": 0, "methods_called": [],
        "source": "trudvsem-open-vacancies", "qualified_real_leads": 0,
        "crm_writes": "NO", "outbound_messages": "NO",
        "portal_permission_to_write": "NOT_REQUESTED",
        "duplicate_check": "NOT_AUTHORIZED_YET",
        "first_source_live_handoff": "NOT_VERIFIED",
    }
    own = client is None
    if own:
        client = httpx.Client(timeout=12.0, follow_redirects=False, trust_env=False)
    try:
        try:
            types = _call(client, base, "crm.type.list", {"filter": {}, "start": 0})
            report["methods_called"].append("crm.type.list")
            if not isinstance(types, dict) or not isinstance(types.get("types"), list):
                raise PreflightError("Unexpected smart-process metadata")
            matching = [t for t in types["types"] if isinstance(t, dict)
                        and t.get("title") == expected_process
                        and isinstance(t.get("entityTypeId"), int)
                        and t["entityTypeId"] >= 128]
            if len(matching) != 1:
                report["status"] = "ISOLATED_SMART_PROCESS_NOT_FOUND"
                return report
            entity_id = matching[0]["entityTypeId"]
            report["smart_process_entity_type_id"] = entity_id
            categories = _call(client, base, "crm.category.list", {"entityTypeId": entity_id})
            report["methods_called"].append("crm.category.list")
            if not isinstance(categories, dict) or not isinstance(categories.get("categories"), list):
                raise PreflightError("Unexpected smart-process category metadata")
            report["category_ids"] = sorted({int(c["id"]) for c in categories["categories"]
                                            if isinstance(c, dict) and type(c.get("id")) is int
                                            and c["id"] >= 0})
            fields = _call(client, base, "crm.item.fields", {"entityTypeId": entity_id})
            report["methods_called"].append("crm.item.fields")
            if not isinstance(fields, dict) or not isinstance(fields.get("fields"), dict):
                raise PreflightError("Unexpected smart-process field metadata")
            report["field_count"] = len(fields["fields"])
            report["status"] = "ISOLATED_READ_ACCESS_CONFIRMED"
            return report
        except PreflightError as error:
            report["status"] = "READ_FAILED"
            report["error"] = str(error)
            return report
    finally:
        if own:
            client.close()


def main() -> None:
    webhook = os.getenv("BITRIX24_TEST_WEBHOOK", "")
    try:
        report = probe(webhook)
    except PreflightError as error:
        report = {"status": "CONFIG_OR_PORTAL_ERROR", "error": str(error),
                  "mode": "READ_ONLY", "crm_writes": "NO"}
    print("SERVICEIT_BITRIX_FIRST_SOURCE_PREFLIGHT=" +
          json.dumps(report, ensure_ascii=True))
    if report["status"] != "ISOLATED_READ_ACCESS_CONFIRMED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
