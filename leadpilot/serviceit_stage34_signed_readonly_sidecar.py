"""Stage 3/4 sidecar: signed new-card event -> read-only manager/task audit.

Runs independently beside the existing nine-field AI worker. It NEVER
creates or edits CRM items or tasks and cannot authorize marketing contact.
A non-admin webhook sees only its accessible tasks: zero visible tasks does
NOT prove full global task deduplication. Formal Lead Score requires a
separate independently signed nine-gate corporate verification receipt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import verify_event, EventCloudBlocked
from .serviceit_enrichment_worker import normalized, validate_identity, Blocked
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

PORTAL = "serviseit.bitrix24.ru"
_MAX_RESPONSE = 250_000
_MAX_TASKS = 150
_FIELDS = ["ID", "UF_CRM_TASK", "RESPONSIBLE_ID"]
_ALLOWED = frozenset(("crm.item.get", "crm.enum.ownertype", "tasks.task.list", "user.get"))


class ObserverBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ObserverBlocked(code)


def positive(value):
    need(type(value) in (int, str)
         and re.fullmatch(r"[1-9][0-9]{0,9}", str(value)) is not None,
         "ID_NOT_POSITIVE")
    return int(value)


def request(client, base, method, args):
    need(isinstance(client, httpx.Client) and method in _ALLOWED
         and type(args) is dict, "REST_METHOD_OR_ARGS_FORBIDDEN")
    try:
        res = client.post(base + "/" + method + ".json", json=args,
                          timeout=20, follow_redirects=False)
        need(res.status_code == 200 and 0 < len(res.content) <= _MAX_RESPONSE,
             "REST_HTTP_OR_SCOPE_UNVERIFIED")
        data = res.json()
        need(type(data) is dict and "error" not in data and "result" in data,
             "REST_RESPONSE_UNVERIFIED")
        return data
    except ObserverBlocked:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise ObserverBlocked("REST_TRANSPORT_OR_RESPONSE_UNVERIFIED") from None


def owner_type(client, base):
    data = request(client, base, "crm.enum.ownertype", {})
    values = data["result"]
    need(type(values) is list and len(values) <= 300,
         "OWNER_TYPES_UNVERIFIED")
    matched = [str(v.get("SYMBOL_CODE_SHORT")) for v in values
               if type(v) is dict and str(v.get("ID")) == "1062"]
    need(len(matched) == 1
         and re.fullmatch(r"T[0-9a-f]{1,8}", matched[0]),
         "OWNER_LINK_SYMBOL_UNVERIFIED")
    return matched[0]


def visible_tasks(client, base, binding):
    """Read bounded exact-linked pages. No visibility != no global tasks."""
    need(type(binding) is str
         and re.fullmatch(r"T[0-9a-f]{1,8}_[1-9][0-9]{0,9}", binding),
         "TASK_BINDING_INVALID")
    found = set()
    for offset in (0, 50, 100):
        args = {"filter": {"UF_CRM_TASK": binding},
                "select": _FIELDS, "order": {"ID": "ASC"}, "start": offset}
        data = request(client, base, "tasks.task.list", args)
        result = data["result"]
        need(type(result) is dict and type(result.get("tasks")) is list,
             "TASK_LIST_SHAPE_UNVERIFIED")
        rows = result["tasks"]
        need(len(rows) <= 50 and type(data.get("total")) is int
             and 0 <= data["total"] <= _MAX_TASKS,
             "TASK_LIST_TOTAL_UNVERIFIED")
        total = data["total"]
        for task in rows:
            need(type(task) is dict, "TASK_ROW_UNVERIFIED")
            links = task.get("ufCrmTask", task.get("UF_CRM_TASK"))
            need(type(links) is list and binding in links
                 and len(links) <= 20,
                 "TASK_LINK_NOT_EXACT")
            task_id = positive(task.get("id", task.get("ID")))
            need(task_id not in found, "TASK_DUPLICATE_PAGE")
            found.add(task_id)
        next_start = data.get("next")
        if next_start is None:
            need(len(found) == total,
                 "TASK_LIST_INCOMPLETE_OR_HIDDEN_PAGINATION")
            return len(found)
        need(type(next_start) is int and next_start == offset + 50
             and len(rows) == 50 and total > next_start,
             "TASK_PAGINATION_UNVERIFIED")
    raise ObserverBlocked("TASK_LIST_TOO_MANY_PAGES")


def assigned_employee(client, base, assigned):
    if assigned is None or str(assigned) in ("", "0"):
        return "NO_ASSIGNEE_ON_CARD"
    uid = positive(assigned)
    data = request(client, base, "user.get", {
        "FILTER": {"ID": uid, "ACTIVE": True},
        "select": ["ID", "ACTIVE", "USER_TYPE"], "start": 0,
    })
    users = data["result"]
    need(type(users) is list and len(users) <= 50
         and data.get("next") is None and data.get("total") == len(users),
         "ASSIGNEE_SEARCH_UNVERIFIED")
    matches = [x for x in users if type(x) is dict
               and str(x.get("ID")) == str(uid)
               and x.get("ACTIVE") in (True, "Y")
               and x.get("USER_TYPE") == "employee"]
    return ("ONE_ACTIVE_ASSIGNED_EMPLOYEE_UNAPPROVED"
            if len(matches) == 1 else "ASSIGNEE_NOT_VERIFIED")


def inspect(client, webhook, card_id):
    card_id = positive(card_id)
    need(8 <= card_id <= 2_000_000_000 and card_id not in (3, 7),
         "CARD_HISTORIC_OR_INVALID")
    base = validate_webhook(webhook)
    need(base.startswith("https://" + PORTAL + "/rest/"),
         "CORPORATE_PORTAL_MISMATCH")
    data = request(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y",
    })
    need(type(data["result"]) is dict
         and type(data["result"].get("item")) is dict,
         "CRM_ITEM_RESPONSE_UNVERIFIED")
    item = normalized(data["result"]["item"])
    validate_identity(item, card_id)
    prefix = owner_type(client, base)
    count = visible_tasks(client, base, prefix + "_" + str(card_id))
    employee = assigned_employee(client, base, item.get("assignedById"))
    present_ai = sum(1 for code in AI_FIELDS
                     if type(item.get(code)) is str and bool(item[code].strip()))
    status = item.get(PREFIX + "AI_STATUS")
    # No inference from AI_STATUS, Score, source content or task list to
    # independently verified corporate customer/STOP/legal/marketing clearance.
    return {
        "status": "SERVICEIT_STAGE34_READONLY_EVENT_OBSERVED",
        "scope": "SERVICEIT_1062_21_ONLY",
        "linked_task_count_visible_to_webhook": count,
        "task_visibility": ("SOME_LINKED_TASKS_VISIBLE" if count else
                            "NO_VISIBLE_TASKS_NOT_GLOBAL_DEDUP_PROOF"),
        "global_task_dedup_verified": False,
        "assignee": employee,
        "assignee_authorized_for_new_task": False,
        "ai_fields_present": present_ai,
        "ai_status_present": type(status) is str and bool(status.strip()),
        "formal_score": None,
        "qualification": "HOLD_UNTIL_SIGNED_9_GATE_VERIFICATION",
        "stage3_task_created": False,
        "stage4_score_written": False,
        "crm_writes": 0,
        "outreach": False,
    }


def run(event, env, client):
    need(env.get("GITHUB_REPOSITORY") == "Ramzes2023/Service-IT-LeadGen"
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "repository_dispatch",
         "GITHUB_EVENT_SCOPE_NOT_ALLOWED")
    # Validate HMAC and timestamp BEFORE any corporate REST traffic.
    card_id = verify_event(event, env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY",""))
    return inspect(client, env.get("SERVICEIT_BITRIX_WEBHOOK",""), card_id)


def main():
    env = dict(os.environ)
    try:
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        need(path.is_file() and 0 < path.stat().st_size <= 256000,
             "GITHUB_EVENT_FILE_UNVERIFIED")
        event = json.loads(path.read_text("utf-8"))
        need(type(event) is dict, "GITHUB_EVENT_DATA_UNVERIFIED")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = run(event, env, client)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ObserverBlocked, EventCloudBlocked, Blocked, ValueError, TypeError, OSError,
            KeyError, httpx.HTTPError):
        # Never report untrusted REST text, event contents, card ID or tokens.
        print(json.dumps({"status":"SERVICEIT_STAGE34_READONLY_BLOCKED",
                          "reason":"EVENT_PERMISSIONS_OR_REST_UNVERIFIED",
                          "stage3_task_created":False,
                          "stage4_score_written":False,
                          "crm_writes":0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
