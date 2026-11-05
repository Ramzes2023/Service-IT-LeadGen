"""Only new closed-won LeadGen sales can open internal finance tasks."""
from datetime import datetime, timezone
import httpx
import pytest

from leadpilot import serviceit_finance_hybrid_live_worker as m
from leadpilot import serviceit_finance_metadata_readonly as schema
from leadpilot.serviceit_finance_hybrid import (
    AUTOMATIC, BEGIN, END, STATUS_MISSING, STATUS_DRAFT,
)

NOW=datetime(2026,11,5,14,0,tzinfo=timezone.utc)
ENV={
    "GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
    "GITHUB_REF":"refs/heads/main",
    "SERVICEIT_FINANCE_MANAGER_TASK_ENABLED":m.ENABLED,
    "SERVICEIT_BITRIX_WEBHOOK":"corporate-redacted",
}


def test_not_visible_sales_funnel_never_reads_support_deals(monkeypatch):
    monkeypatch.setattr(m,"validate_webhook",lambda w:m.PORTAL+"1615/redacted")
    monkeypatch.setattr(m,"exact_category",lambda client,base:None)
    monkeypatch.setattr(m,"list_won",
                        lambda *args:pytest.fail("Must not scan support"))
    with httpx.Client() as client:
        result=m.run(client,ENV,now=NOW)
    assert result["status"]=="FINANCE_LEADGEN_FUNNEL_NOT_VISIBLE_NO_WRITE"
    assert result["payouts"]==result["crm_item_writes"]==0
    assert result["tasks_created_and_verified"]==0


def install_fake_sales(monkeypatch,deal):
    monkeypatch.setattr(m,"validate_webhook",lambda w:m.PORTAL+"1615/redacted")
    monkeypatch.setattr(m,"exact_category",lambda client,base:4)
    monkeypatch.setattr(m,"deal_schema",lambda *args:None)
    monkeypatch.setattr(m,"task_schema",lambda *args:None)
    monkeypatch.setattr(m,"list_won",lambda client,base,cat:[123])
    monkeypatch.setattr(m,"current_employee",lambda client,base:1615)
    monkeypatch.setattr(m,"read_deal",
                        lambda client,base,cat,id,now:(deal,41))
    monkeypatch.setattr(m,"verify_employee",lambda *args:None)


def fake_comment(paid="5000",costs="1000"):
    return "\n".join([BEGIN,
        "PAID_EX_VAT_RUB="+paid,
        "DIRECT_COSTS_RUB="+costs,
        "PAYMENT_DOCUMENT=bank:10001",
        "COST_DOCUMENT=cost:10001",
        END])


def test_missing_data_creates_only_internal_input_request(monkeypatch):
    install_fake_sales(monkeypatch,{"COMMENTS":""})
    monkeypatch.setattr(m,"linked_tasks",lambda *args:[])
    wrote=[]
    monkeypatch.setattr(m,"create_task",
                        lambda client,base,env,deal,manager,creator,kind,description:
                        (wrote.append((deal,manager,creator,kind,description))
                         or "TASK_CREATED_AND_VERIFIED"))
    with httpx.Client() as c:
        out=m.run(c,ENV,now=NOW)
    assert out["input_missing_or_conflict"]==1
    assert out["draft_calculated"]==0
    assert out["tasks_created_and_verified"]==1
    assert len(wrote)==1 and wrote[0][3]=="request"
    assert "PAID_EX_VAT_RUB=" in wrote[0][-1]
    assert "DIRECT_COSTS_RUB=" in wrote[0][-1]
    assert out["payouts"]==out["outbound_customer_contacts"]==0


def test_manager_filled_real_deal_comments_trigger_internal_result(monkeypatch):
    install_fake_sales(monkeypatch,{"COMMENTS":fake_comment()})
    monkeypatch.setattr(m,"linked_tasks",lambda *args:[])
    wrote=[]
    monkeypatch.setattr(m,"create_task",
                        lambda client,base,env,deal,manager,creator,kind,description:
                        (wrote.append((kind,description))
                         or "TASK_CREATED_AND_VERIFIED"))
    with httpx.Client() as c:
        out=m.run(c,ENV,now=NOW)
    assert out["draft_calculated"]==1
    assert out["tasks_created_and_verified"]==1
    assert wrote[0][0]=="report"
    assert "Разработчику 15%: 600.00 ₽" in wrote[0][1]
    assert "Продающему менеджеру 10%: 400.00 ₽" in wrote[0][1]
    assert "Компании 75% от положительной РВП: 3000.00 ₽" in wrote[0][1]
    assert "ВЫПЛАТЫ НЕ РАЗРЕШЕНЫ" in wrote[0][1]


