"""Stage 3: verify *exact* corporate Bitrix process/funnel & caller admin rights.

Read-only preflight for an owner-approved change restricted to
serviseit.bitrix24.ru, entityTypeId=1062, categoryId=21, scope C.
No crm.item.details.configuration.set, tasks.task.add, cross-category lookups,
personnel identifiers, layout contents or webhook tokens in output.
"""
from __future__ import annotations

import json
import os
import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_manager_layout_safe import plan_shared_manager_layout, ManagerLayoutBlocked

_ALLOWED = {
    "user.admin": {},
    "crm.type.getByEntityTypeId": {"entityTypeId": 1062},
    "crm.category.get": {"entityTypeId": 1062, "id": 21},
    "crm.item.details.configuration.get": {
        "entityTypeId": 1062, "scope": "C", "extras": {"categoryId": 21}},
    "crm.item.fields": {"entityTypeId": 1062, "useOriginalUfNames": "Y"},
}
_MAX_BODY = 400_000


class LayoutScopeBlocked(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise LayoutScopeBlocked(code)


def _read(client, base, method, args):
    _require(method in _ALLOWED and args == _ALLOWED.get(method),
             "METHOD_OR_SCOPE_NOT_ALLOWED")
    try:
        response = client.post(base + "/" + method + ".json", json=args,
                               timeout=20, follow_redirects=False)
        _require(response.status_code == 200 and 0 < len(response.content) <= _MAX_BODY,
                 "REST_SCOPE_OR_HTTP_UNVERIFIED")
        payload = response.json()
        _require(type(payload) is dict and "error" not in payload
                 and "result" in payload, "REST_RESPONSE_UNVERIFIED")
        return payload["result"]
    except LayoutScopeBlocked:
        raise
    except (ValueError, KeyError, TypeError, httpx.HTTPError):
        raise LayoutScopeBlocked("REST_TRANSPORT_OR_FORMAT") from None


def inspect(client, webhook):
    base = validate_webhook(webhook)
    _require(base.startswith("https://serviseit.bitrix24.ru/rest/"),
             "PORTAL_OUT_OF_SCOPE")
    owner_admin = _read(client, base, "user.admin", {})
    _require(type(owner_admin) is bool, "ADMIN_AUTHORITY_UNKNOWN")
    typ_result = _read(client, base, "crm.type.getByEntityTypeId",
                       {"entityTypeId": 1062})
    _require(type(typ_result) is dict and type(typ_result.get("type")) is dict,
             "SMART_PROCESS_UNVERIFIED")
    typ = typ_result["type"]
    _require(str(typ.get("entityTypeId")) == "1062"
             and str(typ.get("id")) == "13",
             "SMART_PROCESS_NOT_SERVICEIT_13")
    cat_result = _read(client, base, "crm.category.get",
                       {"entityTypeId": 1062, "id": 21})
    _require(type(cat_result) is dict and type(cat_result.get("category")) is dict,
             "FUNNEL_UNVERIFIED")
    cat = cat_result["category"]
    _require(str(cat.get("entityTypeId")) == "1062"
             and str(cat.get("id")) == "21"
             and isinstance(cat.get("name"), str)
             and cat["name"].strip(), "FUNNEL_ID_MISMATCH")
    current = _read(client, base, "crm.item.details.configuration.get",
                    {"entityTypeId": 1062, "scope": "C",
                     "extras": {"categoryId": 21}})
    available_result = _read(client, base, "crm.item.fields",
                             {"entityTypeId": 1062, "useOriginalUfNames": "Y"})
    _require(type(available_result) is dict
             and type(available_result.get("fields")) is dict,
             "FIELDS_UNVERIFIED")
    try:
        plan = plan_shared_manager_layout(current, available_result["fields"])
    except ManagerLayoutBlocked:
        raise LayoutScopeBlocked("LAYOUT_PLAN_NOT_SAFE") from None
    return {
        "status": "SERVICEIT_1062_21_SHARED_LAYOUT_SCOPE_READONLY_PASS",
        "portal_scoped": True,
        "entity_type_id": 1062,
        "category_id": 21,
        "shared_scope": "C",
        "only_one_funnel_targeted": True,
        "connected_webhook_user_is_portal_admin": owner_admin,
        "proposed_missing_fields": plan["added_count"],
        "existing_layout_preserved_by_plan": True,
        "admin_approval_from_company_recorded": False,
        "shared_layout_write_performed": False,
        "kanban_tile_verified_in_ui": False,
        "crm_writes": 0,
    }


def main():
    hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = inspect(client, hook)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (LayoutScopeBlocked, ValueError):
        print(json.dumps({"status":"SERVICEIT_LAYOUT_SCOPE_READONLY_BLOCKED",
                          "reason":"PORTAL_RIGHTS_CATEGORY_OR_LAYOUT_UNVERIFIED",
                          "shared_layout_write_performed": False,
                          "crm_writes":0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
