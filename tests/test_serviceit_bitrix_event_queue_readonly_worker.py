"""End-to-end synthetic queue -> real-shape mock crm.item.get -> safe classification.

All tests offline. No updates, no model, no clients, no webhook stored.
"""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_bitrix_new_card_events import enqueue_new_item, queue_status
from leadpilot.serviceit_bitrix_event_queue_readonly_worker import (
    ReadOnlyWorkerBlocked,classify_item,examine_queue,
    examine_ids,_rest_reader,pending_ids,main,
)

KEYS={
    "id":10,"entityTypeId":1062,"categoryId":21,
    "UF_CRM_13_SVCIT_SIGNAL_ID":"B2B-CENTER-4661617",
    "UF_CRM_13_SVCIT_SIGNAL_KIND":"tender-opportunity",
    "UF_CRM_13_SVCIT_SIGNAL_SOURCE":"B2B-Center email subscription",
    "UF_CRM_13_SVCIT_STOP_REASON":"Не квалифицированный B2B-лид",
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS":"HOLD: ещё не проверена закупка",
    "UF_CRM_13_SVCIT_AI_STATUS":None,
    "UF_CRM_13_SVCIT_AI_FULL_REPORT":None,
    "UF_CRM_13_SVCIT_LEAD_SCORE":None,
    "UF_CRM_13_SVCIT_AI_SCORE_BADGE":None,
}


def test_new_b2b_center_card_without_ai_gets_hold_no_invented_score():
    result=classify_item(KEYS,requested_id=10)
    assert result["source_kind"]=="b2b_center_hold"
    assert result["decision"]=="HOLD_MISSING_PRELIMINARY_AI"
    assert result["analysis_allowed"] is False
    assert result["gigachat_requested"] is False
    assert result["crm_writes"]==0
    assert result["manual_contact_authorized"] is False
    assert "tender-" not in str(result)
    assert "4661617" not in str(result)


def test_card_prepopulated_with_valid_hold_ai_does_not_repeat():
    item={**KEYS,
          "UF_CRM_13_SVCIT_AI_STATUS":"NOT_RUN_SOURCE_UNVERIFIED",
          "UF_CRM_13_SVCIT_AI_FULL_REPORT":"AI модель не запускалась"}
    out=classify_item(item,requested_id=10)
    assert out["decision"]=="HOLD_PRELIMINARY_AI_ALREADY_PRESENT"
    item["UF_CRM_13_SVCIT_AI_STATUS"]="HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert classify_item(item,requested_id=10)["decision"]=="HOLD_PRELIMINARY_AI_ALREADY_PRESENT"


def test_b2b_center_existing_score_is_quarantined_not_promoted():
    item={**KEYS,"UF_CRM_13_SVCIT_LEAD_SCORE":90,
          "UF_CRM_13_SVCIT_AI_SCORE_BADGE":"🟢 90/100"}
    assert classify_item(item,requested_id=10)["decision"]=="HOLD_WITH_UNEXPECTED_SCORE_REVIEW"


def test_synthetic_and_other_crm_categories_are_excluded():
    demo={**KEYS,"id":3,"UF_CRM_13_SVCIT_SIGNAL_ID":"DEMO-ITREQ-001",
          "UF_CRM_13_SVCIT_SIGNAL_KIND":"demo-synthetic"}
    assert classify_item(demo,requested_id=3)["decision"]=="EXCLUDED_SYNTHETIC_DEMO"
    not_target={**KEYS,"categoryId":22}
    assert classify_item(not_target,requested_id=10)["decision"]=="OUTSIDE_SERVICEIT_CATEGORY"


def test_other_tender_does_not_assume_valid_provenance():
    item={**KEYS,"UF_CRM_13_SVCIT_SIGNAL_KIND":"tender-opportunity",
          "UF_CRM_13_SVCIT_SIGNAL_SOURCE":"Bidzaar"}
    assert classify_item(item,requested_id=10)["decision"]=="TENDER_SOURCE_CONTRACT_UNVERIFIED"


