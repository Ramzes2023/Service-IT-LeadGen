"""Owner-triggered, READ-ONLY match of an existing GitHub webhook secret.

Never accepts or prints raw URLs, auth codes, employee data or CRM records.
Checks a SHA-256 of a *high-entropy* user-provided webhook URL supplied
in an owner-only Issue #148 comment, then verifies scoped REST rights.
This checker CANNOT prove tasks.task.add or crm.item.update write permission;
no write method is ever invoked. No existing workflows are modified.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

import httpx
from .bitrix_metadata_audit import validate_webhook

OWNER = "Ramzes2023"
REPO = "Ramzes2023/Service-IT-LeadGen"
MARKER = "SERVICEIT_COMPARE_WEBHOOK_SHA256_V1:"
ALL_SCOPES = frozenset(("crm", "task", "user_brief"))
READS = frozenset((
    "profile", "scope", "crm.type.getByEntityTypeId", "crm.category.get",
    "crm.item.fields", "crm.item.list", "tasks.task.getFields",
    "tasks.task.list",
))
FIELDS = (
    "UF_CRM_13_SVCIT_LEAD_SCORE", "UF_CRM_13_SVCIT_SCORE_VERSION",
    "UF_CRM_13_SVCIT_AI_SCORE_BADGE",
    "UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION",
)

class WebhookReadOnlyBlocked(ValueError):
    pass

def need(ok, code):
    if not ok:
        raise WebhookReadOnlyBlocked(code)

def expected_from_event(env):
    need(env.get("GITHUB_REPOSITORY") == REPO
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "issue_comment"
         and env.get("GITHUB_ACTOR") == OWNER,
         "OWNER_GITHUB_IDENTITY_REQUIRED")
    path = Path(env.get("GITHUB_EVENT_PATH") or "/dev/null")
    need(path.is_file() and 0 < path.stat().st_size <= 256000,
         "OWNER_EVENT_FILE_INVALID")
    try:
        event=json.loads(path.read_text("utf8"))
    except (OSError, UnicodeError, ValueError):
        raise WebhookReadOnlyBlocked("OWNER_EVENT_JSON_INVALID") from None
    need(type(event) is dict and event.get("action") == "created"
         and type(event.get("repository")) is dict
         and event["repository"].get("full_name") == REPO
         and type(event.get("issue")) is dict
         and event["issue"].get("number") == 148
         and event["issue"].get("pull_request") is None
         and type(event.get("sender")) is dict
         and event["sender"].get("login") == OWNER
         and type(event.get("comment")) is dict
         and type(event["comment"].get("user")) is dict
         and event["comment"]["user"].get("login") == OWNER,
         "OWNER_COMMENT_NOT_AUTHORIZED")
    marker=event["comment"].get("body")
    need(type(marker) is str and re.fullmatch(MARKER+r"[0-9a-f]{64}",marker),
         "WEBHOOK_SHA256_MARKER_INVALID")
    return marker[len(MARKER):]

def read(client, base, method, params):
    need(method in READS and type(params) is dict,
         "READ_ONLY_API_METHOD_REQUIRED")
    try:
        res=client.post(base+"/"+method+".json",json=params,
                        follow_redirects=False,timeout=20)
        need(res.status_code==200 and 0 < len(res.content) <= 512000,
             "BITRIX_REST_SCOPE_OR_HTTP_INVALID")
        body=res.json()
        need(type(body) is dict and "error" not in body
             and "result" in body, "BITRIX_REST_RESPONSE_INVALID")
        return body["result"]
    except WebhookReadOnlyBlocked:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise WebhookReadOnlyBlocked("BITRIX_REST_TRANSPORT_UNVERIFIED") from None

def check(client, env):
    expected=expected_from_event(env)
    url=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    have=sha256(url.encode("utf8")).hexdigest()
    if not hmac.compare_digest(have,expected):
        return {
            "status": "GITHUB_SECRET_DOES_NOT_MATCH_SUPPLIED_WEBHOOK",
            "exact_webhook_match": False, "crm_writes": 0,
            "task_add_calls": 0, "customer_contacts": 0,
        }
    need(re.fullmatch(r"https://serviseit\.bitrix24\.ru/rest/1615/[A-Za-z0-9_-]{6,}",
                      url) is not None, "EXPECTED_SERVICEIT_WEBHOOK_IDENTITY_INVALID")
    user=read(client,url,"profile",{})
    need(type(user) is dict and str(user.get("ID"))=="1615"
         and user.get("ADMIN") is False,"PORTAL_USER_1615_NOT_CONFIRMED")
    scope=read(client,url,"scope",{})
    need(type(scope) is list and ALL_SCOPES.issubset(set(scope)),
         "REQUIRED_CRM_TASK_USER_SCOPES_MISSING")
    typ=read(client,url,"crm.type.getByEntityTypeId",{"entityTypeId":1062})
    need(type(typ) is dict and type(typ.get("type")) is dict,
         "SVCIT_SMART_PROCESS_METADATA_INVALID")
    t=typ["type"]
    need(str(t.get("entityTypeId"))=="1062"
         and str(t.get("id"))=="13"
         and t.get("isUseInUserfieldEnabled") in ("Y",True)
         and type(t.get("linkedUserFields")) is dict
         and t["linkedUserFields"].get("TASKS_TASK|UF_CRM_TASK") in ("Y",True),
         "SVCIT_SMART_PROCESS_TASK_LINK_INVALID")
    cat=read(client,url,"crm.category.get",
             {"entityTypeId":1062,"id":21})
    need(type(cat) is dict and type(cat.get("category")) is dict
         and str(cat["category"].get("id"))=="21"
         and str(cat["category"].get("entityTypeId"))=="1062",
         "SVCIT_CATEGORY_21_NOT_ACCESSIBLE")
    fields=read(client,url,"crm.item.fields",{"entityTypeId":1062,
                                               "useOriginalUfNames":"Y"})
    need(type(fields) is dict and type(fields.get("fields")) is dict
         and all(type(fields["fields"].get(f)) is dict for f in FIELDS),
         "SVCIT_AI_FIELDS_MISSING")
    wanted={"UF_CRM_13_SVCIT_LEAD_SCORE":"integer"}
    need(all(fields["fields"][k].get("isReadOnly") is False
             and fields["fields"][k].get("type")==wanted.get(k,"string")
             for k in FIELDS),"SVCIT_AI_FIELD_TYPES_OR_FLAGS_INVALID")
    tf=read(client,url,"tasks.task.getFields",{})
    need(type(tf) is dict and type(tf.get("fields")) is dict
         and all(type(tf["fields"].get(k)) is dict for k in
                 ("TITLE","DESCRIPTION","RESPONSIBLE_ID","UF_CRM_TASK"))
         and tf["fields"]["UF_CRM_TASK"].get("type")=="crm",
         "TASK_FIELDS_NOT_ACCESSIBLE")
    empty=read(client,url,"tasks.task.list",
               {"filter":{"ID":0},"select":["ID"],"start":0})
    need(type(empty) is dict and type(empty.get("tasks")) is list
         and len(empty["tasks"]) == 0, "TASK_LIST_READ_NOT_CONFIRMED")
    listing=read(client,url,"crm.item.list",
                 {"entityTypeId":1062,"filter":{"categoryId":21},
                  "select":["id","categoryId"],"start":0})
    need(type(listing) is dict and type(listing.get("items")) is list
         and all(str(item.get("categoryId"))=="21"
                 for item in listing["items"]),
         "EXACT_CRM_LIST_SCOPE_UNVERIFIED")
    return {
        "status":"SAME_WEBHOOK_CONFIGURED_IN_GITHUB_READONLY_PASS",
        "exact_webhook_match":True,
        "corporate_user_1615_active_identity":True,
        "scope_crm_task_user_brief":True,
        "smart_process_1062_category_21":True,
        "task_read_scope":True,
        "all_four_fields_visible":True,
        "write_permissions_tested":False,
        "task_add_calls":0,"crm_writes":0,"customer_contacts":0,
    }

def main():
    env=dict(os.environ)
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            output=check(client,env)
        print(json.dumps(output,sort_keys=True))
        return 0 if output["exact_webhook_match"] else 2
    except Exception:
        # Never include exception details or webhook strings in logs.
        print(json.dumps({"status":"WEBHOOK_READONLY_SAFE_HOLD",
                          "exact_webhook_match":None,
                          "task_add_calls":0,"crm_writes":0,
                          "customer_contacts":0}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
