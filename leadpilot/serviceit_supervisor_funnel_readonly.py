"""Evidence-first real supervisor funnel: read-only aggregate, never invent sales.

Use only new 1062/21 signals. Completed activities are NOT proof of contact.
Contracts, billed amounts and MRR need separate linked finance evidence.
"""
from datetime import datetime, timezone
import json
import os
import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import after_activation, calibrate_known_card, discover
from .serviceit_postcall_funnel_scope_readonly import probe

def calculate(client, env, *, now=None):
    now=now or datetime.now(timezone.utc)
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    if not base.startswith("https://serviseit.bitrix24.ru/rest/"):
        raise ValueError("WRONG_CRM_PORTAL")
    rest=lambda method,args: list_only(client,base,method,args)
    calibrate_known_card(rest)
    found=discover(rest,after_activation(env,now),now)
    rights=probe(client,env["SERVICEIT_BITRIX_WEBHOOK"])
    return {
        "status":"SERVICEIT_MANAGER_FUNNEL_READONLY",
        "period":"NEW_SIGNAL_CUTOVER_ONWARDS",
        "found_visible_new_signals":len(found),
        "qualified_leads":None,
        "actual_contacts":None,
        "confirmed_interest":None,
        "proposals":None,
        "contracts":None,
        "paid_revenue":None,
        "mrr":None,
        "contact_events_are_not_proof":True,
        "separate_deal_funnel_ready":rights["separate_sales_funnel_visible"],
        "postcall_fields_ready":rights["manager_fields_ready"],
        "complete_revenue_evidence":False,
        "crm_writes":0,"task_writes":0,
    }

def main():
    e=dict(os.environ)
    if (e.get("GITHUB_REPOSITORY")!="Ramzes2023/Service-IT-LeadGen"
        or e.get("GITHUB_REF")!="refs/heads/main"
        or e.get("GITHUB_EVENT_NAME") not in ("issue_comment","schedule")):
        return 2
    try:
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            report=calculate(client,e)
    except Exception:
        report={"status":"SERVICEIT_FUNNEL_READ_UNVERIFIED",
                "crm_writes":0,"revenue_verified":False}
    print(json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    with open(os.environ.get("GITHUB_STEP_SUMMARY","/dev/null"),"a") as out:
        out.write("## Сервис ИТ — реальная воронка (только чтение)\n")
        out.write("Найденные сигналы в доступной CRM: "+str(report.get("found_visible_new_signals","не подтверждено"))+"\n\n")
        out.write("Квалификация → контакт → интерес → КП → договор → выручка/MRR: **нет подтверждённой сквозной связи; не ноль**.\n")
        out.write("Изменений CRM: 0.\n")
    return 0 if report["status"]=="SERVICEIT_MANAGER_FUNNEL_READONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