@pytest.mark.parametrize("field,val,expected",[
    ("id",11,"CRM_ITEM_IDENTITY_CONFLICT"),
    ("entityTypeId",100,"CRM_ITEM_IDENTITY_CONFLICT"),
    ("UF_CRM_13_SVCIT_SIGNAL_ID","invalid","B2B_SOURCE_ID_CONFLICT"),
    ("UF_CRM_13_SVCIT_SIGNAL_ID",["array"],"CRM_SOURCE_FIELD_TYPES_INVALID"),
    ("UF_CRM_13_SVCIT_AI_FULL_REPORT",{}, "CRM_SOURCE_FIELD_TYPES_INVALID"),
    ("UF_CRM_13_SVCIT_LEAD_SCORE","90","CRM_EXISTING_SCORE_TYPE_INVALID"),
    ("UF_CRM_13_SVCIT_STOP_REASON","","B2B_HOLD_GUARD_MISSING"),
])
def test_bad_or_inconsistent_crm_item_fails_closed(field,val,expected):
    item={**KEYS,field:val}
    with pytest.raises(ReadOnlyWorkerBlocked,match=expected):
        classify_item(item,requested_id=10)


def test_read_pending_and_classify_multiple_real_shapes(tmp_path):
    queue=tmp_path/"queue.sqlite3"
    for card in (10,3,25):
        enqueue_new_item(queue,{
            "status":"ACCEPTED_NEW_ITEM","entity_type_id":1062,"item_id":card})
    snapshot=queue.read_bytes()
    items={
        10:KEYS,
        3:{**KEYS,"id":3,"UF_CRM_13_SVCIT_SIGNAL_ID":"DEMO-ITREQ-001",
            "UF_CRM_13_SVCIT_SIGNAL_KIND":"demo-synthetic"},
        25:{**KEYS,"id":25,"UF_CRM_13_SVCIT_SIGNAL_ID":"OTHER-X",
            "UF_CRM_13_SVCIT_SIGNAL_SOURCE":"Bidzaar"}
    }
    calls=[]
    def mock_get(method,params):
        calls.append((method,params))
        assert method=="crm.item.get" and params["entityTypeId"]==1062
        assert params["useOriginalUfNames"]=="Y"
        return {"item":items[params["id"]]}
    result=examine_queue(queue,mock_get)
    assert result["status"]=="SERVICEIT_EVENT_QUEUE_CRM_READONLY"
    assert result["inspected"]==3
    assert result["crm_reads"]==3 and result["crm_writes"]==0
    assert result["queue_claims"]==0 and result["gigachat_calls"]==0
    assert len(calls)==3
    assert {x["decision"] for x in result["items"]}=={
        "HOLD_MISSING_PRELIMINARY_AI","EXCLUDED_SYNTHETIC_DEMO",
        "TENDER_SOURCE_CONTRACT_UNVERIFIED"}
    assert pending_ids(queue)==[3,10,25]
    assert queue_status(queue)["pending"]==3
    assert queue.read_bytes()==snapshot


def test_reader_rejects_unauthorized_methods_and_raw_errors():
    seen=[]
    def handler(request):
        seen.append((request.url.path,request.content))
        return httpx.Response(200,json={"result":{"item":KEYS}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch=_rest_reader(
            "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY123456",
            client)
        got=fetch("crm.item.get",{"entityTypeId":1062,"id":10,
                                 "useOriginalUfNames":"Y"})
        assert got["item"]["id"]==10
        with pytest.raises(ReadOnlyWorkerBlocked,match="ONLY_EXACT_ITEM_READ_ALLOWED"):
            fetch("crm.item.update",{"entityTypeId":1062,"id":10,
                    "fields":{"title":"WRONG"}})
    assert len(seen)==1 and seen[0][0].endswith("/crm.item.get.json")
    assert b"crm.item.update" not in seen[0][1]


def test_empty_or_bad_queue_does_not_trigger_webhook(tmp_path):
    with pytest.raises(ReadOnlyWorkerBlocked,match="QUEUE_NOT_AVAILABLE"):
        pending_ids(tmp_path/"missing.sqlite3")
    assert examine_ids([],lambda *a:pytest.fail("unwanted"))["inspected"]==0
    assert main([])==0


def test_source_contains_no_updates_or_gigachat_calls():
    p=Path(__file__).resolve().parents[1]/"leadpilot"/"serviceit_bitrix_event_queue_readonly_worker.py"
    text=p.read_text(encoding="utf-8")
    for token in ("crm.item.update","crm.item.add","event.bind",
                  "classify_cockpit_signal","GIGACHAT_CREDENTIALS",
                  "tasks.task.add","DELETE FROM new_cards","UPDATE new_cards"):
        assert token not in text
