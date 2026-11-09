"""Hourly hybrid LIVE finance assistant for genuine NEW LeadGen closed-won deals.

Reads only CRM deal category explicitly named «Лидогенерация».
If the sales manager needs to supply unknown bank/cost facts, creates an
INTERNAL task linked to that deal; the manager adds a strict template to the
deal COMMENTS. Reads later revisions and calculates 15/10/75 as a DRAFT,
writing/updating a second INTERNAL task owned by our integration. Exact
remote encrypted one-use fences guard each task creation and report revision.

A deal amount or task description is NEVER bank verification or payroll.
Never touch support category, past records, lead 1062/21, source connectors,
public outreach, contracts, payment providers, or actual salaries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, REPOSITORY
from .serviceit_finance_hybrid import (
    AUTOMATIC, STATUS_MISSING, STATUS_CONFLICT,
    STATUS_DRAFT, STATUS_DOCS, manager_template, reconcile,
)

PORTAL="https://serviseit.bitrix24.ru/rest/"
CATEGORY_NAME="Лидогенерация"
CUTOVER=datetime(2026,11,5,0,0,tzinfo=timezone.utc)
ENABLED="YES_LEADGEN_SALES_INTERNAL_FINANCE"
ISSUE=148
ONCE="SERVICEIT_FINANCE_HYBRID_LIVE_ONCE_V1"
OWNER="Ramzes2023"
TASK_MARKER="SERVICEIT_FINANCE_INTERNAL_TASK_V1"
MAX_DEALS=100
REST_ALLOW=frozenset({
    "crm.category.list","crm.deal.fields","crm.deal.list","crm.deal.get",
    "tasks.task.getFields","tasks.task.list","tasks.task.get",
    "tasks.task.add","tasks.task.update",
    "user.get","user.current",
})


class FinanceWorkerBlocked(ValueError):
    pass


def need(ok,code):
    if not ok: raise FinanceWorkerBlocked(code)


def positive(value):
    need(type(value) in (int,str) and
         re.fullmatch(r"[1-9][0-9]{0,9}",str(value)) is not None,
         "FINANCE_ID_INVALID")
    return int(value)


def from_iso(value):
    need(type(value) is str and 10<=len(value)<=42,
         "FINANCE_DEAL_DATE_INVALID")
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        raise FinanceWorkerBlocked("FINANCE_DEAL_DATE_INVALID") from None
    need(dt.tzinfo is not None,"FINANCE_DEAL_DATE_TIMEZONE_MISSING")
    return dt.astimezone(timezone.utc)


def call(client,base,method,args):
    need(method in REST_ALLOW and type(args) is dict,
         "FINANCE_METHOD_NOT_ALLOWED")
    if method=="crm.deal.get":
        need(set(args)=={"id"}, "FINANCE_GET_ARGUMENTS_UNVERIFIED")
    if method=="crm.category.list":
        need(args=={"entityTypeId":2},"FINANCE_CATEGORY_FILTER_FORBIDDEN")
    if method=="crm.deal.list":
        # Native VPS cutover happens after the original cloud CUTOVER. The
        # effective threshold is max(CUTOVER, native activation UTC) in
        # list_won(); never require equality with the *older* fixed CUTOVER.
        filters=args.get("filter")
        threshold=filters.get(">=DATE_CREATE") if type(filters) is dict else None
        threshold_ok=False
        if type(threshold) is str:
            try:
                parsed=from_iso(threshold)
                threshold_ok=(
                    threshold == parsed.isoformat()
                    and CUTOVER <= parsed <=
                        datetime.now(timezone.utc)+timedelta(minutes=5)
                )
            except FinanceWorkerBlocked:
                threshold_ok=False
        need(type(filters) is dict
             and set(filters)=={"CATEGORY_ID","STAGE_SEMANTIC_ID",">=DATE_CREATE"}
             and filters.get("STAGE_SEMANTIC_ID")=="S"
             and type(filters.get("CATEGORY_ID")) is int
             and filters["CATEGORY_ID"]>0
             and threshold_ok
             and args.get("start") in (0,50)
             and args.get("select")==[
                 "ID","CATEGORY_ID","STAGE_SEMANTIC_ID","DATE_CREATE"],
             "FINANCE_DEAL_LIST_OUTSIDE_SCOPE")
    if method=="tasks.task.list":
        need(set(args)=={"filter","select","order","start"}
             and type(args["filter"].get("UF_CRM_TASK")) is str
             and re.fullmatch(r"D_[1-9][0-9]{0,9}",
                              args["filter"]["UF_CRM_TASK"])
             and args["select"]==["ID","TITLE","RESPONSIBLE_ID","UF_CRM_TASK"]
             and args["start"] in (0,50),
             "FINANCE_TASK_LIST_OUTSIDE_SCOPE")
    if method=="tasks.task.add":
        need(set(args)=={"fields"} and
             set(args["fields"])==
             {"TITLE","DESCRIPTION","CREATED_BY","RESPONSIBLE_ID","UF_CRM_TASK"},
             "FINANCE_TASK_ADD_OUTSIDE_SCOPE")
    if method=="tasks.task.update":
        need(set(args)=={"taskId","fields"} and
             set(args["fields"])=={"DESCRIPTION"},
             "FINANCE_TASK_UPDATE_OUTSIDE_SCOPE")
    try:
        res=client.post(base+"/"+method+".json",json=args,timeout=25,
                        follow_redirects=False)
        need(res.status_code==200 and 0<len(res.content)<=600000,
             "FINANCE_REST_HTTP_OR_SCOPE")
        body=res.json()
        need(type(body) is dict and "error" not in body
             and "result" in body,"FINANCE_REST_RESULT_UNVERIFIED")
        return body
    except FinanceWorkerBlocked:raise
    except (httpx.HTTPError,TypeError,ValueError):
        raise FinanceWorkerBlocked("FINANCE_REST_TRANSPORT_UNVERIFIED") from None


def exact_category(client,base):
    result=call(client,base,"crm.category.list",{"entityTypeId":2})["result"]
    need(type(result) is dict and type(result.get("categories")) is list,
         "FINANCE_CATEGORIES_UNVERIFIED")
    found=[c for c in result["categories"]
           if type(c) is dict and c.get("name")==CATEGORY_NAME
           and str(c.get("entityTypeId"))=="2"]
    need(len(found)<=1,"FINANCE_DUPLICATE_CATEGORY_NAMES")
    if not found: return None
    id=positive(found[0].get("id"))
    need(id>0,"FINANCE_DEFAULT_OR_SUPPORT_CATEGORY_NOT_ALLOWED")
    return id


def deal_schema(client,base):
    catalog=call(client,base,"crm.deal.fields",{})["result"]
    need(type(catalog) is dict and all(x in catalog for x in (
        "CATEGORY_ID","STAGE_SEMANTIC_ID","ASSIGNED_BY_ID",
        "DATE_CREATE","COMMENTS","CURRENCY_ID")),
        "FINANCE_DEAL_REQUIRED_FIELDS_MISSING")
    return catalog


def task_schema(client,base):
    result=call(client,base,"tasks.task.getFields",{})["result"]
    need(type(result) is dict and type(result.get("fields")) is dict,
         "FINANCE_TASK_SCHEMA_UNVERIFIED")
    fields=result["fields"]
    need(all(type(fields.get(x)) is dict
             for x in ("TITLE","DESCRIPTION","RESPONSIBLE_ID","UF_CRM_TASK")),
         "FINANCE_TASK_FIELDS_MISSING")
    need(fields["UF_CRM_TASK"].get("type")=="crm",
         "FINANCE_TASK_CRM_LINK_INVALID")


def list_won(client,base,cat,*,native_after=None):
    earliest=max(CUTOVER,native_after) if native_after is not None else CUTOVER
    items=[]
    for start in (0,50):
        response=call(client,base,"crm.deal.list",{
            "filter":{"CATEGORY_ID":cat,"STAGE_SEMANTIC_ID":"S",
                      ">=DATE_CREATE":earliest.isoformat()},
            "select":["ID","CATEGORY_ID","STAGE_SEMANTIC_ID","DATE_CREATE"],
            "start":start,
        })
        batch=response["result"]
        need(type(batch) is list and len(batch)<=50,
             "FINANCE_DEAL_LIST_RESPONSE_UNVERIFIED")
        for row in batch:
            need(type(row) is dict and
                 str(row.get("CATEGORY_ID"))==str(cat)
                 and row.get("STAGE_SEMANTIC_ID")=="S"
                 and from_iso(row.get("DATE_CREATE"))>=earliest,
                 "FINANCE_WRONG_DEAL_IN_LIST")
            id=positive(row.get("ID"))
            need(id not in items,"FINANCE_LIST_DUPLICATE_DEAL")
            items.append(id)
        nxt=response.get("next")
        total=response.get("total")
        need(total is None or
             (type(total) is int and 0<=total<=MAX_DEALS),
             "FINANCE_LIST_TOTAL_UNVERIFIED")
        if nxt is None:
            need(total is None or len(items)==total,
                 "FINANCE_LIST_TRUNCATED")
            return items
        need(nxt==start+50 and len(batch)==50,
             "FINANCE_LIST_PAGINATION_UNVERIFIED")
    raise FinanceWorkerBlocked("FINANCE_TOO_MANY_DEALS_FAIL_CLOSED")


def read_deal(client,base,cat,id,now,*,native_after=None):
    deal=call(client,base,"crm.deal.get",{"id":id})["result"]
    need(type(deal) is dict and str(deal.get("ID"))==str(id)
         and str(deal.get("CATEGORY_ID"))==str(cat)
         and deal.get("STAGE_SEMANTIC_ID")=="S"
         and deal.get("CURRENCY_ID")=="RUB",
         "FINANCE_DEAL_NOT_ISOLATED_WON_RUB")
    created=from_iso(deal.get("DATE_CREATE"))
    earliest=max(CUTOVER,native_after) if native_after is not None else CUTOVER
    need(earliest<=created<=now,"FINANCE_DEAL_HISTORICAL_OR_FUTURE")
    need(str(deal.get("IS_RECURRING","N")) in ("N","False","0"),
         "FINANCE_RECURRING_TEMPLATE_FORBIDDEN")
    uid=positive(deal.get("ASSIGNED_BY_ID"))
    return deal,uid


def verify_employee(client,base,uid):
    response=call(client,base,"user.get",{
        "FILTER":{"ID":uid,"ACTIVE":True},
        "select":["ID","ACTIVE","USER_TYPE"],"start":0,
    })
    people=response["result"]
    need(type(people) is list and len(people)==1
         and response.get("total")==1
         and response.get("next") is None,
         "FINANCE_MANAGER_NOT_SINGLE_VISIBLE")
    u=people[0]
    need(str(u.get("ID"))==str(uid)
         and u.get("ACTIVE") in (True,"Y")
         and u.get("USER_TYPE")=="employee",
         "FINANCE_MANAGER_NOT_ACTIVE_EMPLOYEE")


def current_employee(client,base):
    user=call(client,base,"user.current",{})["result"]
    need(type(user) is dict, "FINANCE_BOT_USER_UNKNOWN")
    return positive(user.get("ID"))


def linked_tasks(client,base,deal_id):
    link="D_"+str(deal_id)
    rows,ids=[],set()
    for start in (0,50):
        resp=call(client,base,"tasks.task.list",{
            "filter":{"UF_CRM_TASK":link},
            "select":["ID","TITLE","RESPONSIBLE_ID","UF_CRM_TASK"],
            "order":{"ID":"ASC"},"start":start,
        })
        result=resp["result"]
        need(type(result) is dict and type(result.get("tasks")) is list,
             "FINANCE_TASK_LIST_NOT_READABLE")
        data=result["tasks"]
        need(len(data)<=50,"FINANCE_TASK_LIST_TOO_MANY")
        for row in data:
            need(type(row) is dict,"FINANCE_TASK_LIST_ITEM_INVALID")
            links=row.get("ufCrmTask",row.get("UF_CRM_TASK"))
            need(type(links) is list and link in links,
                 "FINANCE_TASK_LINK_FILTER_NOT_PROVEN")
            id=positive(row.get("id",row.get("ID")))
            need(id not in ids,"FINANCE_TASK_LIST_DUPLICATE")
            ids.add(id);rows.append(row)
        nxt=resp.get("next")
        total=resp.get("total")
        need(type(total) is int and 0<=total<=100,
             "FINANCE_TASK_LIST_TOTAL_UNVERIFIED")
        if nxt is None:
            need(total==len(rows),"FINANCE_TASK_LIST_INCOMPLETE")
            return rows
        need(nxt==start+50 and len(data)==50,
             "FINANCE_TASK_LIST_PAGE_UNVERIFIED")
    raise FinanceWorkerBlocked("FINANCE_TOO_MANY_TASKS")


def titles(deal):
    return {
        "request":f"Сервис ИТ | Финансы: уточнить данные | сделка #{deal}",
        "report":f"Сервис ИТ | Расчёт РВП 15/10/75 | сделка #{deal}",
    }


def our_task(client,base,id,kind,deal,manager,creator):
    title=titles(deal)[kind]
    item=call(client,base,"tasks.task.get",{
        "taskId":id,
        "select":["ID","TITLE","DESCRIPTION","RESPONSIBLE_ID",
                  "CREATED_BY","UF_CRM_TASK"],
    })["result"]
    need(type(item) is dict and type(item.get("task")) is dict,
         "FINANCE_TASK_GET_INVALID")
    t=item["task"]
    need(str(t.get("id",t.get("ID")))==str(id)
         and t.get("title",t.get("TITLE"))==title
         and str(t.get("responsibleId",t.get("RESPONSIBLE_ID")))==str(manager)
         and str(t.get("createdBy",t.get("CREATED_BY")))==str(creator)
         and t.get("ufCrmTask",t.get("UF_CRM_TASK"))==["D_"+str(deal)]
         and isinstance(t.get("description",t.get("DESCRIPTION")),str)
         and TASK_MARKER in t.get("description",t.get("DESCRIPTION")),
         "FINANCE_TASK_NOT_OWNED_OR_BINDING_CHANGED")
    return t.get("description",t.get("DESCRIPTION"))


class FinanceFence(EncryptedEventFence):
    def __init__(self, client, token, key, kind, version="v1"):
        super().__init__(client,token,key)
        need(kind in ("request-add","report-add","report-update","request-update"),
             "FINANCE_FENCE_KIND_INVALID")
        need(type(version) is str and
             re.fullmatch(r"[a-f0-9]{64}|v1",version),
             "FINANCE_FENCE_VERSION_INVALID")
        self.kind,self.version=kind,version

    def url(self, card):
        need(type(card) is int and 0<card<=2_000_000_000,
             "FINANCE_FENCE_DEAL_ID_INVALID")
        hashpart=hmac.new(
            self.path_key,
            f"serviceit-finance-v1:{self.kind}:deal:{card}:{self.version}"
                .encode("utf-8"),sha256).hexdigest()
        return ("https://api.github.com/repos/"+REPOSITORY+
                "/contents/.serviceit/finance-task-once/"+hashpart+".enc")


def action_fence(client,env,deal,kind,version="v1",*,native_connection=None):
    if native_connection is None:
        return FinanceFence(client,env.get("GITHUB_TOKEN",""),
                            env.get("SERVICEIT_GH_LEDGER_KEY",""),
                            kind,version)
    from .serviceit_vps_native_actions import PostgresActionFence
    operation={
        "request-add":"FINANCE_REQUEST_ADD",
        "report-add":"FINANCE_REPORT_ADD",
        "report-update":"FINANCE_REPORT_UPDATE",
        "request-update":"FINANCE_REQUEST_UPDATE",
    }.get(kind)
    need(operation is not None,"NATIVE_FINANCE_KIND_INVALID")
    return PostgresActionFence(native_connection,operation,deal,version)


def create_task(client,base,env,deal,manager,creator,kind,description,
                *,native_connection=None):
    link="D_"+str(deal)
    need(kind in ("request","report"),"FINANCE_TASK_KIND_INVALID")
    name=titles(deal)[kind]
    need(type(description) is str and TASK_MARKER in description
         and len(description)<=2500,"FINANCE_TASK_DESCRIPTION_INVALID")
    fence=action_fence(client,env,deal,kind+"-add",
                       native_connection=native_connection)
    if fence.reserve(deal)!="RESERVED":
        return "ALREADY_FENCED_NO_RETRY"
    try:
        added=call(client,base,"tasks.task.add",{
            "fields":{"TITLE":name,"DESCRIPTION":description,
                      "CREATED_BY":creator,
                      "RESPONSIBLE_ID":manager,"UF_CRM_TASK":[link]},
        })["result"]
        need(type(added) is dict and type(added.get("task")) is dict,
             "FINANCE_TASK_ADD_UNVERIFIED")
        id=positive(added["task"].get("id"))
        seen=our_task(client,base,id,kind,deal,manager,creator)
        need(seen==description,"FINANCE_TASK_CREATE_READBACK_DIFFERENT")
    except Exception:
        fence.finalize(deal,"UNCERTAIN")
        return "TASK_ADD_UNCERTAIN_NO_RETRY"
    fence.finalize(deal,"VERIFIED")
    return "TASK_CREATED_AND_VERIFIED"


def update_report(client,base,env,deal,report_task,manager,creator,description,
                  *,native_connection=None):
    need(type(description) is str and TASK_MARKER in description
         and len(description)<=2500,"FINANCE_REPORT_DESCRIPTION_INVALID")
    before=our_task(client,base,report_task,"report",deal,manager,creator)
    if before==description:
        return "REPORT_ALREADY_CURRENT"
    version=sha256(description.encode("utf-8")).hexdigest()
    fence=action_fence(client,env,deal,"report-update",version,
                       native_connection=native_connection)
    if fence.reserve(deal)!="RESERVED":
        return "REPORT_REVISION_PREVIOUSLY_FENCED"
    try:
        # Recheck because manager/creator or contents might have changed.
        need(our_task(client,base,report_task,"report",deal,manager,creator)==before,
             "FINANCE_REPORT_CONCURRENT_EDIT")
        call(client,base,"tasks.task.update",{
            "taskId":report_task,
            "fields":{"DESCRIPTION":description},
        })
        actual=our_task(client,base,report_task,"report",deal,manager,creator)
        need(actual==description,"FINANCE_REPORT_UPDATE_READBACK_MISMATCH")
    except Exception:
        fence.finalize(deal,"UNCERTAIN")
        return "REPORT_UPDATE_UNCERTAIN_NO_RETRY"
    fence.finalize(deal,"VERIFIED")
    return "REPORT_UPDATED_VERIFIED"


def update_request(client,base,env,deal,request_task,manager,creator,description,
                   *,native_connection=None):
    need(type(description) is str and TASK_MARKER in description
         and len(description)<=2500,"FINANCE_REQUEST_DESCRIPTION_INVALID")
    before=our_task(client,base,request_task,"request",deal,manager,creator)
    if before==description:
        return "REQUEST_ALREADY_CURRENT"
    version=sha256(description.encode("utf-8")).hexdigest()
    fence=action_fence(client,env,deal,"request-update",version,
                       native_connection=native_connection)
    if fence.reserve(deal)!="RESERVED":
        return "REQUEST_REVISION_PREVIOUSLY_FENCED"
    try:
        need(our_task(client,base,request_task,"request",deal,manager,creator)==before,
             "FINANCE_REQUEST_CONCURRENT_EDIT")
        call(client,base,"tasks.task.update",{
            "taskId":request_task,"fields":{"DESCRIPTION":description}})
        actual=our_task(client,base,request_task,"request",deal,manager,creator)
        need(actual==description,"FINANCE_REQUEST_UPDATE_READBACK_MISMATCH")
    except Exception:
        fence.finalize(deal,"UNCERTAIN")
        return "REQUEST_UPDATE_UNCERTAIN_NO_RETRY"
    fence.finalize(deal,"VERIFIED")
    return "REQUEST_UPDATED_VERIFIED"


def report_text(result):
    need(type(result) is dict,"FINANCE_REPORT_INVALID")
    status=result.get("status")
    if status in (STATUS_MISSING,STATUS_CONFLICT):
        desc=(
            TASK_MARKER+"\n"
            "СТОП: финансовых данных недостаточно либо они противоречат друг другу.\n"
            "Прежние суммы не являются актуальными, расчёт не подтверждён.\n"
            "Уточните данные в сделке и у бухгалтерии.\n"
            "Выплаты не разрешены.\n")
    else:
        a=result.get("amounts")
        need(type(a) is dict and
             all(type(a.get(k)) is str for k in (
                 "paid_ex_vat","direct_costs","profit",
                 "developer","sales_manager","company")),
             "FINANCE_REPORT_AMOUNTS_MISSING")
        desc=(
            TASK_MARKER+"\n"
            "ПРЕДВАРИТЕЛЬНЫЙ АВТОМАТИЧЕСКИЙ РАСЧЁТ. Не платёжное поручение.\n"
            "Реально оплачено без НДС (по имеющимся данным): "+a["paid_ex_vat"]+" ₽\n"
            "Прямые расходы (по имеющимся данным): "+a["direct_costs"]+" ₽\n"
            "РВП: "+a["profit"]+" ₽\n"
            "Разработчику 15%: "+a["developer"]+" ₽\n"
            "Продающему менеджеру 10%: "+a["sales_manager"]+" ₽\n"
            "Компании 75% от положительной РВП: "+a["company"]+" ₽\n"
            "При убытке компания покрывает отрицательный остаток.\n"
            "Суммы от менеджера не являются банковским подтверждением.\n"
            "Для начисления сверить документы и банковское поступление.\n"
            "ВЫПЛАТЫ НЕ РАЗРЕШЕНЫ.\n"
            "Версия: "+result["version_sha256"]+"\n"
        )
    need(len(desc)<=2500,"FINANCE_REPORT_SIZE_INVALID")
    return desc


def process_deal(client,base,env,cat,deal_id,creator,now,
                 *,native_connection=None,native_after=None):
    deal,manager=(read_deal(client,base,cat,deal_id,now)
                  if native_after is None else
                  read_deal(client,base,cat,deal_id,now,
                            native_after=native_after))
    verify_employee(client,base,manager)
    finance_fields={key:deal.get(key) for key in AUTOMATIC.values()}
    result=reconcile(finance_fields,deal.get("COMMENTS"))
    name=titles(deal_id)
    rows=linked_tasks(client,base,deal_id)
    req=[x for x in rows if x.get("title",x.get("TITLE"))==name["request"]]
    rep=[x for x in rows if x.get("title",x.get("TITLE"))==name["report"]]
    need(len(req)<=1 and len(rep)<=1,
         "FINANCE_MULTIPLE_OWN_TASKS")
    for mode,items in (("request",req),("report",rep)):
        if items:
            our_task(client,base,
                     positive(items[0].get("id",items[0].get("ID"))),
                     mode,deal_id,manager,creator)
    needed=result["status"] in (STATUS_MISSING,STATUS_CONFLICT,STATUS_DOCS)
    if needed:
        to_fill=sorted(set(result.get("missing",[])+result.get("conflicting",[])))
        need(bool(to_fill),"FINANCE_UNKNOWN_DATA_GAP")
        description=TASK_MARKER+"\n"+manager_template(deal_id,to_fill)
    else:
        description=(
            TASK_MARKER+"\n"
            "Финансовые суммы и реквизиты, ранее запрошенные для сделки, "
            "заполнены. Проверьте отдельный предварительный расчёт РВП; "
            "отсутствие дополнительного запроса не подтверждает банковские "
            "поступления и не разрешает выплату.\n"
        )
    def invoke(fn,*args):
        # Existing GitHub worker and tests retain their exact signatures.
        # Only the native VPS path passes PostgreSQL transaction context.
        return (fn(*args) if native_connection is None else
                fn(*args,native_connection=native_connection))
    if req:
        request_id=positive(req[0].get("id",req[0].get("ID")))
        request=invoke(update_request,client,base,env,deal_id,request_id,
                       manager,creator,description)
    elif needed:
        request=invoke(create_task,client,base,env,deal_id,manager,
                       creator,"request",description)
    else:
        request="INPUT_REQUEST_NOT_NEEDED"
    # Updating a report or its request is never an authorized payment.
    if rep:
        task_id=positive(rep[0].get("id",rep[0].get("ID")))
        action=invoke(update_report,client,base,env,deal_id,task_id,manager,
                      creator,report_text(result))
    elif result["status"] in (STATUS_DRAFT,STATUS_DOCS):
        action=invoke(create_task,client,base,env,deal_id,manager,
                      creator,"report",report_text(result))
    else:
        action="REPORT_NOT_READY"
    return {"data_status":result["status"],"request_action":request,
            "report_action":action}


def run(client,env,*,now=None,native_connection=None):
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(env.get("SERVICEIT_FINANCE_MANAGER_TASK_ENABLED")==ENABLED,
         "FINANCE_LIVE_SCOPE_OR_OWNER_OPT_IN_MISSING")
    native_after=None
    if native_connection is None:
        need(env.get("GITHUB_REPOSITORY")==REPOSITORY
             and env.get("GITHUB_REF")=="refs/heads/main",
             "FINANCE_LIVE_SCOPE_OR_OWNER_OPT_IN_MISSING")
    else:
        from .serviceit_bitrix_poll_fallback import date_value
        need(env.get("SERVICEIT_VPS_FINANCE_ENABLED")=="YES"
             and env.get("SERVICEIT_VPS_WRITER_MODE")=="LIVE"
             and env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED")=="YES"
             and env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK")=="YES"
             and env.get("SERVICEIT_VPS_POLL_ENABLED")=="YES"
             and env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"),
             "NATIVE_FINANCE_CUTOVER_NOT_READY")
        native_after=date_value(env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"])
        need(native_after <= now, "NATIVE_FINANCE_CUTOVER_TIME_INVALID")
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    need(base.startswith(PORTAL),"FINANCE_WRONG_PORTAL")
    category=exact_category(client,base)
    totals={
        "status":"FINANCE_HYBRID_LIVE_SCOPED",
        "category_visible":category is not None,
        "new_won_deals_visible":0,
        "processed":0,"input_missing_or_conflict":0,
        "draft_calculated":0,"tasks_created_and_verified":0,
        "reports_updated_and_verified":0,
        "manager_requests_updated_and_verified":0,
        "blocked":0,
        "source_ingestion":0,"crm_item_writes":0,
        "support_deal_mutations":0,"outbound_customer_contacts":0,
        "payouts":0,
    }
    if category is None:
        totals["status"]="FINANCE_LEADGEN_FUNNEL_NOT_VISIBLE_NO_WRITE"
        return totals
    deal_schema(client,base)
    task_schema(client,base)
    ids=(list_won(client,base,category)
         if native_after is None else
         list_won(client,base,category,native_after=native_after))
    totals["new_won_deals_visible"]=len(ids)
    if not ids:
        totals["status"]="FINANCE_NO_NEW_CLOSED_WON_SALES"
        return totals
    creator=current_employee(client,base)
    for id in ids:
        try:
            item=(process_deal(client,base,env,category,id,creator,now)
                  if native_connection is None else
                  process_deal(client,base,env,category,id,creator,now,
                               native_connection=native_connection,
                               native_after=native_after))
        except Exception:
            totals["blocked"]+=1
            continue
        totals["processed"]+=1
        if item["data_status"] in (STATUS_MISSING,STATUS_CONFLICT):
            totals["input_missing_or_conflict"]+=1
        else:
            totals["draft_calculated"]+=1
        totals["tasks_created_and_verified"]+=sum(
            x=="TASK_CREATED_AND_VERIFIED" for x in (
                item["request_action"],item["report_action"]))
        totals["reports_updated_and_verified"]+=int(
            item["report_action"]=="REPORT_UPDATED_VERIFIED")
        totals["manager_requests_updated_and_verified"]+=int(
            item["request_action"]=="REQUEST_UPDATED_VERIFIED")
        if "UNCERTAIN" in item["request_action"] or "UNCERTAIN" in item["report_action"]:
            totals["blocked"]+=1
    return totals


def validate_trigger(env):
    need(env.get("GITHUB_REPOSITORY")==REPOSITORY
         and env.get("GITHUB_REF")=="refs/heads/main",
         "FINANCE_REPOSITORY_SCOPE_INVALID")
    if env.get("GITHUB_EVENT_NAME")=="schedule":return
    need(env.get("GITHUB_EVENT_NAME")=="issue_comment"
         and env.get("GITHUB_ACTOR")==OWNER,
         "FINANCE_OWNER_SCOPE_INVALID")
    p=Path(env.get("GITHUB_EVENT_PATH","/nonexistent"))
    need(p.is_file() and 0<p.stat().st_size<=100_000,
         "FINANCE_EVENT_UNVERIFIED")
    try:
        event=json.loads(p.read_text(encoding="utf8"))
    except (ValueError,OSError):
        raise FinanceWorkerBlocked("FINANCE_EVENT_INVALID") from None
    need(type(event) is dict and event.get("action")=="created"
         and (event.get("repository") or {}).get("full_name")==REPOSITORY
         and (event.get("issue") or {}).get("number")==ISSUE
         and not (event.get("issue") or {}).get("pull_request")
         and (event.get("sender") or {}).get("login")==OWNER
         and (event.get("comment") or {}).get("body")==ONCE
         and ((event.get("comment") or {}).get("user") or {}).get("login")==OWNER,
         "FINANCE_OWNER_MARKER_UNVERIFIED")


def main():
    env=dict(os.environ)
    result={"status":"FINANCE_HYBRID_BLOCKED_REVIEW",
            "crm_item_writes":0,"payouts":0}
    try:
        validate_trigger(env)
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            result=run(client,env)
    except Exception:
        print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
        return 2
    # Aggregate only: no amounts/customer names/IDs/payment documents in logs.
    print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
