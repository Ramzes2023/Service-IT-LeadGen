"""Read-only corporate task-scoping preflight for manager task automation.

NO tasks.task.add, task mutation, user guesses, customer reads or credentials
in the output. This probe uses the existing Bitrix REST secret only to verify
whether the portal's real task API and smart-process task binding are usable.
It does NOT authorize a marketing contact or create a task.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .bitrix_metadata_audit import validate_webhook

_ALLOWED = {"tasks.task.getFields", "crm.type.getByEntityTypeId", "crm.enum.ownertype"}
_MAX_RESPONSE = 256_000


class TaskGateBlocked(ValueError):
    pass


def _require(ok: bool, reason: str):
    if not ok:
        raise TaskGateBlocked(reason)


def _query(client: httpx.Client, base: str, method: str, args: dict):
    _require(method in _ALLOWED, "TASK_PROBE_METHOD_NOT_READONLY")
    _require((args == {} if method != "crm.type.getByEntityTypeId"
             else args == {"entityTypeId":1062}), "TASK_PROBE_ARGS_INVALID")
    try:
        response = client.post(base + "/" + method + ".json",
                               json=args,timeout=18,follow_redirects=False)
        _require(response.status_code == 200 and len(response.content) <= _MAX_RESPONSE,
                 "TASK_PROBE_METHOD_UNAVAILABLE_OR_SCOPE")
        data=response.json()
        _require(type(data) is dict and "error" not in data
                 and isinstance(data.get("result"), (list,dict)),
                 "TASK_PROBE_RESPONSE_INVALID")
        return data["result"]
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise TaskGateBlocked("TASK_PROBE_TRANSPORT_OR_RESPONSE") from None


def inspect(client: httpx.Client, base: str) -> dict:
    f = _query(client,base,"tasks.task.getFields",{})
    _require(type(f) is dict and type(f.get("fields")) is dict,
             "TASK_FIELDS_UNAVAILABLE")
    fields=f["fields"]
    for key in ("TITLE","RESPONSIBLE_ID","DESCRIPTION","UF_CRM_TASK"):
        _require(type(fields.get(key)) is dict, "TASK_FIELDS_REQUIRED_MISSING")
    _require(fields["UF_CRM_TASK"].get("type") == "crm",
             "TASK_CRM_BINDING_FIELD_TYPE_UNEXPECTED")
    typ = _query(client,base,"crm.type.getByEntityTypeId",{"entityTypeId":1062})
    _require(type(typ) is dict and type(typ.get("type")) is dict,
             "SMART_PROCESS_SETTINGS_UNAVAILABLE")
    typ=typ["type"]
    _require(str(typ.get("entityTypeId")) == "1062",
             "SMART_PROCESS_TYPE_MISMATCH")
    enabled = typ.get("isUseInUserfieldEnabled") in (True,"Y","true")
    linked=typ.get("linkedUserFields")
    binding = (type(linked) is dict and
               linked.get("TASKS_TASK|UF_CRM_TASK") in (True,"Y","true","1"))
    owners = _query(client,base,"crm.enum.ownertype",{})
    _require(type(owners) is list and len(owners) <= 500,
             "CRM_OWNER_TYPES_UNKNOWN")
    matches=[o for o in owners if type(o) is dict
             and str(o.get("ID")) == "1062"]
    _require(len(matches) == 1
             and isinstance(matches[0].get("SYMBOL_CODE_SHORT"),str)
             and re.fullmatch(r"T[0-9a-f]{1,8}",
                              matches[0]["SYMBOL_CODE_SHORT"]),
             "CRM_TASK_OWNER_SYMBOL_UNVERIFIED")
    return {
      "status": "TASK_CREATE_READONLY_PREFLIGHT",
      "task_scope": "READONLY_PRESENT",
      "smart_process_1062_task_link": "VERIFIED" if enabled and binding else "NOT_CONFIGURED",
      "owner_type_code": "EXISTS_VERIFIED_NOT_EXPOSED",
      "manager_user_identity": "NOT_VERIFIED",
      "task_dedupe": "NEEDS_SEPARATE_EXACT_READONLY_PROOF",
      "task_write_authorized": False,
      "task_created": False,
      "crm_writes": 0,
    }


def main() -> int:
    hook=os.environ.pop("SERVICEIT_BITRIX_WEBHOOK","")
    try:
        base=validate_webhook(hook)
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            status=inspect(client,base)
        ok=True
    except (TaskGateBlocked,ValueError):
        status={"status":"TASK_CREATE_READONLY_BLOCKED",
                "reason":"MISSING_TASK_SCOPE_OR_BINDING_OR_REST_ERROR",
                "task_created":False,"crm_writes":0}
        ok=False
    print(json.dumps(status,sort_keys=True))
    return 0 if ok else 2


if __name__=="__main__":
    raise SystemExit(main())
