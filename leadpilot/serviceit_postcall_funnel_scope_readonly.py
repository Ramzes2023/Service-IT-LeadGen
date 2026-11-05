"""Read-only portal preflight for Mikaيل's after-call + revenue/MRR funnel.

Access may be partial. Only report field/API availability and aggregate
presence: no raw CRM object, call body, customer ID, email, name or money value.
Never create a task, contact anyone, change a stage or touch support pipeline.
"""
from __future__ import annotations
import json
import os
import re
import httpx
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import PREFIX

PORTAL="https://serviseit.bitrix24.ru/rest/"
EXPECTED={
    "crm.item.fields", "crm.category.list", "crm.activity.fields",
    "crm.activity.list", "crm.status.list",
}
NEEDED={
    "postcall_next_action": PREFIX+"MANAGER_NEXT_ACTION",
    "manager_review": PREFIX+"REVIEW_DECISION",
    "ai_summary": PREFIX+"AI_SUMMARY",
    "ai_next_action": PREFIX+"AI_NEXT_ACTION",
    "score_version": PREFIX+"SCORE_VERSION",
}
REQUIRED_ACTIVITY=("ID","OWNER_ID","OWNER_TYPE_ID","COMPLETED","DESCRIPTION")

class PortalReadBlocked(ValueError): pass

def safe_request(client,base,method,args):
    if method not in EXPECTED:
        raise PortalReadBlocked("METHOD_NOT_READONLY_ALLOWLISTED")
    if method=="crm.status.list":
        target=(args.get("filter") or {}).get("ENTITY_ID") if type(args) is dict else None
        if not (type(args) is dict and set(args)=={"filter"}
                and type(args.get("filter")) is dict
                and set(args["filter"])=={"ENTITY_ID"}
                and type(target) is str
                and re.fullmatch(r"DEAL_STAGE_[1-9][0-9]{0,8}",target)):
            raise PortalReadBlocked("ONLY_ISOLATED_DEAL_STAGES_ALLOWED")
    response=client.post(base+"/"+method+".json",
                         json=args,timeout=20,follow_redirects=False)
    if response.status_code!=200 or len(response.content)>300000:
        raise PortalReadBlocked("API_READ_DENIED_OR_HTTP_UNVERIFIED")
    try: body=response.json()
    except ValueError: raise PortalReadBlocked("API_BODY_NOT_JSON") from None
    if type(body) is not dict or "error" in body or "result" not in body:
        raise PortalReadBlocked("API_RESULT_NOT_VALID")
    return body["result"]

def sales_stage_readback(client,base,category_id):
    """Only stages of the exactly located nondefault LeadGen DEAL category.

    The customer-facing stage names/IDs stay in memory; public logs expose
    booleans only. The result is visibility, not permission to change stages.
    """
    if type(category_id) is not int or not 0<category_id<=999_999_999:
        raise PortalReadBlocked("ISOLATED_DEAL_CATEGORY_ID_INVALID")
    scope="DEAL_STAGE_"+str(category_id)
    rows=safe_request(client,base,"crm.status.list",
                      {"filter":{"ENTITY_ID":scope}})
    if type(rows) is not list or not 1<=len(rows)<=50:
        raise PortalReadBlocked("DEAL_STAGES_EMPTY_OR_UNVERIFIED")
    ids=set()
    names=[]
    success=failure=False
    for row in rows:
        if (type(row) is not dict or row.get("ENTITY_ID")!=scope
            or type(row.get("STATUS_ID")) is not str
            or not row["STATUS_ID"] or row["STATUS_ID"] in ids
            or type(row.get("NAME")) is not str or not row["NAME"].strip()):
            raise PortalReadBlocked("DEAL_STAGES_NOT_SCOPED_OR_UNIQUE")
        ids.add(row["STATUS_ID"])
        names.append(row["NAME"].strip().casefold())
        extra=row.get("EXTRA")
        if extra is not None and type(extra) is not dict:
            raise PortalReadBlocked("DEAL_STAGE_EXTRA_NOT_OBJECT")
        semantic=(extra or {}).get("SEMANTICS",row.get("SEMANTICS"))
        if type(semantic) is str:
            semantic=semantic.strip().casefold()
        success|=semantic in ("s","success")
        failure|=semantic in ("f","failure")
    labels={
        "interest":any("интерес" in n for n in names),
        "quotation":any("кп" in n or "предложен" in n or "расч" in n
                        for n in names),
        "negotiation":any("переговор" in n for n in names),
        "agreement":any("согласован" in n for n in names),
        "closed_won":success,
        "closed_lost":failure,
    }
    return {
        "status":"SALES_STAGES_READBACK_VERIFIED",
        "visible_stage_count":len(rows),
        "sales_flow_requirements":labels,
        "recommended_sales_flow_visible":all(labels.values()),
        "sales_stage_semantics_ready":success and failure,
    }


