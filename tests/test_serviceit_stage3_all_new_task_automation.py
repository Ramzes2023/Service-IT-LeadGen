"""Stage 3 automatic all-future-card internal tasks: no duplicate or wrong portal."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import httpx
import pytest

from leadpilot import serviceit_stage3_all_new_task_automation as auto

NOW=datetime(2026,11,4,11,0,tzinfo=timezone.utc)
WEBHOOK="https://serviseit.bitrix24.ru/rest/123/FAKE_TOKEN_ONLY"
ENV={
    "GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
    "GITHUB_REF":"refs/heads/main",
    "SERVICEIT_STAGE3_TASK_WRITE_ENABLED":"YES_1062_21_INTERNAL_REVIEW",
    "SERVICEIT_BITRIX_WEBHOOK":WEBHOOK,
    "SERVICEIT_GH_LEDGER_KEY":"TEST_FAKE_SECRET",
    "GITHUB_TOKEN":"TEST_FAKE_TOKEN",
}

class Fence:
    seen=set()
    count=0
    states=[]
    def __init__(self,*args,**kwargs):
        pass
    def reserve(self,card):
        self.states.append("reserve")
        if card in self.seen:
            return "ALREADY_FENCED_NO_RETRY"
        self.seen.add(card)
        type(self).count+=1
        return "RESERVED"
    def finalize(self,card,result):
        self.states.append(result)
        return True

def prepare(monkeypatch,*,visible=0,uid=42,model=None,task_add_fail=False,
            assignee="ONE_ACTIVE_ASSIGNED_EMPLOYEE_UNAPPROVED"):
    Fence.seen=set()
    Fence.count=0
    Fence.states=[]
    calls=[]
    item={"id":120,"entityTypeId":1062,"categoryId":21,
          "createdTime":NOW.isoformat(),
          "assignedById":uid,"stageId":"DT1062_21:NEW"}
    monkeypatch.setattr(auto,"card_read",lambda client,base,card,now:item.copy())
    monkeypatch.setattr(auto,"check_task_binding",lambda client,base:calls.append("binding"))
    monkeypatch.setattr(auto,"owner_type",lambda client,base:"T426")
    monkeypatch.setattr(auto,"visible_tasks",lambda client,base,binding:(
        calls.append(("visible",binding)) or visible))
    monkeypatch.setattr(auto,"assigned_employee",
                        lambda client,base,assigned:assignee)
    monkeypatch.setattr(auto,"TaskFence",Fence)
    def request(client,base,method,args):
        calls.append((method,args))
        if method=="tasks.task.add":
            if task_add_fail:
                raise httpx.ReadTimeout("ambiguous post")
            assert args["fields"]["RESPONSIBLE_ID"]==uid
            assert args["fields"]["UF_CRM_TASK"]==["T426_120"]
            assert "НЕ звонить" in args["fields"]["DESCRIPTION"]
            return {"task":{"id":"26234"}}
        pytest.fail("Unexpected REST write")
    monkeypatch.setattr(auto,"rest",request)
    monkeypatch.setattr(auto,"task_get_confirmed",
                        lambda c,b,tid,binding,uid,title:calls.append(("readback",tid)))
    return calls

def test_new_card_creates_one_internal_task_with_readback_and_encrypted_fence(monkeypatch):
    calls=prepare(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("unexpected HTTP"))) as client:
        one=auto.process_card(client,ENV,120,now=NOW)
        two=auto.process_card(client,ENV,120,now=NOW)
    assert one=="INTERNAL_REVIEW_TASK_READBACK_VERIFIED"
    assert two=="ALREADY_RESERVED_NO_RETRY"
    assert Fence.count==1
    assert len([v for v in calls if isinstance(v,tuple) and v[0]=="tasks.task.add"])==1
    assert len([v for v in calls if isinstance(v,tuple) and v[0]=="readback"])==1
    assert Fence.states==["reserve","VERIFIED","reserve"]

def test_existing_visible_task_never_creates_another(monkeypatch):
    calls=prepare(monkeypatch,visible=1)
    with httpx.Client() as client:
        assert auto.process_card(client,ENV,120,now=NOW)=="VISIBLE_TASK_EXISTS_NO_WRITE"
    assert Fence.count==0
    assert not any(isinstance(c,tuple) and c[0]=="tasks.task.add" for c in calls)

def test_ambiguous_task_post_is_never_replayed(monkeypatch):
    calls=prepare(monkeypatch,task_add_fail=True)
    with httpx.Client() as client:
        one=auto.process_card(client,ENV,120,now=NOW)
        two=auto.process_card(client,ENV,120,now=NOW)
    assert one=="TASK_WRITE_UNCERTAIN_NO_RETRY"
    assert two=="ALREADY_RESERVED_NO_RETRY"
    assert len([v for v in calls if isinstance(v,tuple) and v[0]=="tasks.task.add"])==1
    assert Fence.states==["reserve","UNCERTAIN","reserve"]

@pytest.mark.parametrize("changes",[
    {"SERVICEIT_STAGE3_TASK_WRITE_ENABLED":"NO"},
    {"GITHUB_REF":"refs/heads/other"},
    {"GITHUB_REPOSITORY":"elsewhere/repo"},
    {"SERVICEIT_BITRIX_WEBHOOK":"https://elsewhere.bitrix24.ru/rest/1/TOKEN"},
])
def test_no_unauthorized_portal_or_host_task(monkeypatch,changes):
    calls=prepare(monkeypatch)
    env={**ENV,**changes}
    with httpx.Client() as client:
        with pytest.raises(Exception):
            auto.process_card(client,env,120,now=NOW)
    assert Fence.count==0 and calls==[]

@pytest.mark.parametrize("invalid_id",[3,7,0,-1,2_000_000_001])
def test_historical_or_unbounded_id_blocks_before_fence(monkeypatch,invalid_id):
    calls=prepare(monkeypatch)
    monkeypatch.setattr(auto,"card_read",
                        lambda client,base,card,now:
                        (_ for _ in ()).throw(auto.TaskAutomationBlocked("HISTORICAL")))
    with httpx.Client() as client:
        with pytest.raises(auto.TaskAutomationBlocked):
            auto.process_card(client,ENV,invalid_id,now=NOW)
    assert Fence.count==0

def test_unverified_assignee_stays_pending_without_fence(monkeypatch):
    calls=prepare(monkeypatch,assignee="ASSIGNEE_NOT_VERIFIED")
    with httpx.Client() as client:
        with pytest.raises(auto.TaskAutomationBlocked,match="NO_ACTIVE_CARD_ASSIGNEE"):
            auto.process_card(client,ENV,120,now=NOW)
    assert Fence.count==0

def test_cutover_rejects_preexisting_card(monkeypatch):
    monkeypatch.setattr(auto,"rest",lambda client,base,method,args:{
        "item":{"id":120,"entityTypeId":1062,"categoryId":21,
                "createdTime":"2026-11-03T15:00:00+00:00"}
    })
    with httpx.Client() as client:
        with pytest.raises(auto.TaskAutomationBlocked,match="CARD_BEFORE_TASK_AUTOMATION_CUTOVER"):
            auto.card_read(client,WEBHOOK,120,now=NOW)

def test_cron_scans_every_new_id_with_separate_one_time_task_gate(monkeypatch):
    called=[]
    monkeypatch.setattr(auto,"after_activation",lambda env,now:auto.CUTOVER)
    monkeypatch.setattr(auto,"discover",lambda rest,start,now:[120,121,122])
    monkeypatch.setattr(auto,"process_card",
                        lambda client,env,card,now:
                        (called.append(card) or {
                           120:"INTERNAL_REVIEW_TASK_READBACK_VERIFIED",
                           121:"ALREADY_RESERVED_NO_RETRY",
                           122:"VISIBLE_TASK_EXISTS_NO_WRITE",
                        }[card]))
    with httpx.Client() as client:
        result=auto.scan_cards(client,{**ENV,"SERVICEIT_BITRIX_POLL_START_UTC":
                      auto.CUTOVER.isoformat()},now=NOW)
    assert called==[120,121,122]
    assert result["candidates"]==3
    assert result["created_and_verified"]==1
    assert result["already_fenced"]==1
    assert result["existing_visible"]==1

def test_workflow_is_generic_event_plus_cron_and_only_our_scope():
    w=Path(".github/workflows/serviceit-stage3-all-new-task-automation.yml").read_text("utf8")
    assert "repository_dispatch:" in w
    assert "schedule:" in w
    assert "serviceit_bitrix_item_add_v1" in w
    assert "SERVICEIT_STAGE3_TASK_WRITE_ENABLED: YES_1062_21_INTERNAL_REVIEW" in w
    assert "serviceit-bitrix-new-card-free-event" in w
    assert "sourceit_b2b" not in w
    assert "contents: write" in w
