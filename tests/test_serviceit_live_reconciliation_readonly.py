"""Contract and evidence tests for genuine read-only scheduled reconciliation."""
from datetime import datetime, timezone
import json
import httpx
import pytest

from leadpilot import serviceit_live_reconciliation_readonly as m
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_kanban_presentation import (
    SCORE_FIELD,VERSION_FIELD,BADGE_FIELD,
)

NOW = datetime(2026,11,5,12,0,tzinfo=timezone.utc)
WEBHOOK = "https://serviseit.bitrix24.ru/rest/1615/REDACTED"
ENV = {
    "SERVICEIT_BITRIX_WEBHOOK": WEBHOOK,
    "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_EVENT_NAME": "schedule",
}


def ready(monkeypatch, cards):
    monkeypatch.setattr(m,"validate_webhook",lambda x:WEBHOOK)
    monkeypatch.setattr(m,"calibrate_known_card",lambda fn:None)
    monkeypatch.setattr(m,"discover",lambda fn,start,now:cards)
    monkeypatch.setattr(m,"after_activation",lambda e,now:now)
    monkeypatch.setattr(m,"owner_type",lambda client,base:"T426")
    monkeypatch.setattr(m,"probe",lambda client,hook:{
        "manager_fields_ready":True,
        "separate_sales_funnel_visible":False,
        "commercial_funnel_found_or_absent":"NOT_VISIBLE_OR_NOT_CONFIGURED",
    })


def test_empty_real_new_cards_never_fabricates_signals_or_sales(monkeypatch):
    ready(monkeypatch,[])
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:pytest.fail("No direct item/details calls expected"))) as client:
        result=m.check(client,ENV,now=NOW)
    assert result["status"]=="SERVICEIT_RECONCILE_NO_REAL_NEW_CARDS"
    assert result["new_cards_visible"]==result["inspected"]==0
    assert result["crm_writes"]==result["tasks_created"]==0
    assert result["automatic_profit_payouts_authorized"] is False
    assert result["separate_sales_funnel_visible_to_webhook"] is False
    assert result["formal_verified_score_concluded"] is False


def test_full_ai_and_task_visible_but_score_not_formally_qualified(monkeypatch):
    ready(monkeypatch,[120,121,122])
    all_ai={field:"Утверждения только для проверки" for field in AI_FIELDS}
    first={"id":120,"assignedById":41,**all_ai,
           SCORE_FIELD:65,VERSION_FIELD:"svc-prelim-v1-FNS-UNVERIFIED",
           BADGE_FIELD:"🟡 65/100 — требуется проверка"}
    second={"id":121,"assignedById":None,**{
        next(iter(AI_FIELDS)):"Есть частичный AI отчёт"}}
    third={"id":122,"assignedById":42}
    data={120:first,121:second,122:third}
    monkeypatch.setattr(m,"detail",lambda client,base,id:data[id])
    def tasks(client,base,binding):
        if binding=="T426_121":
            raise ValueError("No task visibility")
        return 1 if binding=="T426_120" else 0
    monkeypatch.setattr(m,"visible_tasks",tasks)
    with httpx.Client() as client:
        result=m.check(client,ENV,now=NOW)
    assert result["status"]=="SERVICEIT_RECONCILE_NEEDS_REVIEW"
    assert result["new_cards_visible"]==3 and result["inspected"]==3
    assert result["ai_nine_complete"]==1
    assert result["ai_partial"]==1
    assert result["ai_absent"]==1
    assert result["provisional_or_other_score_fields_visible"]==1
    assert result["formal_verified_score_concluded"] is False
    assert result["assignee_missing"]==1
    assert result["linked_task_visible"]==1
    assert result["task_visibility_unknown"]==1
    assert result["no_linked_task_visible_not_global_absence"]==1
    assert result["crm_writes"]==result["tasks_created"]==0


def test_missing_list_visibility_must_not_be_reported_as_no_tasks(monkeypatch):
    ready(monkeypatch,[120])
    monkeypatch.setattr(m,"detail",lambda c,b,i:{"assignedById":12})
    monkeypatch.setattr(m,"owner_type",lambda c,b:(_ for _ in ()).throw(
        ValueError("task API rights missing")))
    with httpx.Client() as client:
        out=m.check(client,ENV,now=NOW)
    assert out["task_visibility_unknown"]==1
    assert out["no_linked_task_visible_not_global_absence"]==0
    assert out["status"]=="SERVICEIT_RECONCILE_NEEDS_REVIEW"


def test_detail_unverified_is_unknown_not_zero(monkeypatch):
    ready(monkeypatch,[120])
    monkeypatch.setattr(m,"detail",lambda c,b,i:(_ for _ in ()).throw(
        ValueError("CRM permission")))
    with httpx.Client() as client:
        out=m.check(client,ENV,now=NOW)
    assert out["detail_unverified"]==1
    assert out["inspected"]==0
    assert out["status"]=="SERVICEIT_RECONCILE_PARTIAL_VISIBILITY"


def test_results_are_capped_and_later_cards_pending(monkeypatch):
    ready(monkeypatch,list(range(120,140)))
    monkeypatch.setattr(m,"detail",lambda c,b,i:{"assignedById":0})
    monkeypatch.setattr(m,"visible_tasks",lambda c,b,binding:0)
    with httpx.Client() as client:
        out=m.check(client,ENV,now=NOW)
    assert out["inspected"]==m.MAX_INSPECT
    assert out["pending_next_run"]==8
    assert out["status"]=="SERVICEIT_RECONCILE_PARTIAL_VISIBILITY"


def test_schedule_and_exact_owner_marker_only(tmp_path):
    m.allow_context(ENV)
    p=tmp_path/"evt.json"
    evt={
        "action":"created",
        "repository":{"full_name":"Ramzes2023/Service-IT-LeadGen"},
        "issue":{"number":148},
        "sender":{"login":"Ramzes2023"},
        "comment":{"body":m.ONCE,"user":{"login":"Ramzes2023"}},
    }
    p.write_text(json.dumps(evt),encoding="utf8")
    m.allow_context({**ENV,"GITHUB_EVENT_NAME":"issue_comment",
                     "GITHUB_ACTOR":"Ramzes2023","GITHUB_EVENT_PATH":str(p)})
    for wrong in (
        {"GITHUB_ACTOR":"other"},
        {"GITHUB_EVENT_PATH":""},
        {"GITHUB_REPOSITORY":"evil/repo"},
        {"GITHUB_REF":"refs/heads/other"},
    ):
        with pytest.raises(m.ReconciliationBlocked):
            m.allow_context({**ENV,"GITHUB_EVENT_NAME":"issue_comment",
                             "GITHUB_ACTOR":"Ramzes2023",
                             "GITHUB_EVENT_PATH":str(p),**wrong})
    evt["comment"]["body"]="SERVICEIT_FINANCE_PAYOUT_NOW"
    p.write_text(json.dumps(evt),encoding="utf8")
    with pytest.raises(m.ReconciliationBlocked):
        m.allow_context({**ENV,"GITHUB_EVENT_NAME":"issue_comment",
                         "GITHUB_ACTOR":"Ramzes2023",
                         "GITHUB_EVENT_PATH":str(p)})


def test_safelist_does_not_write_to_crm_or_contact_people():
    from pathlib import Path
    source=Path("leadpilot/serviceit_live_reconciliation_readonly.py").read_text("utf8")
    assert '"/crm.item.get.json"' in source
    for method in ("crm.item.update.json","crm.item.add.json",
                   "crm.deal.add.json","crm.activity.add.json",
                   "tasks.task.add.json","crm.category.add.json"):
        assert method not in source
