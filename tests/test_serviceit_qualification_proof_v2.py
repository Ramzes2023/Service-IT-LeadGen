"""Stage 4: signed corporate proof cannot be impersonated by signal/LLM text."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import httpx
import pytest

from leadpilot.serviceit_qualification_proof_v2 import (
    VERSION, GATE_ORIGINS, HOLD, ProofBlocked, qualify_signed,
    crm_inn_visibility_readonly, _canonical,
)
NOW = datetime(2026,11,4,12,0,tzinfo=timezone.utc)
KEY = "CORPORATE_TEST_ONLY_SECRET_0123456789abcdef"
INN = "7700000009"
BASE = "https://serviseit.bitrix24.ru/rest/123/SYNTHETIC_TOKEN"

def signed(*, kind="procurement", checked=None):
    stamp=(checked or NOW-timedelta(minutes=2)).isoformat()
    receipt = {"version":VERSION,
       "snapshot":{
           "portal":"serviseit.bitrix24.ru","entity_type_id":1062,
           "category_id":21,"item_id":52,"before_hash":"a"*64,
           "captured_at":stamp,
           "company":{"inn":INN,"name":"ООО Пример", "region":"Москва",
                      "industry":"торговля"},
           "signals":[{"signal_type":kind,"signal_date":"2026-11-03",
                       "source_url":"https://example.org/tenders/52",
                       "evidence":"Есть актуальная публикация о закупке сопровождения 1С"}]},
       "checks":{}}
    for gate,origin in GATE_ORIGINS.items():
        receipt["checks"][gate]={
            "state":"VERIFIED","finding":"CLEAR","origin":origin,
            "subject_inn":INN,"checked_at":stamp,"evidence_digest":"b"*64}
    return resign(receipt)

def resign(receipt):
    source={k:receipt[k] for k in ("version","snapshot","checks")}
    receipt["signature"]="sha256="+hmac.new(KEY.encode(),_canonical(source),sha256).hexdigest()
    return receipt

def test_all_nine_corporate_proofs_unlock_formal_score_not_contact_or_task():
    result=qualify_signed(signed(),KEY,now=NOW,current_crm_before_hash='a'*64)
    assert result["status"]=="MANAGER_REVIEW_REQUIRED"
    assert isinstance(result["formal_lead_score"],int)
    assert 0 <= result["formal_lead_score"] <= 100
    assert result["verified_evidence_gates"]==9
    assert result["task_creation_authorized"] is False
    assert result["outreach_authorized"] is False
    assert result["crm_writes"]==0

def test_missing_or_forged_fns_evidence_never_yields_score():
    x=signed()
    del x["checks"]["private_small_moscow"]
    assert qualify_signed(x,KEY,now=NOW)==HOLD
    x=signed()
    x["checks"]["stop_list"]["finding"]="CLEAR_UNCHECKED"
    assert qualify_signed(x,KEY,now=NOW)==HOLD
    x=resign(x)
    assert qualify_signed(x,KEY,now=NOW)==HOLD

def test_stale_attestation_blocks_even_with_good_signature():
    x=signed(checked=NOW-timedelta(hours=2))
    assert qualify_signed(x,KEY,now=NOW)==HOLD

def test_wrong_card_or_bad_inn_blocks_even_with_valid_signature():
    x=signed()
    x["snapshot"]["item_id"]=3
    assert qualify_signed(resign(x),KEY,now=NOW)==HOLD
    x=signed()
    x["snapshot"]["company"]["inn"]="7700000000"
    assert qualify_signed(resign(x),KEY,now=NOW)==HOLD

def test_vacancy_never_becomes_direct_buying_lead_even_with_all_proofs():
    x=qualify_signed(signed(kind="1c_vacancy"),KEY,now=NOW,current_crm_before_hash='a'*64)
    assert x["status"]=="VACANCY_ONLY_MANAGER_REVIEW"
    assert x["crm_handoff_eligible"] is False
    assert x["outreach_authorized"] is False

def fake_requisites(req, rows, *, bad=False):
    assert req.method == "POST"
    assert req.url.path.endswith("/crm.requisite.list.json")
    assert json.loads(req.content)=={
       "filter":{"RQ_INN":INN},
       "select":["ID","ENTITY_TYPE_ID","ENTITY_ID","RQ_INN"],"start":0}
    if bad:
        return httpx.Response(403,json={"error":"ACCESS_DENIED"})
    return httpx.Response(200,json={"result":rows,"total":len(rows)})

def test_crm_exact_inn_positive_blocks_potential_existing_customer():
    rows=[{"ID":"11","ENTITY_TYPE_ID":"4","ENTITY_ID":"501","RQ_INN":INN}]
    with httpx.Client(transport=httpx.MockTransport(
            lambda r:fake_requisites(r,rows))) as c:
        out=crm_inn_visibility_readonly(c,BASE,INN)
    assert out["status"]=="POTENTIAL_EXISTING_RECORD_BLOCK"
    assert out["visible_matches"]==1
    assert out["global_crm_customer_clear"] is False
    assert out["stop_list_verified"] is False

def test_crm_no_visible_record_is_not_global_dedup_clear():
    with httpx.Client(transport=httpx.MockTransport(
            lambda r:fake_requisites(r,[]))) as c:
        out=crm_inn_visibility_readonly(c,BASE,INN)
    assert out["status"]=="NO_MATCH_IN_VISIBLE_REQUISITES_ONLY"
    assert out["global_dedup_verified"] is False
    assert out["crm_writes"]==0

def test_crm_unauthorized_or_mismatched_inn_fails_closed():
    with httpx.Client(transport=httpx.MockTransport(
            lambda r:fake_requisites(r,[],bad=True))) as c:
        with pytest.raises(ProofBlocked,match="CRM_REQUISITE_SCOPE_OR_HTTP"):
            crm_inn_visibility_readonly(c,BASE,INN)
    rows=[{"ID":"11","ENTITY_TYPE_ID":"4","ENTITY_ID":"501","RQ_INN":"0000000000"}]
    with httpx.Client(transport=httpx.MockTransport(
            lambda r:fake_requisites(r,rows))) as c:
        with pytest.raises(ProofBlocked,match="CRM_REQUISITE_IDENTITY_MISMATCH"):
            crm_inn_visibility_readonly(c,BASE,INN)


def test_valid_signed_receipt_without_fresh_independent_crm_readback_is_hold():
    report=signed()
    assert qualify_signed(report,KEY,now=NOW)==HOLD
    assert qualify_signed(report,KEY,now=NOW,
                          current_crm_before_hash="c"*64)==HOLD
    assert qualify_signed(report,KEY,now=NOW,
                          current_crm_before_hash="a"*64)["formal_lead_score"] is not None