def test_when_manager_updates_amounts_reporting_task_is_refreshed(monkeypatch):
    install_fake_sales(monkeypatch,{"COMMENTS":fake_comment(paid="6000")})
    fake_report={"id":"999","title":m.titles(123)["report"]}
    monkeypatch.setattr(m,"linked_tasks",lambda *args:[fake_report])
    monkeypatch.setattr(m,"our_task",lambda *args:"old")
    updated=[]
    monkeypatch.setattr(m,"update_report",
                        lambda client,base,env,deal,id,manager,creator,description:
                        (updated.append((id,description))
                         or "REPORT_UPDATED_VERIFIED"))
    with httpx.Client() as c:
        out=m.run(c,ENV,now=NOW)
    assert out["reports_updated_and_verified"]==1
    assert updated[0][0]==999
    assert "Разработчику 15%: 750.00 ₽" in updated[0][1]


def test_finance_task_is_never_created_for_historical_or_other_funnel(monkeypatch):
    deal={"ID":"123","CATEGORY_ID":"3","STAGE_SEMANTIC_ID":"S",
          "CURRENCY_ID":"RUB","ASSIGNED_BY_ID":"41",
          "DATE_CREATE":"2026-11-05T13:00:00+00:00"}
    monkeypatch.setattr(m,"call",lambda *args:{"result":deal})
    with pytest.raises(m.FinanceWorkerBlocked,match="NOT_ISOLATED"):
        m.read_deal(None,"",4,123,NOW)
    deal["CATEGORY_ID"]="4"
    deal["DATE_CREATE"]="2026-11-04T13:00:00+00:00"
    with pytest.raises(m.FinanceWorkerBlocked,match="HISTORICAL"):
        m.read_deal(None,"",4,123,NOW)


def test_never_process_unpaid_deal(monkeypatch):
    deal={"ID":"123","CATEGORY_ID":"4","STAGE_SEMANTIC_ID":"P",
          "CURRENCY_ID":"RUB","ASSIGNED_BY_ID":"41",
          "DATE_CREATE":"2026-11-05T13:00:00+00:00"}
    monkeypatch.setattr(m,"call",lambda *args:{"result":deal})
    with pytest.raises(m.FinanceWorkerBlocked,match="NOT_ISOLATED"):
        m.read_deal(None,"",4,123,NOW)


def test_task_rest_allowlist_rejects_changing_support_or_customer(monkeypatch):
    with pytest.raises(m.FinanceWorkerBlocked,match="METHOD_NOT_ALLOWED"):
        m.call(None,"","crm.deal.update",{})
    with pytest.raises(m.FinanceWorkerBlocked,match="OUTSIDE_SCOPE"):
        m.call(None,"","crm.deal.list",{"filter":{"CATEGORY_ID":0}})
    with pytest.raises(m.FinanceWorkerBlocked,match="OUTSIDE_SCOPE"):
        m.call(None,"","tasks.task.add",{"fields":{"TITLE":"x"}})
    assert "crm.item.update" not in m.REST_ALLOW
    assert "crm.deal.add" not in m.REST_ALLOW
    assert "crm.deal.update" not in m.REST_ALLOW


def test_readonly_schema_no_payout_claim(monkeypatch):
    monkeypatch.setattr(schema,"validate_webhook",
                        lambda w:"https://serviseit.bitrix24.ru/rest/1615/redacted")
    def fake(client,base,method,args):
        if method=="crm.category.list":
            return {"categories":[{"id":0,"entityTypeId":2,"name":"Общая"}]}
        if method=="crm.deal.fields":
            return {"OPPORTUNITY":{},"CATEGORY_ID":{},"DATE_CREATE":{},
                    "CURRENCY_ID":{},"ASSIGNED_BY_ID":{},
                    "STAGE_SEMANTIC_ID":{}}
        if method=="tasks.task.getFields":
            return {"fields":{"TITLE":{},"DESCRIPTION":{},
                              "RESPONSIBLE_ID":{},"UF_CRM_TASK":{}}}
        pytest.fail("outside allowlist")
    monkeypatch.setattr(schema,"call",fake)
    with httpx.Client() as c:
        r=schema.audit(c,"secret")
    assert r["sales_funnel_visible"] is False
    assert r["payment_data_verified"] is False
    assert r["bank_settlement_source_available"] is False
    assert r["task_fields_ready"] is True
    assert r["finance_field_configuration_needed"] is True


