"""Live READ-ONLY acceptance for universal ServiceIT stages 3 and 4.

Only the corporate Bitrix24 portal and CRM type 1062/category 21.
Read metadata and count future-only items without identities/contents.
Never call tasks.task.add, crm.item.update or any customer-contact action.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_poll_fallback import discover, after_activation, PollBlocked
from .serviceit_stage3_all_new_task_automation import CUTOVER
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

_METHODS = frozenset({
    "crm.type.getByEntityTypeId", "crm.category.get",
    "tasks.task.getFields", "crm.enum.ownertype",
    "crm.item.fields", "crm.item.list",
})


class ReadinessBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ReadinessBlocked(code)


def args_ok(method, params):
    if method == "crm.type.getByEntityTypeId":
        return params == {"entityTypeId": 1062}
    if method == "crm.category.get":
        return params == {"entityTypeId": 1062, "id": 21}
    if method in ("tasks.task.getFields", "crm.enum.ownertype"):
        return params == {}
    if method == "crm.item.fields":
        return params == {"entityTypeId": 1062, "useOriginalUfNames": "Y"}
    if method == "crm.item.list":
        return (type(params) is dict
                and params.get("entityTypeId") == 1062
                and params.get("useOriginalUfNames") == "N"
                and type(params.get("filter")) is dict
                and set(params["filter"]) == {"categoryId", ">=createdTime"}
                and params["filter"]["categoryId"] == 21
                and params.get("order") == {"id": "ASC"}
                and params.get("select") == ["id", "categoryId", "createdTime"]
                and params.get("start") in (0, 50, 100, 150))
    return False


def read(client, base, method, params):
    need(method in _METHODS and args_ok(method, params),
         "READONLY_METHOD_OR_SCOPE_FORBIDDEN")
    try:
        response = client.post(base + "/" + method + ".json", json=params,
                               timeout=22, follow_redirects=False)
        need(response.status_code == 200
             and 0 < len(response.content) <= 512_000,
             "REST_SCOPE_OR_HTTP")
        payload = response.json()
        need(type(payload) is dict and "error" not in payload
             and "result" in payload, "REST_RESULT_UNVERIFIED")
        result = payload["result"]
        if method == "crm.item.list":
            need(type(result) is dict, "LIST_RESULT_NOT_OBJECT")
            result = dict(result)
            for name in ("next", "total"):
                if name in payload:
                    need(name not in result or result[name] == payload[name],
                         "LIST_PAGINATION_CONFLICT")
                    result[name] = payload[name]
        return result
    except ReadinessBlocked:
        raise
    except (ValueError, TypeError, httpx.HTTPError):
        raise ReadinessBlocked("REST_TRANSPORT_OR_JSON_UNVERIFIED") from None


def inspect(client, webhook, env, *, now=None):
    now = now or datetime.now(timezone.utc)
    need(type(now) is datetime and now.tzinfo is not None,
         "CLOCK_NOT_UTC_AWARE")
    now = now.astimezone(timezone.utc)
    base = validate_webhook(webhook)
    need(base.startswith("https://serviseit.bitrix24.ru/rest/"),
         "NOT_CORPORATE_SERVICEIT")
    typ = read(client, base, "crm.type.getByEntityTypeId",
               {"entityTypeId": 1062})
    need(type(typ) is dict and type(typ.get("type")) is dict
         and str(typ["type"].get("id")) == "13"
         and str(typ["type"].get("entityTypeId")) == "1062",
         "SERVICEIT_SMART_PROCESS_IDENTITY_MISMATCH")
    settings = typ["type"]
    task_enabled = settings.get("isUseInUserfieldEnabled") in (True, "Y", "true")
    linked = settings.get("linkedUserFields")
    task_linked = (type(linked) is dict
                   and linked.get("TASKS_TASK|UF_CRM_TASK")
                   in (True, "Y", "true", "1"))
    category = read(client, base, "crm.category.get",
                    {"entityTypeId": 1062, "id": 21})
    need(type(category) is dict and type(category.get("category")) is dict
         and str(category["category"].get("id")) == "21"
         and str(category["category"].get("entityTypeId")) == "1062",
         "SERVICEIT_CATEGORY_MISMATCH")
    tasks = read(client, base, "tasks.task.getFields", {})
    need(type(tasks) is dict and type(tasks.get("fields")) is dict,
         "TASK_FIELDS_NOT_VISIBLE")
    fields = tasks["fields"]
    task_fields_ok = (all(type(fields.get(k)) is dict
                          for k in ("TITLE", "DESCRIPTION", "RESPONSIBLE_ID",
                                    "UF_CRM_TASK"))
                      and fields["UF_CRM_TASK"].get("type") == "crm")
    owner_codes = read(client, base, "crm.enum.ownertype", {})
    need(type(owner_codes) is list and len(owner_codes) <= 500,
         "OWNER_TYPES_SHAPE_INVALID")
    matches = [x for x in owner_codes if type(x) is dict
               and str(x.get("ID")) == "1062"
               and re.fullmatch(r"T[0-9a-f]{1,8}",
                                str(x.get("SYMBOL_CODE_SHORT", "")))]
    need(len(matches) == 1, "OWNER_TYPE_NOT_UNIQUE")
    crm_fields = read(client, base, "crm.item.fields",
                      {"entityTypeId": 1062, "useOriginalUfNames": "Y"})
    need(type(crm_fields) is dict and type(crm_fields.get("fields")) is dict,
         "CRM_FIELD_CATALOG_INVALID")
    field_catalog = crm_fields["fields"]
    score_fields = (SCORE_FIELD, VERSION_FIELD, BADGE_FIELD)
    score_contract = {
        code: (type(field_catalog.get(code)) is dict
               and field_catalog[code].get("type")
               == ("integer" if code == SCORE_FIELD else "string")
               and field_catalog[code].get("isReadOnly") is False
               and field_catalog[code].get("isMultiple") in (None, False))
        for code in score_fields
    }
    cutoff = max(CUTOVER, after_activation(env, now))
    cards = discover(lambda method, args: read(client, base, method, args),
                     cutoff, now)
    return {
        "status": "SERVICEIT_STAGE34_READONLY_READY",
        "scope": "serviseit.bitrix24.ru:1062/21",
        "task_link_configured": task_enabled and task_linked,
        "task_fields_visible": task_fields_ok,
        "task_owner_type_visible": True,
        "can_create_task_scope_confirmed": False,
        "score_fields_readonly_schema": {
            "score_valid": score_contract[SCORE_FIELD],
            "version_valid": score_contract[VERSION_FIELD],
            "badge_valid": score_contract[BADGE_FIELD],
        },
        "independent_nine_gate_verifier_connected_by_this_check": False,
        "eligible_future_card_count": len(cards),
        "task_add_calls": 0,
        "crm_writes": 0,
        "external_contacts": 0,
    }


def main():
    env = dict(os.environ)
    webhook = env.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            summary = inspect(client, webhook, env)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except (ReadinessBlocked, PollBlocked, ValueError, TypeError,
            httpx.HTTPError):
        print(json.dumps({
            "status": "SERVICEIT_STAGE34_READONLY_BLOCKED",
            "reason": "PORTAL_SCHEMA_OR_WINDOW_UNVERIFIED",
            "crm_writes": 0,
            "task_add_calls": 0,
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
