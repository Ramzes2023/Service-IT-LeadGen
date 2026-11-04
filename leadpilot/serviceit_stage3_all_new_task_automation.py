"""Automatic INTERNAL review tasks for every *future* ServiceIT 1062/21 card.

Uses BOTH signed new-item events and a bounded scheduled catch-up scan.
No administrator layout access, external contact, qualification claims, card
mutation, source ingestion or prior-record changes. The assignee is the
verified active employee already assigned to the specific new CRM card.

A durable remote encrypted one-use reservation is created before ANY task.add.
An ambiguous task.add or readback is NEVER retried. A missing/partial Bitrix
task listing cannot establish global deduplication, so this worker guarantees
at most ONE task from THIS integration, not absence of inaccessible tasks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import (
    verify_event, EncryptedEventFence, EventCloudBlocked,
    REPOSITORY, BRANCH, PORTAL,
)
from .serviceit_bitrix_poll_fallback import (
    after_activation, discover, date_value, PollBlocked,
)
from .serviceit_stage34_signed_readonly_sidecar import (
    owner_type, visible_tasks, assigned_employee, positive,
    ObserverBlocked, request as readonly_request,
)
from .serviceit_enrichment_worker import normalized, validate_identity, Blocked

CUTOVER = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)
WRITE_GATE = "YES_1062_21_INTERNAL_REVIEW"
FOLDER = ".serviceit/manager-task-once"
MAX_SCAN_BATCH = 200

class TaskAutomationBlocked(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise TaskAutomationBlocked(code)

class TaskFence(EncryptedEventFence):
    def url(self, card: int) -> str:
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "TASK_CARD_ID_INVALID")
        digest = hmac.new(
            self.path_key, f"stage3-internal-task-v1:1062:21:{card}".encode(),
            sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f"{FOLDER}/{digest}.enc")

def rest(client, base, method, args):
    require(method in ("crm.item.get", "crm.item.list",
                       "crm.type.getByEntityTypeId", "tasks.task.getFields",
                       "tasks.task.add", "tasks.task.get"),
            "REST_METHOD_FORBIDDEN")
    try:
        response=client.post(base + "/" + method + ".json",json=args,
                             timeout=20,follow_redirects=False)
        require(response.status_code==200 and 0<len(response.content)<=400_000,
                "REST_HTTP_OR_SCOPE")
        data=response.json()
        require(type(data) is dict and "error" not in data
                and "result" in data,"REST_RESPONSE_UNVERIFIED")
        return data["result"]
    except TaskAutomationBlocked:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise TaskAutomationBlocked("REST_BODY_OR_TRANSPORT_UNVERIFIED") from None

def check_task_binding(client, base):
    meta=rest(client,base,"crm.type.getByEntityTypeId",{"entityTypeId":1062})
    require(type(meta) is dict and type(meta.get("type")) is dict,
            "SMART_PROCESS_TYPE_UNVERIFIED")
    typ=meta["type"]
    require(str(typ.get("id"))=="13"
            and str(typ.get("entityTypeId"))=="1062"
            and typ.get("isUseInUserfieldEnabled") in (True,"Y","true")
            and type(typ.get("linkedUserFields")) is dict
            and typ["linkedUserFields"].get("TASKS_TASK|UF_CRM_TASK")
                 in (True,"Y","true","1"),
            "SMART_PROCESS_TASK_LINK_MISSING")
    f=rest(client,base,"tasks.task.getFields",{})
    require(type(f) is dict and type(f.get("fields")) is dict,
            "TASK_FIELDS_UNVERIFIED")
    for name in ("TITLE","DESCRIPTION","RESPONSIBLE_ID","UF_CRM_TASK"):
        require(type(f["fields"].get(name)) is dict,"TASK_REQUIRED_FIELD_MISSING")
    require(f["fields"]["UF_CRM_TASK"].get("type")=="crm",
            "TASK_BINDING_TYPE_INVALID")

def card_read(client, base, card, *, now):
    card=positive(card)
    require(card>=8 and card not in (3,7), "HISTORICAL_CARD_FORBIDDEN")
    data=rest(client,base,"crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
    })
    require(type(data) is dict and type(data.get("item")) is dict,
            "CARD_RESPONSE_UNVERIFIED")
    item=normalized(data["item"])
    validate_identity(item,card)
    created=date_value(item.get("createdTime"))
    require(CUTOVER<=created<=now, "CARD_BEFORE_TASK_AUTOMATION_CUTOVER")
    return item

def task_get_confirmed(client,base,task_id,binding,uid,title):
    item=rest(client,base,"tasks.task.get",{
        "taskId":task_id,"select":["ID","RESPONSIBLE_ID","UF_CRM_TASK","TITLE"],
    })
    require(type(item) is dict and type(item.get("task")) is dict,
            "TASK_GET_READBACK_INVALID")
    task=item["task"]
    linked=task.get("ufCrmTask",task.get("UF_CRM_TASK"))
    require(str(task.get("id",task.get("ID"))) == str(task_id)
            and str(task.get("responsibleId",task.get("RESPONSIBLE_ID"))) == str(uid)
            and type(linked) is list and linked==[binding]
            and task.get("title",task.get("TITLE")) == title,
            "TASK_READBACK_MISMATCH")

def process_card(client, env, card, *, now=None):
    """One card. Every refusal before reserve is non-destructive.

    Distinct GitHub ledger path and workflow concurrency serialize this
    task with other ServiceIT writers while preserving original AI receipts.
    """
    now=now or datetime.now(timezone.utc)
    require(type(now) is datetime and now.tzinfo is not None,
            "CLOCK_MISSING_TIMEZONE")
    now=now.astimezone(timezone.utc)
    require(env.get("SERVICEIT_STAGE3_TASK_WRITE_ENABLED")==WRITE_GATE,
            "STAGE3_OWNER_OPT_IN_MISSING")
    require(env.get("GITHUB_REPOSITORY")==REPOSITORY
            and env.get("GITHUB_REF")=="refs/heads/main",
            "GITHUB_SCOPE_INVALID")
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    require(base.startswith(f"https://{PORTAL}/rest/"),"PORTAL_INVALID")
    card=positive(card)
    item=card_read(client,base,card,now=now)
    check_task_binding(client,base)
    binding=owner_type(client,base)+"_"+str(card)
    n=visible_tasks(client,base,binding)
    if n:
        return "VISIBLE_TASK_EXISTS_NO_WRITE"
    uid=positive(item.get("assignedById"))
    state=assigned_employee(client,base,uid)
    require(state=="ONE_ACTIVE_ASSIGNED_EMPLOYEE_UNAPPROVED",
            "NO_ACTIVE_CARD_ASSIGNEE")
    # The assignee was designated on THIS specific new card. This permits
    # internal review routing, but is NOT authorization to call the customer.
    # Prevent replacing a concurrently reassigned manager or changed category.
    second=card_read(client,base,card,now=now)
    require(str(second.get("assignedById"))==str(uid)
            and second.get("stageId")==item.get("stageId"),
            "MANAGER_OR_STAGE_CHANGED_DURING_CHECK")
    title=f"Сервис ИТ | Проверить новый сигнал | CRM #{card}"
    description=(
        "Внутренняя проверка нового AI-сигнала в смарт-процессе "
        "«Сервис ИТ — AI-сигналы». Это не квалифицированный клиент.\n"
        "Проверить первоисточник, актуальность, ИНН/ФНС-МСП, "
        "клиентскую базу и дубли CRM, стоп-лист и правовое основание. "
        "Уточнить потребность, данные GigaChat рассматривать только как "
        "гипотезу, Lead Score не считать подтверждённым без девяти "
        "независимых проверок. НЕ звонить, НЕ писать и НЕ рассылать КП "
        "без решения уполномоченного менеджера."
    )
    fence=TaskFence(client,env.get("GITHUB_TOKEN",""),
                    env.get("SERVICEIT_GH_LEDGER_KEY",""))
    state=fence.reserve(card)
    if state!="RESERVED":
        return "ALREADY_RESERVED_NO_RETRY"
    # From this point ANY ambiguous network result leaves the durable fence.
    try:
        result=rest(client,base,"tasks.task.add",{
            "fields":{"TITLE":title,"DESCRIPTION":description,
                      "RESPONSIBLE_ID":uid,"UF_CRM_TASK":[binding]}})
        require(type(result) is dict and type(result.get("task")) is dict,
                "TASK_ADD_RESPONSE_UNVERIFIED")
        task_id=positive(result["task"].get("id"))
        task_get_confirmed(client,base,task_id,binding,uid,title)
    except Exception:
        fence.finalize(card,"UNCERTAIN")
        return "TASK_WRITE_UNCERTAIN_NO_RETRY"
    fence.finalize(card,"VERIFIED")
    return "INTERNAL_REVIEW_TASK_READBACK_VERIFIED"

def scan_cards(client,env,*,now=None):
    """Schedule catches missing signed events and late assignee assignment.

    Future-only activation and 7-day window; never scan historic clients.
    """
    now=now or datetime.now(timezone.utc)
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    require(base.startswith(f"https://{PORTAL}/rest/"),"PORTAL_INVALID")
    start=max(CUTOVER,after_activation(env,now))
    def list_rest(method,args):
        require(method=="crm.item.list","SCAN_METHOD_FORBIDDEN")
        out=rest(client,base,method,args)
        return out
    cards=discover(list_rest,start,now)
    require(len(cards)<=MAX_SCAN_BATCH,"SCAN_BATCH_OVERSIZED")
    totals={"candidates":len(cards),"created_and_verified":0,
            "already_fenced":0,"existing_visible":0,"not_ready":0,
            "uncertain":0}
    for card in cards:
        try:
            status=process_card(client,env,card,now=now)
        except Exception:
            totals["not_ready"]+=1
            continue
        key={
            "INTERNAL_REVIEW_TASK_READBACK_VERIFIED":"created_and_verified",
            "ALREADY_RESERVED_NO_RETRY":"already_fenced",
            "VISIBLE_TASK_EXISTS_NO_WRITE":"existing_visible",
            "TASK_WRITE_UNCERTAIN_NO_RETRY":"uncertain",
        }.get(status,"not_ready")
        totals[key]+=1
    return totals

def main():
    env=dict(os.environ)
    summary={"status":"SERVICEIT_STAGE3_NOT_APPLIED","crm_writes":0,
             "external_contacts":0,"historical_records_changed":0}
    try:
        require(env.get("GITHUB_EVENT_NAME") in
                ("repository_dispatch","schedule","issue_comment"),
                "EVENT_TYPE_NOT_APPROVED")
        if env.get("GITHUB_EVENT_NAME")=="issue_comment":
            file=Path(env.get("GITHUB_EVENT_PATH",""))
            require(file.is_file() and 0<file.stat().st_size<16384,
                    "OWNER_EVENT_FILE_INVALID")
            owner=json.loads(file.read_text("utf-8"))
            require(type(owner) is dict
                    and owner.get("action")=="created"
                    and type(owner.get("issue")) is dict
                    and owner["issue"].get("number")==148
                    and owner["issue"].get("pull_request") is None
                    and type(owner.get("sender")) is dict
                    and owner["sender"].get("login")=="Ramzes2023"
                    and type(owner.get("comment")) is dict
                    and owner["comment"].get("body")==
                    "SERVICEIT_STAGE34_ALL_NEW_OWNER_ONCE_V1",
                    "OWNER_ONCE_EVENT_NOT_AUTHORIZED")
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            if env["GITHUB_EVENT_NAME"]=="repository_dispatch":
                file=Path(env.get("GITHUB_EVENT_PATH",""))
                require(file.is_file() and 0<file.stat().st_size<16384,
                        "SIGNATURE_EVENT_FILE_INVALID")
                event=json.loads(file.read_text("utf-8"))
                card=verify_event(event,env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY",""))
                status=process_card(client,env,card)
                summary={"status":status,"crm_writes":0,
                         "task_create_confirmed":
                             status=="INTERNAL_REVIEW_TASK_READBACK_VERIFIED",
                         "task_creation_uncertain":
                             status=="TASK_WRITE_UNCERTAIN_NO_RETRY",
                         "external_contacts":0}
            else:
                result=scan_cards(client,env)
                summary={"status":"SERVICEIT_STAGE3_SCHEDULED_SCAN",
                         **result,"crm_writes":0,"external_contacts":0}
    except Exception:
        summary={"status":"SERVICEIT_STAGE3_SAFE_HOLD",
                 "reason":"CORPORATE_RIGHTS_OR_EVIDENCE_UNVERIFIED",
                 "crm_writes":0,"external_contacts":0}
    print(json.dumps(summary,sort_keys=True))
    return 0 if summary["status"] not in (
        "SERVICEIT_STAGE3_SAFE_HOLD","TASK_WRITE_UNCERTAIN_NO_RETRY") else 2

if __name__=="__main__":
    raise SystemExit(main())