def test_missing_docs_after_manager_fills_amounts_updates_existing_request(monkeypatch):
    # Manager has already entered numeric values; only finance document refs
    # still require manual completion. Existing request gets updated, no
    # repeated creation, and the calculated result appears as a second task.
    install_fake_sales(monkeypatch,{"COMMENTS":"\n".join([
        BEGIN,"PAID_EX_VAT_RUB=5000","DIRECT_COSTS_RUB=1000",END])})
    old={"id":"777","title":m.titles(123)["request"]}
    monkeypatch.setattr(m,"linked_tasks",lambda *args:[old])
    monkeypatch.setattr(m,"our_task",lambda *args:"previous text")
    revisions,created=[],[]
    monkeypatch.setattr(m,"update_request",
                        lambda client,base,env,deal,id,manager,creator,description:
                        (revisions.append((id,description))
                         or "REQUEST_UPDATED_VERIFIED"))
    monkeypatch.setattr(m,"create_task",
                        lambda client,base,env,deal,manager,creator,kind,description:
                        (created.append(kind)
                         or "TASK_CREATED_AND_VERIFIED"))
    with httpx.Client() as c:
        out=m.run(c,ENV,now=NOW)
    assert out["manager_requests_updated_and_verified"]==1
    assert revisions[0][0]==777
    assert "PAYMENT_DOCUMENT=" in revisions[0][1]
    assert "COST_DOCUMENT=" in revisions[0][1]
    assert "PAID_EX_VAT_RUB=" not in revisions[0][1]
    assert created==["report"]


def test_report_with_old_amounts_is_replaced_by_hold_on_incomplete_data(monkeypatch):
    install_fake_sales(monkeypatch,{"COMMENTS":""})
    prior={"id":"888","title":m.titles(123)["report"]}
    monkeypatch.setattr(m,"linked_tasks",lambda *args:[prior])
    changed=[]
    monkeypatch.setattr(m,"create_task",
                        lambda *args:"TASK_CREATED_AND_VERIFIED")
    monkeypatch.setattr(m,"our_task",lambda *args:"prior")
    monkeypatch.setattr(m,"update_report",
                        lambda client,base,env,deal,id,manager,creator,description:
                        (changed.append(description) or "REPORT_UPDATED_VERIFIED"))
    with httpx.Client() as c:
        out=m.run(c,ENV,now=NOW)
    assert out["reports_updated_and_verified"]==1
    assert "СТОП:" in changed[0]
    assert "600.00" not in changed[0]
    assert "Выплаты не разрешены" in changed[0]


def test_encrypted_one_use_fence_prevents_ambiguous_replay(monkeypatch):
    state={"reserved":False,"writes":0}
    class FakeFence:
        def __init__(self,*args): pass
        def reserve(self,deal):
            if state["reserved"]:return "ALREADY_FENCED_NO_RETRY"
            state["reserved"]=True
            return "RESERVED"
        def finalize(self,deal,status):return True
    monkeypatch.setattr(m,"FinanceFence",FakeFence)
    monkeypatch.setattr(m,"call",lambda *args:{"result":{"task":{"id":789}}})
    monkeypatch.setattr(m,"our_task",lambda *args:m.TASK_MARKER+"\n"+m.manager_template(
        123,["PAID_EX_VAT_RUB"]))
    description=m.TASK_MARKER+"\n"+m.manager_template(123,["PAID_EX_VAT_RUB"])
    with httpx.Client() as c:
        first=m.create_task(c,m.PORTAL,ENV,123,41,1615,
                            "request",description)
        second=m.create_task(c,m.PORTAL,ENV,123,41,1615,
                             "request",description)
    assert first=="TASK_CREATED_AND_VERIFIED"
    assert second=="ALREADY_FENCED_NO_RETRY"


def test_task_report_never_claims_manager_comment_as_bank_verified():
    from leadpilot.serviceit_finance_hybrid import reconcile
    out=reconcile({},fake_comment())
    text=m.report_text(out)
    assert "ПРЕДВАРИТЕЛЬНЫЙ" in text
    assert "не являются банковским подтверждением" in text
    assert "ВЫПЛАТЫ НЕ РАЗРЕШЕНЫ" in text
    assert "600.00 ₽" in text
