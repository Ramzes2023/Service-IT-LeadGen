"""Read-only stage 3 acceptance: manager identity candidate and exact task link.

Only Bitrix REST reads. A matching person is a CANDIDATE, never authorization
to assign tasks. Historical DEMO item #3 is used strictly as a positive link
control, never modified. Logs expose no names, raw task data or user IDs.
"""
from __future__ import annotations

import json
import os
import re
import httpx

from .bitrix_metadata_audit import validate_webhook

_PORTAL = "serviseit.bitrix24.ru"
_ALLOWED = frozenset({"user.get", "crm.enum.ownertype", "tasks.task.list"})
_MAX_BYTES = 256_000


class ManagerPreflightBlocked(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise ManagerPreflightBlocked(code)


def _read(client, base, method, params):
    _need(method in _ALLOWED, "METHOD_NOT_READONLY")
    expected = {
        "user.get": {"FILTER": {"NAME_SEARCH": "Гиреев", "ACTIVE": True}},
        "crm.enum.ownertype": {},
    }
    if method != "tasks.task.list":
        _need(params == expected[method], "METHOD_ARGS_NOT_APPROVED")
    else:
        _need(type(params) is dict
              and params.get("order") == {"ID": "ASC"}
              and params.get("select") == ["ID", "UF_CRM_TASK", "RESPONSIBLE_ID"]
              and params.get("start") == 0
              and type(params.get("filter")) is dict
              and len(params["filter"]) == 1
              and re.fullmatch(r"T[0-9a-f]{1,8}_3",
                               str(params["filter"].get("UF_CRM_TASK", ""))),
              "TASK_FILTER_NOT_EXACT_DEMO")
    try:
        res = client.post(base + "/" + method + ".json", json=params,
                          timeout=18, follow_redirects=False)
        _need(res.status_code == 200 and len(res.content) <= _MAX_BYTES,
              "REST_HTTP_OR_SCOPE")
        payload = res.json()
        _need(type(payload) is dict and "error" not in payload
              and "result" in payload, "REST_RESPONSE_INVALID")
        return payload
    except ManagerPreflightBlocked:
        raise
    except (ValueError, TypeError, KeyError, httpx.HTTPError):
        raise ManagerPreflightBlocked("REST_RESPONSE_OR_TRANSPORT") from None


def _items(body, key=None):
    value = body["result"] if key is None else body["result"].get(key)
    _need(type(value) is list and len(value) <= 50, "LIST_SHAPE_INVALID")
    total = body.get("total")
    _need((total is None or type(total) is int and total == len(value))
          and body.get("next") is None, "LIST_PAGINATION_UNVERIFIED")
    return value


def inspect(client, base):
    user_response = _read(client, base, "user.get",
                          {"FILTER": {"NAME_SEARCH": "Гиреев", "ACTIVE": True}})
    users = _items(user_response)
    # A name match is NEVER proof that this employee is approved as assignee.
    matches = [u for u in users if type(u) is dict
               and str(u.get("NAME", "")).strip().casefold() == "микаил"
               and str(u.get("LAST_NAME", "")).strip().casefold() == "гиреев"
               and u.get("ACTIVE") in (True, "Y")
               and u.get("USER_TYPE", "employee") == "employee"
               and re.fullmatch(r"[1-9][0-9]{0,9}", str(u.get("ID", "")))]
    manager_state = ("ONE_DIRECTORY_CANDIDATE_UNAPPROVED"
                     if len(matches) == 1 else "IDENTITY_NOT_VERIFIED")
    owners = _items(_read(client, base, "crm.enum.ownertype", {}))
    symbols = [str(x.get("SYMBOL_CODE_SHORT")) for x in owners
               if type(x) is dict and str(x.get("ID")) == "1062"]
    _need(len(symbols) == 1 and re.fullmatch(r"T[0-9a-f]{1,8}", symbols[0]),
          "TASK_OWNER_PREFIX_NOT_VERIFIED")
    binding = symbols[0] + "_3"
    task_response = _read(client, base, "tasks.task.list", {
        "filter": {"UF_CRM_TASK": binding},
        "select": ["ID", "UF_CRM_TASK", "RESPONSIBLE_ID"],
        "order": {"ID": "ASC"}, "start": 0,
    })
    tasks = _items(task_response, "tasks")
    # Never trust a filtered list blindly; verify the linked CRM value again.
    for task in tasks:
        _need(type(task) is dict, "TASK_ITEM_SHAPE_INVALID")
        linked = task.get("ufCrmTask", task.get("UF_CRM_TASK"))
        _need(type(linked) is list and binding in linked
              and re.fullmatch(r"[1-9][0-9]{0,9}",
                               str(task.get("id", task.get("ID", "")))),
              "TASK_LINK_READBACK_UNVERIFIED")
    return {
        "status": "MANAGER_TASK_READONLY_ACCEPTANCE",
        "manager_directory_candidate": manager_state,
        "manager_candidate_count": len(matches),
        "manager_id_authorized": False,
        "task_link_demo_lookup": "FOUND" if tasks else "NONE_VISIBLE_UNVERIFIED",
        "task_link_demo_count": len(tasks),
        "task_dedupe_for_new_cards": "NOT_PROVEN",
        "task_write_authorized": False,
        "task_created": False,
        "crm_writes": 0,
    }


def main():
    hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    try:
        base = validate_webhook(hook)
        _need(base.startswith("https://" + _PORTAL + "/rest/"),
              "CORPORATE_PORTAL_MISMATCH")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = inspect(client, base)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ManagerPreflightBlocked, ValueError):
        print(json.dumps({"status": "MANAGER_TASK_READONLY_BLOCKED",
                          "reason": "SCOPE_IDENTITY_OR_RESPONSE_UNVERIFIED",
                          "task_created": False, "crm_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
