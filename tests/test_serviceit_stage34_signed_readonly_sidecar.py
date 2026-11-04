"""Stage3/4 signed event sidecar: no write privileges, bounded read only."""
from __future__ import annotations

import hmac
from hashlib import sha256
import json
import time
from uuid import uuid4
from pathlib import Path

import httpx
import pytest

from leadpilot import serviceit_stage34_signed_readonly_sidecar as sidecar
from leadpilot.serviceit_bitrix_dispatch_worker import EVENT, PORTAL
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

WEBHOOK="https://serviseit.bitrix24.ru/rest/123/FAKE_PUBLIC_TEST_TOKEN"
HMAC="TEST_SIGNING_KEY_0123456789_123456789abcdef"
CARD=120


def event(*, card=CARD, now=None):
    now=int(time.time()) if now is None else now
    nonce=str(uuid4())
    msg=f"v1|{PORTAL}|1062|{card}|{now}|{nonce}"
    return {
        "action":EVENT,
        "repository":{"full_name":"Ramzes2023/Service-IT-LeadGen"},
        "client_payload":{
            "version":1, "portal":PORTAL, "entity_type_id":1062,
            "item_id":card, "timestamp":now, "nonce":nonce,
            "signature":"sha256="+hmac.new(HMAC.encode(),msg.encode(),sha256).hexdigest()
        },
    }


def env():
    return {"GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
            "GITHUB_REF":"refs/heads/main",
            "GITHUB_EVENT_NAME":"repository_dispatch",
            "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY":HMAC,
            "SERVICEIT_BITRIX_WEBHOOK":WEBHOOK}


def adapter(req, *, category=21, linked=None, employee=True, no_manager=False,
            incomplete_tasks=False):
    assert req.method=="POST" and req.url.host=="serviseit.bitrix24.ru"
    method=req.url.path.rsplit("/",1)[-1]
    args=json.loads(req.content)
    if method=="crm.item.get.json":
        assert args=={"entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y"}
        ai={k:"" for k in AI_FIELDS}
        ai[PREFIX+"AI_STATUS"]="NOT_RUN_SOURCE_UNVERIFIED"
        ai[PREFIX+"AI_SUMMARY"]="📑 ТЕНДЕР · HOLD"
        return httpx.Response(200,json={"result":{"item":{
            "id":CARD,"entityTypeId":1062,"categoryId":category,
            "assignedById":None if no_manager else 42,"title":"Проверка",
            **ai}}})
    if method=="crm.enum.ownertype.json":
        assert args=={}
        return httpx.Response(200,json={"result":[
            {"ID":1062,"SYMBOL_CODE_SHORT":"T426"}]})
    if method=="tasks.task.list.json":
        assert args=={"filter":{"UF_CRM_TASK":"T426_120"},
                      "select":["ID","UF_CRM_TASK","RESPONSIBLE_ID"],
                      "order":{"ID":"ASC"},"start":0}
        rows=linked if linked is not None else []
        return httpx.Response(200,json={"result":{"tasks":rows},
                 **({} if incomplete_tasks else {"total":len(rows)})})
    if method=="user.get.json":
        assert args=={"FILTER":{"ID":42,"ACTIVE":True},
                      "select":["ID","ACTIVE","USER_TYPE"],"start":0}
        rows=[{"ID":42,"ACTIVE":True,"USER_TYPE":"employee"}] if employee else []
        return httpx.Response(200,json={"result":rows,"total":len(rows)})
    pytest.fail(f"Forbidden REST method {method}")


def test_good_new_signed_event_observes_without_tasks_or_score():
    seen=[]
    def route(req):
        seen.append(req.url.path.rsplit("/",1)[-1])
        return adapter(req)
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        out=sidecar.run(event(),env(),client)
    assert set(seen)=={"crm.item.get.json","crm.enum.ownertype.json",
                        "tasks.task.list.json","user.get.json"}
    assert len(seen)==4
    assert out["status"]=="SERVICEIT_STAGE34_READONLY_EVENT_OBSERVED"
    assert out["scope"]=="SERVICEIT_1062_21_ONLY"
    assert out["linked_task_count_visible_to_webhook"]==0
    assert out["global_task_dedup_verified"] is False
    assert out["assignee"]=="ONE_ACTIVE_ASSIGNED_EMPLOYEE_UNAPPROVED"
    assert out["assignee_authorized_for_new_task"] is False
    assert out["ai_fields_present"]==2
    assert out["formal_score"] is None
    assert out["qualification"]=="HOLD_UNTIL_SIGNED_9_GATE_VERIFICATION"
    assert out["stage3_task_created"] is False
    assert out["stage4_score_written"] is False
    assert out["crm_writes"]==0
    assert "120" not in json.dumps(out)
    assert "42" not in json.dumps(out)


def test_existing_exact_linked_task_is_visible_not_duplicated():
    tasks=[{"id":"26164","ufCrmTask":["T426_120"],"responsibleId":"42"}]
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:adapter(req,linked=tasks))) as client:
        out=sidecar.run(event(),env(),client)
    assert out["task_visibility"]=="SOME_LINKED_TASKS_VISIBLE"
    assert out["linked_task_count_visible_to_webhook"]==1
    assert out["global_task_dedup_verified"] is False
    assert out["stage3_task_created"] is False


