"""LIVE corporate finance capability inspection, strictly read-only.

No raw financial values, customer names, deal IDs, webhook or document refs
are written to Actions logs. Standard OPPORTUNITY is an OFFER amount, not proof
of bank settlement. Only the isolated LeadGen deal category is relevant.
"""
from __future__ import annotations

import json
import os
import httpx

from .bitrix_metadata_audit import validate_webhook

CORPORATE="https://serviseit.bitrix24.ru/rest/"
METHODS={"crm.category.list", "crm.deal.fields", "tasks.task.getFields"}
EXPECTED_FIELDS={"OPPORTUNITY", "CURRENCY_ID", "STAGE_SEMANTIC_ID",
                 "ASSIGNED_BY_ID", "DATE_CREATE", "CATEGORY_ID"}
TASK_FIELDS={"TITLE", "DESCRIPTION", "RESPONSIBLE_ID", "UF_CRM_TASK"}
FIN_PREFIX="UF_CRM_SVCIT_FIN_"


class FinanceProbeBlocked(ValueError):
    pass


def need(ok,code):
    if not ok: raise FinanceProbeBlocked(code)


def call(client,base,method,args):
    need(method in METHODS and type(args) is dict, "FINANCE_AUDIT_METHOD_BLOCKED")
    try:
        r=client.post(base+"/"+method+".json",json=args,timeout=24,follow_redirects=False)
        need(r.status_code==200 and 0<len(r.content)<=500_000,
             "FINANCE_AUDIT_HTTP_UNVERIFIED")
        body=r.json()
        need(type(body) is dict and "error" not in body and "result" in body,
             "FINANCE_AUDIT_BODY_UNVERIFIED")
        return body["result"]
    except FinanceProbeBlocked: raise
    except (httpx.HTTPError,TypeError,ValueError):
        raise FinanceProbeBlocked("FINANCE_AUDIT_TRANSPORT_UNVERIFIED") from None


def audit(client,webhook):
    base=validate_webhook(webhook)
    need(base.startswith(CORPORATE),"NOT_CORPORATE_BITRIX")
    out={"status":"SERVICEIT_FINANCE_READONLY_SCHEMA",
         "payment_data_verified":False,
         "bank_settlement_source_available":False,
         "crm_writes":0,"tasks_created":0,
         "financial_data_logged":False}
    cats=call(client,base,"crm.category.list",{"entityTypeId":2})
    need(type(cats) is dict and type(cats.get("categories")) is list,
         "FINANCE_FUNNEL_LIST_UNVERIFIED")
    matched=[x for x in cats["categories"] if type(x) is dict
             and x.get("name")=="Лидогенерация" and str(x.get("entityTypeId"))=="2"
             and str(x.get("id","")).isdigit()]
    out["sales_funnel_visible"]=len(matched)==1
    out["sales_funnel_not_visible_or_unconfigured"]=len(matched)==0
    out["sales_funnel_duplicate_visible"]=len(matched)>1
    fields=call(client,base,"crm.deal.fields",{})
    need(type(fields) is dict,"FINANCE_CRM_DEAL_FIELDS_UNVERIFIED")
    out["standard_deal_fields"]={k:k in fields for k in sorted(EXPECTED_FIELDS)}
    out["manager_payment_expense_field_codes_present"]=[
        k for k in sorted(fields) if k.startswith(FIN_PREFIX)
        and len(k)<=80
    ]
    task=call(client,base,"tasks.task.getFields",{})
    need(type(task) is dict and type(task.get("fields")) is dict,
         "FINANCE_TASK_FIELDS_UNVERIFIED")
    out["task_fields_ready"]=all(k in task["fields"] for k in TASK_FIELDS)
    out["finance_field_configuration_needed"]=not out[
        "manager_payment_expense_field_codes_present"]
    return out


def main():
    e=os.environ
    if(e.get("GITHUB_REPOSITORY")!="Ramzes2023/Service-IT-LeadGen"
       or e.get("GITHUB_REF")!="refs/heads/main"
       or e.get("GITHUB_EVENT_NAME")!="issue_comment"):
        return 2
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            out=audit(client,e.get("SERVICEIT_BITRIX_WEBHOOK",""))
    except Exception:
        out={"status":"SERVICEIT_FINANCE_SCHEMA_READ_UNVERIFIED",
             "crm_writes":0,"tasks_created":0}
    print(json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if out["status"]=="SERVICEIT_FINANCE_READONLY_SCHEMA" else 2


if __name__=="__main__":
    raise SystemExit(main())
