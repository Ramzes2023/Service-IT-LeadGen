"""Stage4: live read-only exact card binding, no claim of external verifier."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import FIELD_MAP
from leadpilot.serviceit_kanban_presentation import BADGE_FIELD
from leadpilot.serviceit_preliminary_score_markers import provisional_payload
from leadpilot.serviceit_enrichment_worker import fingerprint
from leadpilot.serviceit_qualification_proof_v2 import GATE_ORIGINS, HOLD, VERSION, _canonical
from leadpilot.serviceit_stage4_live_proof_readonly import review_signed_card_readonly

NOW=datetime(2026,11,4,12,0,tzinfo=timezone.utc)
KEY="CORPORATE_TEST_SECRET_123456789_abcdef"
WEBHOOK="https://serviseit.bitrix24.ru/rest/123/SYNTHETIC_TEST"

def card(*, different_inn=False, stopped=False, existing_score=False, wrong_category=False):
    item={"id":120,"entityTypeId":1062,"categoryId":22 if wrong_category else 21,
          "title":"Проверка коммерческой закупки",
          FIELD_MAP["company_inn"]:"7700000000" if different_inn else "7700000009",
          FIELD_MAP["company_name"]:"ООО Пример",
          FIELD_MAP["region"]:"Москва",
          FIELD_MAP["signal_url"]:"https://example.org/tender/120",
          FIELD_MAP["signal_date"]:"2026-11-03",
          FIELD_MAP["lead_score"]:55 if existing_score else "",
          FIELD_MAP["score_version"]:"",
          BADGE_FIELD:"",
          "UF_CRM_13_SVCIT_STOP_REASON":"Не обращаться" if stopped else ""}
    return item

def proof(item, *, forged=False):
    stamp=(NOW-timedelta(minutes=1)).isoformat()
    receipt={
        "version":VERSION,
        "snapshot":{
            "portal":"serviseit.bitrix24.ru",
            "entity_type_id":1062, "category_id":21,
            "item_id":120,"before_hash":fingerprint(item),
            "captured_at":stamp,
            "company":{"inn":"7700000009","name":"ООО Пример",
                       "region":"Москва","industry":"информационные услуги"},
            "signals":[{"source_url":"https://example.org/tender/120",
                        "signal_date":"2026-11-03",
                        "signal_type":"procurement",
                        "evidence":"Требуются услуги сопровождения 1С"}]},
        "checks":{
            key:{"origin":origin,"state":"VERIFIED","finding":"CLEAR",
                 "subject_inn":"7700000009","evidence_digest":"a"*64,
                 "checked_at":stamp}
            for key,origin in GATE_ORIGINS.items()},
    }
    receipt["signature"]="sha256="+hmac.new(KEY.encode(),_canonical(receipt),sha256).hexdigest()
    if forged:
        receipt["checks"]["stop_list"]["finding"]="CLEAR_BUT_NO_SOURCE"
    return receipt

def response(req,item,*,duplicate=False,denied=False):
    assert req.method=="POST"
    if req.url.path.endswith("/crm.item.get.json"):
        assert json.loads(req.content)=={
            "entityTypeId":1062,"id":120,"useOriginalUfNames":"Y"}
        return httpx.Response(200,json={"result":{"item":item}})
    if req.url.path.endswith("/crm.requisite.list.json"):
        assert json.loads(req.content)=={
            "filter":{"RQ_INN":"7700000009"},
            "select":["ID","ENTITY_TYPE_ID","ENTITY_ID","RQ_INN"],
            "start":0}
        if denied:
            return httpx.Response(403,json={"error":"ACCESS_DENIED"})
        rows=[{"ID":"111","ENTITY_TYPE_ID":"4",
               "ENTITY_ID":"555","RQ_INN":"7700000009"}] if duplicate else []
        return httpx.Response(200,json={"result":rows,"total":len(rows)})
    pytest.fail("Unexpected read-only Bitrix REST method")

def test_all_nine_signed_with_matching_live_readback_has_in_memory_score_only():
    item=card()
    receipt=proof(item)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:response(req,item))) as client:
        result=review_signed_card_readonly(client,WEBHOOK,120,receipt,KEY,now=NOW)
    assert 0<=result["formal_lead_score"]<=100
    assert result["verified_evidence_gates"]==9
    assert result["score_version"]
    assert result["crm_writes"]==0
    assert result["task_creation_authorized"] is False
    assert result["outreach_authorized"] is False

def test_forged_evidence_blocks_before_any_crm_rest_call():
    item=card()
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("bad receipt must not read Bitrix"))) as client:
        assert review_signed_card_readonly(
            client,WEBHOOK,120,proof(item,forged=True),KEY,now=NOW)==HOLD

@pytest.mark.parametrize("changed",[
    {"different_inn":True}, {"stopped":True},
    {"existing_score":True},{"wrong_category":True},
])
def test_changed_or_wrong_live_card_never_qualifies(changed):
    original=card()
    changed_card=card(**changed)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:response(req,changed_card))) as client:
        assert review_signed_card_readonly(
            client,WEBHOOK,120,proof(original),KEY,now=NOW)==HOLD

def test_wrong_requested_card_id_cannot_read_other_customers():
    item=card()
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("out of scope"))) as client:
        assert review_signed_card_readonly(
            client,WEBHOOK,3,proof(item),KEY,now=NOW)==HOLD
        assert review_signed_card_readonly(
            client,WEBHOOK,121,proof(item),KEY,now=NOW)==HOLD

def test_missing_receipt_never_returns_score_or_calls_rest():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("missing independent verified proof"))) as client:
        result=review_signed_card_readonly(client,WEBHOOK,120,None,KEY,now=NOW)
    assert result==HOLD
    assert result["formal_lead_score"] is None


def test_even_signed_nine_gates_cannot_override_visible_existing_inn_duplicate():
    item=card()
    receipt=proof(item)
    methods=[]
    def mock(req):
        methods.append(req.url.path.rsplit("/",1)[-1])
        return response(req,item,duplicate=True)
    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        out=review_signed_card_readonly(client,WEBHOOK,120,receipt,KEY,now=NOW)
    assert out==HOLD
    assert methods==["crm.item.get.json","crm.requisite.list.json"]
    assert out["formal_lead_score"] is None


def test_signed_proof_with_crm_requisite_permission_denied_is_hold():
    item=card()
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:response(req,item,denied=True))) as client:
        assert review_signed_card_readonly(
            client,WEBHOOK,120,proof(item),KEY,now=NOW)==HOLD


def test_exact_provisional_can_be_verified_only_with_nine_signed_gates():
    item = card()
    item.update(provisional_payload(71, "ТОЛЬКО ВИДИМЫЕ"))
    receipt = proof(item)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:response(req,item))) as client:
        decision = review_signed_card_readonly(client,WEBHOOK,120,receipt,KEY,now=NOW)
    assert decision["verified_evidence_gates"]==9
    assert type(decision["formal_lead_score"]) is int
    assert decision["outreach_authorized"] is False


def test_manual_or_partial_fake_preliminary_score_is_not_promotable():
    item = card()
    item.update(provisional_payload(71, "НЕ ПРОВЕРЕНЫ"))
    item[BADGE_FIELD] += " подделка"
    receipt = proof(item)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:response(req,item))) as client:
        assert review_signed_card_readonly(client,WEBHOOK,120,receipt,KEY,now=NOW)==HOLD