def test_no_assignee_has_explicit_blocker_but_no_user_lookup():
    called=[]
    def route(req):
        called.append(req.url.path)
        return adapter(req,no_manager=True)
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        out=sidecar.run(event(),env(),client)
    assert out["assignee"]=="NO_ASSIGNEE_ON_CARD"
    assert not any(x.endswith("user.get.json") for x in called)


def test_bad_hmac_blocks_before_all_rest():
    signed=event()
    signed["client_payload"]["signature"]="sha256"+"0"*64
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("wrong signature must not access Bitrix"))) as client:
        with pytest.raises(Exception):
            sidecar.run(signed,env(),client)


@pytest.mark.parametrize("bad",[
    {"GITHUB_REF":"refs/heads/other"},
    {"GITHUB_EVENT_NAME":"workflow_dispatch"},
    {"GITHUB_REPOSITORY":"another/owner"},
])
def test_bad_host_scope_no_rest(bad):
    e=env()
    e.update(bad)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("bad host scope"))) as client:
        with pytest.raises(sidecar.ObserverBlocked,match="GITHUB_EVENT_SCOPE_NOT_ALLOWED"):
            sidecar.run(event(),e,client)


def test_historic_card_rejected_before_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("historic card"))) as client:
        with pytest.raises(sidecar.ObserverBlocked,match="CARD_HISTORIC_OR_INVALID"):
            sidecar.inspect(client,WEBHOOK,3)


def test_category_mismatch_never_reads_tasks_or_user():
    methods=[]
    def route(req):
        methods.append(req.url.path)
        return adapter(req,category=22)
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        with pytest.raises(Exception):
            sidecar.run(event(),env(),client)
    assert len(methods)==1


def test_broken_tasks_list_cannot_prove_empty():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:adapter(req,incomplete_tasks=True))) as client:
        with pytest.raises(sidecar.ObserverBlocked,
                           match="TASK_LIST_TOTAL_UNVERIFIED"):
            sidecar.run(event(),env(),client)


def test_task_link_incorrect_cannot_be_reported_as_existing():
    tasks=[{"id":"26164","ufCrmTask":["T426_888"],"responsibleId":"42"}]
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:adapter(req,linked=tasks))) as client:
        with pytest.raises(sidecar.ObserverBlocked,match="TASK_LINK_NOT_EXACT"):
            sidecar.run(event(),env(),client)


def test_workflow_readonly_with_no_writing_permissions():
    workflow=Path(".github/workflows/serviceit-stage34-signed-readonly-sidecar.yml").read_text("utf-8")
    assert "repository_dispatch:" in workflow
    assert "serviceit_bitrix_item_add_v1" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "tasks.task.add" not in workflow
    assert "crm.item.update" not in workflow
    assert "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY:" in workflow


def test_stage4_all_future_cards_hold_without_external_receipt():
    from leadpilot import serviceit_stage34_signed_readonly_sidecar as sidecar
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: pytest.fail("no unverified proof may cause extra REST"))) as client:
        result=sidecar.stage4_signed_receipt_summary(client, {}, 120)
    assert result["stage4_proof"]=="NO_INDEPENDENT_RECEIPT_HOLD"
    assert result["stage4_formal_score_in_memory"] is False


def test_stage4_receipt_is_per_card_never_reused(monkeypatch):
    from leadpilot import serviceit_stage34_signed_readonly_sidecar as sidecar
    called=[]
    from leadpilot import serviceit_stage4_live_proof_readonly as live
    def verified(client, webhook, item_id, receipt, key):
        called.append((item_id,receipt,key))
        return {"status":"MANAGER_REVIEW_REQUIRED","formal_lead_score":83}
    monkeypatch.setattr(live,"review_signed_card_readonly",verified)
    env={"SERVICEIT_BITRIX_WEBHOOK":"https://serviseit.bitrix24.ru/rest/123/FAKE",
         "SERVICEIT_STAGE4_VERIFIER_KEY":"some-test-key",
         "SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON":json.dumps({"120":{"receipt":"fake fixture"}})}
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("mocked reviewer should not call network"))) as client:
        for other in (121,122):
            result=sidecar.stage4_signed_receipt_summary(client,env,other)
            assert result["stage4_formal_score_in_memory"] is False
        exact=sidecar.stage4_signed_receipt_summary(client,env,120)
        assert exact["stage4_formal_score_in_memory"] is True
    assert called==[(120,{"receipt":"fake fixture"},"some-test-key")]


def test_stage4_only_one_secret_is_fail_closed_without_model_or_rest():
    from leadpilot import serviceit_stage34_signed_readonly_sidecar as sidecar
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("network must be blocked"))) as client:
        with pytest.raises(sidecar.ObserverBlocked,match="STAGE4_INCOMPLETE_VERIFIER_CONFIG"):
            sidecar.stage4_signed_receipt_summary(client,{
                "SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON":'{"120":{}}',
            },120)
