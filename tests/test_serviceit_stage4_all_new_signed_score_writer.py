"""Stage 4 signed proof -> exact score-only write for every future card, never on HOLD."""
from datetime import datetime,timezone
from pathlib import Path
import json
import httpx
import pytest

from leadpilot import serviceit_stage4_all_new_signed_score_writer as w
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

NOW=datetime(2026,11,4,12,0,tzinfo=timezone.utc)
URL="https://serviseit.bitrix24.ru/rest/123/TEST_WEBHOOK"
ENV={"SERVICEIT_STAGE4_SIGNED_SCORE_WRITE_ENABLED":"YES_1062_21_SIGNED_ONLY",
     "GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
     "GITHUB_REF":"refs/heads/main",
     "SERVICEIT_BITRIX_WEBHOOK":URL,
     "SERVICEIT_STAGE4_VERIFIER_KEY":"TEST_TEST_TEST_123456789123456789",
     "SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON":'{"120":{"signed":"fixture"}}',
     "GITHUB_TOKEN":"FAKE_TOKEN","SERVICEIT_GH_LEDGER_KEY":"FAKE_KEY"}

class Fence:
    seen=set()
    calls=[]
    def __init__(self,*args):
        pass
    def reserve(self,card):
        self.calls.append("reserve")
        if card in self.seen:
            return "ALREADY_FENCED_NO_RETRY"
        self.seen.add(card)
        return "RESERVED"
    def finalize(self,card,result):
        self.calls.append(result)
        return True

def setup(monkeypatch, *, verified=True, existing=False, uncertain=False):
    Fence.seen=set()
    Fence.calls=[]
    before={"id":120,"entityTypeId":1062,"categoryId":21,
            "createdTime":NOW.isoformat(),"stageId":"DT1062_21:NEW",
            SCORE_FIELD:"" if not existing else 80,VERSION_FIELD:"",
            BADGE_FIELD:"","UF_CRM_13_SVCIT_STOP_REASON":""}
    changed={**before}
    changed[SCORE_FIELD]=85
    changed[VERSION_FIELD]="mvp-2026-10-v1"
    changed[BADGE_FIELD]=w.score_label(85)
    calls=[]
    monkeypatch.setattr(w,"card_read",lambda c,b,i,now:before.copy())
    monkeypatch.setattr(w,"ScoreFence",Fence)
    monkeypatch.setattr(w,"score_schema_ok",lambda c,b:True)
    monkeypatch.setattr(w,"review_signed_card_readonly",
        lambda c,h,i,proof,key,now:{
            "status":"MANAGER_REVIEW_REQUIRED" if verified else "HOLD",
            "formal_lead_score":85 if verified else None,
            "score_version":"mvp-2026-10-v1","crm_writes":0})
    def rest(c,b,method,args):
        calls.append((method,args))
        if method=="crm.item.update":
            assert args["entityTypeId"]==1062 and args["id"]==120
            assert set(args["fields"])==w.FIELDS
            if uncertain: raise httpx.ReadTimeout("ambiguous write")
            return {"item":{"id":120}}
        if method=="crm.item.get":
            return {"item":changed}
        pytest.fail("Unsupported REST method")
    monkeypatch.setattr(w,"score_rest",rest)
    return calls

def test_future_verified_receipt_writes_only_score_badge_version_once(monkeypatch):
    calls=setup(monkeypatch)
    with httpx.Client() as client:
        one=w.evaluate_one(client,ENV,120,now=NOW)
        two=w.evaluate_one(client,ENV,120,now=NOW)
    assert one=="FORMAL_SCORE_EXACT_READBACK_VERIFIED"
    assert two=="ALREADY_FENCED_SCORE_NO_RETRY"
    assert len([x for x in calls if x[0]=="crm.item.update"])==1
    assert Fence.calls==["reserve","VERIFIED","reserve"]
    assert len([x for x in calls if x[0]=="crm.item.get"])==1

def test_missing_receipt_can_never_write_or_use_model(monkeypatch):
    calls=setup(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("not allowed"))) as client:
        r=w.evaluate_one(client,{**ENV,
            "SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON":""},120,now=NOW)
    assert r=="HOLD_NO_SIGNED_PROOF"
    assert Fence.calls==[] and calls==[]

def test_unverified_receipt_and_existing_score_do_not_rewrite(monkeypatch):
    for params in ({"verified":False},{"existing":True}):
        calls=setup(monkeypatch,**params)
        with httpx.Client() as client:
            if params.get("existing"):
                with pytest.raises(w.ScoreBlocked,match="SCORE_ALREADY_WRITTEN_OR_UNREADABLE"):
                    w.evaluate_one(client,ENV,120,now=NOW)
            else:
                assert w.evaluate_one(client,ENV,120,now=NOW)=="HOLD_PROOF_NOT_VERIFIED"
        assert Fence.calls==[] and calls==[]

def test_ambiguous_post_no_replay_under_any_duplicate_event(monkeypatch):
    calls=setup(monkeypatch,uncertain=True)
    with httpx.Client() as client:
        assert w.evaluate_one(client,ENV,120,now=NOW)=="SCORE_WRITE_UNCERTAIN_NO_RETRY"
        assert w.evaluate_one(client,ENV,120,now=NOW)=="ALREADY_FENCED_SCORE_NO_RETRY"
    assert len([x for x in calls if x[0]=="crm.item.update"])==1
    assert Fence.calls==["reserve","UNCERTAIN","reserve"]

@pytest.mark.parametrize("bad",[3,7,0,2_000_000_001])
def test_historic_card_rejected_before_receipt_and_fence(monkeypatch,bad):
    setup(monkeypatch)
    with httpx.Client() as client:
        with pytest.raises(w.ScoreBlocked,match="SCORE_CARD_OUTSIDE_SCOPE"):
            w.evaluate_one(client,ENV,bad,now=NOW)
    assert Fence.calls==[]

def test_score_rest_3_fields_only_and_portal_scope_before_any_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("cross-CRM write attempted"))) as client:
        for method,args in [
            ("crm.item.update",{"entityTypeId":1062,"id":120,
                                "useOriginalUfNames":"Y","fields":{"stageId":"CLIENT"}}),
            ("crm.item.update",{"entityTypeId":2,"id":120,
                                "useOriginalUfNames":"Y","fields":{
                                    SCORE_FIELD:80,VERSION_FIELD:"v",BADGE_FIELD:"b"}}),
            ("crm.item.update",{"entityTypeId":1062,"id":3,
                                "useOriginalUfNames":"Y","fields":{
                                    SCORE_FIELD:80,VERSION_FIELD:"v",BADGE_FIELD:"b"}}),
        ]:
            with pytest.raises(w.ScoreBlocked):
                w.score_rest(client,URL,method,args)

def test_stage4_live_corporate_verifier_is_explicitly_required_in_actions():
    body=Path(".github/workflows/serviceit-stage4-all-new-signed-score-writer.yml").read_text("utf8")
    assert "repository_dispatch:" in body
    assert "schedule:" in body
    assert "serviceit-bitrix-new-card-free-event" in body
    assert "YES_VERIFIED_9_GATES_1062_21" in body
    assert "SERVICEIT_STAGE4_VERIFIER_KEY" in body
    assert "SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON" in body
    assert "SERVICEIT_STAGE4_SIGNED_SCORE_WRITE_ENABLED: YES_1062_21_SIGNED_ONLY" in body
