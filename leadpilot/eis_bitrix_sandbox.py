"""Read-only Bitrix24 lead-review smart-process acceptance.

This is *not* a live lead sender. Standard Lead entityTypeId=1 is not isolated
by deal-category pipeline; test intake must be in an admin-approved smart
process with a separate category and no automations. No write REST methods.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

import httpx

from .bitrix_sandbox import validate_webhook, PreflightError

ALLOW_READ = frozenset({"crm.category.list", "crm.item.fields", "crm.item.list"})
REQUIRED_FIELDS = {"title", "categoryId", "stageId"}


@dataclass(frozen=True)
class EISSmartProcessConfig:
    webhook: str
    entity_type_id: int
    category_id: int
    category_name: str
    buyer_inn_field: str

    def __post_init__(self):
        validate_webhook(self.webhook)
        if (type(self.entity_type_id) is not int or self.entity_type_id < 128 or
                type(self.category_id) is not int or self.category_id <= 0):
            raise PreflightError("Requires a positive isolated smart-process ID and nondefault category")
        if (not isinstance(self.category_name, str) or
                not self.category_name.startswith("LeadGen AI") or
                not 9 <= len(self.category_name) <= 90):
            raise PreflightError("Admin-approved LeadGen AI test category name required")
        if (not isinstance(self.buyer_inn_field, str) or
                not re.fullmatch(r"(?:ufCrm[0-9]+_[0-9]+|UF_CRM_[A-Z0-9_]+)", self.buyer_inn_field)):
            raise PreflightError("Real custom INN field mapping required; never guess CRM fields")


def _call(client: httpx.Client, base: str, method: str, payload: dict) -> object:
    if method not in ALLOW_READ:
        raise PreflightError("Bitrix method forbidden in read-only EIS preflight")
    try:
        response = client.post(base + "/" + method, json=payload, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data or "result" not in data:
            raise PreflightError("Bitrix API denied request or returned invalid response")
        return data["result"]
    except (httpx.HTTPError, ValueError):
        raise PreflightError("Bitrix HTTP or response error") from None


def preflight(config: EISSmartProcessConfig,
              client: httpx.Client | None = None) -> dict:
    """Inspect only category, fields and isolated test-item counts (no contents)."""
    base = validate_webhook(config.webhook)
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    state = {
        "mode": "EIS_BITRIX_READ_ONLY", "entity_type_id": config.entity_type_id,
        "category_id": config.category_id, "status": "NOT_VERIFIED",
        "category_confirmed": False, "inn_field_found": False,
        "isolated_item_count": None, "existing_clients_checked": False,
        "company_inn_duplicates_checked": False,
        "automations_off_verified": False,
        "manager_approved": False, "crm_ready": False,
        "write_authorized": False, "crm_writes": "NO", "outbound_messages": "NO",
        "methods_called": [],
    }
    try:
        cat = _call(client, base, "crm.category.list", {"entityTypeId": config.entity_type_id})
        state["methods_called"].append("crm.category.list")
        if not isinstance(cat, dict) or not isinstance(cat.get("categories"), list):
            raise PreflightError("Unknown Bitrix category response")
        match = [x for x in cat["categories"] if isinstance(x, dict)
                 and x.get("id") == config.category_id
                 and x.get("name") == config.category_name]
        if len(match) != 1:
            state["status"] = "TEST_CATEGORY_MISSING_OR_UNVERIFIED"
            return state
        state["category_confirmed"] = True
        fields = _call(client, base, "crm.item.fields", {"entityTypeId": config.entity_type_id})
        state["methods_called"].append("crm.item.fields")
        if not isinstance(fields, dict) or not isinstance(fields.get("fields"), dict):
            raise PreflightError("Unknown Bitrix item fields")
        actual = fields["fields"]
        state["inn_field_found"] = config.buyer_inn_field in actual
        if not REQUIRED_FIELDS.issubset(actual) or not state["inn_field_found"]:
            state["status"] = "REQUIRED_CRM_FIELDS_MISSING"
            return state
        # Check category read permission without copying customer records to output.
        data = _call(client, base, "crm.item.list", {
            "entityTypeId": config.entity_type_id,
            "filter": {"categoryId": config.category_id},
            "select": ["id"], "start": 0,
        })
        state["methods_called"].append("crm.item.list")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise PreflightError("Unknown Bitrix item list response")
        state["isolated_item_count"] = len(data["items"])
        state["status"] = "READ_OK_WRITE_AND_DUPLICATE_GATES_CLOSED"
        return state
    except PreflightError:
        state["status"] = "READ_FAILED_OR_PERMISSION_DENIED"
        return state
    finally:
        if own:
            client.close()


def main() -> None:
    """Explicit read-only preflight. Webhook stays in local environment, never CLI."""
    import argparse
    import os
    p = argparse.ArgumentParser(description="EIS manager smart-process Bitrix read-only preflight")
    p.add_argument("--entity-type-id", type=int, required=True)
    p.add_argument("--category-id", type=int, required=True)
    p.add_argument("--category-name", required=True)
    p.add_argument("--buyer-inn-field", required=True)
    args = p.parse_args()
    try:
        cfg = EISSmartProcessConfig(
            webhook=os.environ.get("BITRIX24_TEST_WEBHOOK", ""),
            entity_type_id=args.entity_type_id,
            category_id=args.category_id,
            category_name=args.category_name,
            buyer_inn_field=args.buyer_inn_field,
        )
        result = preflight(cfg)
    except (PreflightError, ValueError):
        print("EIS_BITRIX_PREFLIGHT=BLOCKED; CONFIG_OR_PERMISSION_ERROR; CRM_WRITES=NO")
        raise SystemExit(2)
    print("EIS_BITRIX_PREFLIGHT=" + json.dumps(result, ensure_ascii=True))
    if result["status"] != "READ_OK_WRITE_AND_DUPLICATE_GATES_CLOSED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
