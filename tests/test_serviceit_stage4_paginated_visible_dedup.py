"""Actual Bitrix paginated exact-INN contract: no false negative after 50.

All fixtures synthetic and no REST writes. Legacy/history may be READ for
dedup, but no historical write is authorized.
"""
import json
import httpx
import pytest

from leadpilot.serviceit_qualification_proof_v2 import (
    crm_inn_visibility_readonly, ProofBlocked,
)
from leadpilot.serviceit_stage4_fns_dedupe_watch import (
    same_process_visible_duplicates, WatchBlocked,
)
from leadpilot.serviceit_enrichment_worker import PREFIX

INN = "7700000009"
BASE = "https://serviseit.bitrix24.ru/rest/1615/FAKE-TEST-TOKEN"
FIELD = PREFIX + "COMPANY_INN"


def req_transport(*, change_inn=False, missing_last=False, repeated=False, wrong_total=False):
    calls = []
    def handler(req):
        assert req.url.path.endswith("/crm.requisite.list.json")
        args = json.loads(req.content)
        assert args["filter"] == {"RQ_INN": INN}
        assert args["select"] == ["ID", "ENTITY_TYPE_ID", "ENTITY_ID", "RQ_INN"]
        start = args["start"]
        calls.append(start)
        assert start in (0, 50)
        rows = ([{"ID": str(i+1), "ENTITY_TYPE_ID":"4",
                  "ENTITY_ID":str(i+10), "RQ_INN":INN}
                 for i in range(50)] if start==0 else
                [{"ID":"2" if repeated else "51", "ENTITY_TYPE_ID":"3",
                  "ENTITY_ID":"99", "RQ_INN":"7700000010" if change_inn else INN}])
        d = {"result": [] if missing_last and start==50 else rows,
             "total": 52 if wrong_total else 51}
        if start==0:
            d["next"] = 50
        return httpx.Response(200, json=d)
    return httpx.MockTransport(handler), calls


def proc_transport(*, wrong_inn=False, repeated=False, total_mismatch=False):
    calls = []
    def handler(req):
        assert req.url.path.endswith("/crm.item.list.json")
        params = json.loads(req.content)
        assert params["entityTypeId"] == 1062
        assert params["filter"] == {"categoryId":21,FIELD:INN}
        assert params["select"] == ["id","categoryId",FIELD]
        start = params["start"]
        calls.append(start)
        assert start in (0,50)
        rows = ([{"id":i+1,"categoryId":21,FIELD:INN} for i in range(50)]
                if start==0 else
                [{"id":2 if repeated else 123,"categoryId":21,
                  FIELD:"7700000010" if wrong_inn else INN}])
        d = {"result": {"items":rows}, "total":52 if total_mismatch else 51}
        if start==0:
            d["next"]=50
        return httpx.Response(200,json=d)
    return httpx.MockTransport(handler), calls


def test_requisites_scan_follows_next_into_second_page():
    transport, calls = req_transport()
    with httpx.Client(transport=transport) as client:
        out = crm_inn_visibility_readonly(client,BASE,INN)
    assert calls == [0,50]
    assert out["visible_matches"]==51
    assert out["status"]=="POTENTIAL_EXISTING_RECORD_BLOCK"
    assert out["visible_pages_complete"] is True
    assert out["global_dedup_verified"] is False


@pytest.mark.parametrize("opts",[
    {"change_inn":True}, {"missing_last":True},
    {"repeated":True}, {"wrong_total":True},
])
def test_requisites_inconsistent_page_is_blocked(opts):
    transport,_=req_transport(**opts)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ProofBlocked):
            crm_inn_visibility_readonly(client,BASE,INN)


def test_process_dedup_finds_other_card_on_second_page():
    transport,calls=proc_transport()
    with httpx.Client(transport=transport) as client:
        out=same_process_visible_duplicates(client,BASE,INN,33)
    assert calls==[0,50]
    assert out["process_visible_duplicate"] is True
    assert out["process_visible_matches"]==51
    assert out["visible_pages_complete"] is True
    assert out["global_crm_clear"] is False


@pytest.mark.parametrize("opts",[
    {"wrong_inn":True}, {"repeated":True}, {"total_mismatch":True},
])
def test_process_dedup_inconsistent_second_page_is_blocked(opts):
    transport,_=proc_transport(**opts)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(WatchBlocked):
            same_process_visible_duplicates(client,BASE,INN,33)


def test_no_empty_first_page_fakes_clear_when_total_nonzero():
    def broken(req):
        return httpx.Response(200,json={"result":[],"total":100})
    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(ProofBlocked):
            crm_inn_visibility_readonly(client,BASE,INN)