def probe(client,webhook):
    base=validate_webhook(webhook)
    if not base.startswith(PORTAL):
        raise PortalReadBlocked("UNEXPECTED_COMPANY_PORTAL")
    out={"status":"SERVICEIT_POSTCALL_FUNNEL_READONLY",
         "company_scope":"1062/21 (future-only)",
         "crm_writes":0,"task_writes":0,"external_messages":0,
         "revenue_verified":False,"mrr_verified":False,
         "global_corporate_crm_coverage_verified":False}
    try:
        fields=safe_request(client,base,"crm.item.fields",
                            {"entityTypeId":1062,"useOriginalUfNames":"Y"})
        if type(fields) is not dict or type(fields.get("fields")) is not dict:
            raise PortalReadBlocked("SMART_PROCESS_FIELDS_UNAVAILABLE")
        catalog=fields["fields"]
        out["manager_fields"]={name:(key in catalog)
                               for name,key in NEEDED.items()}
        out["manager_fields_ready"]=all(out["manager_fields"].values())
    except Exception:
        out["manager_fields_ready"]=False
        out["manager_fields"]={name:False for name in NEEDED}
    try:
        fields=safe_request(client,base,"crm.activity.fields",{})
        out["completed_activity_metadata_readable"]=(
            type(fields) is dict
            and all(field in fields for field in REQUIRED_ACTIVITY))
    except Exception:
        out["completed_activity_metadata_readable"]=False
    try:
        activities=safe_request(client,base,"crm.activity.list",{
            "filter":{"OWNER_TYPE_ID":1062,"COMPLETED":"Y"},
            "select":["ID","OWNER_TYPE_ID","OWNER_ID","COMPLETED"],
            "start":0,
        })
        if type(activities) is not list or len(activities)>50:
            raise PortalReadBlocked("ACTIVITY_LIST_SHAPE_INVALID")
        if any(type(row) is not dict
               or str(row.get("OWNER_TYPE_ID"))!="1062"
               or str(row.get("COMPLETED")) not in ("Y","1","True")
               or not str(row.get("ID","")).isdigit()
               for row in activities):
            raise PortalReadBlocked("ACTIVITY_FILTER_NOT_PROVEN")
        out["completed_call_events_readable"]=True
        out["completed_activity_visible_first_page"]=(
            "NONEMPTY" if activities else "EMPTY")
        # Do not promote count to full CRM KPI: pagination and access unknown.
        out["completed_activity_total_verified"]=False
    except Exception:
        out["completed_call_events_readable"]=False
        out["completed_activity_visible_first_page"]="UNKNOWN"
        out["completed_activity_total_verified"]=False
    try:
        cats=safe_request(client,base,"crm.category.list",{"entityTypeId":2})
        if type(cats) is not dict or type(cats.get("categories")) is not list:
            raise PortalReadBlocked("DEAL_CATEGORIES_UNVERIFIED")
        categories=cats["categories"]
        matches=[r for r in categories
                 if type(r) is dict
                 and r.get("name")=="Лидогенерация"
                 and str(r.get("entityTypeId"))=="2"
                 and str(r.get("id","")).isdigit()]
        out["separate_sales_funnel_visible"]=(len(matches)==1)
        out["sales_funnel_unique"]=len(matches)==1
        out["commercial_funnel_found_or_absent"]=(
            "FOUND" if len(matches)==1
            else "NOT_VISIBLE_OR_NOT_CONFIGURED")
        if len(matches)==1:
            try:
                category_id=int(matches[0]["id"])
                if category_id==0:
                    raise PortalReadBlocked("DEFAULT_CATEGORY_CANNOT_BE_LEADGEN")
                out["sales_stage_audit"]=sales_stage_readback(
                    client,base,category_id)
            except Exception:
                out["sales_stage_audit"]={"status":"STAGE_READBACK_UNVERIFIED",
                                          "sales_stage_semantics_ready":False}
    except Exception:
        out["separate_sales_funnel_visible"]=False
        out["sales_funnel_unique"]=False
        out["commercial_funnel_found_or_absent"]="READ_UNVERIFIED"
    out.setdefault("sales_stage_audit",
                   {"status":"FUNNEL_NOT_VISIBLE_STAGE_CHECK_SKIPPED",
                    "sales_stage_semantics_ready":False})
    # Do not disclose internal names, IDs, customer names or amounts in logs.
    return out

def main():
    e=os.environ
    if (e.get("GITHUB_REPOSITORY")!="Ramzes2023/Service-IT-LeadGen"
        or e.get("GITHUB_REF")!="refs/heads/main"
        or e.get("GITHUB_EVENT_NAME") not in ("schedule","issue_comment")):
        print(json.dumps({"status":"PORTAL_PREFLIGHT_GITHUB_CONTEXT_BLOCKED",
                          "crm_writes":0}),flush=True)
        return 2
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            result=probe(client,e.get("SERVICEIT_BITRIX_WEBHOOK",""))
    except Exception:
        result={"status":"PORTAL_POSTCALL_PREFLIGHT_UNAVAILABLE",
                "crm_writes":0,"task_writes":0,
                "revenue_verified":False,"mrr_verified":False}
    print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if result.get("status")=="SERVICEIT_POSTCALL_FUNNEL_READONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
