"""Synthetic-only CRM qualification bridge: no customer data, contacts or source activation."""
from datetime import date
from hashlib import sha256
import json
import sqlite3
import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION, EXTRA_FIELDS, FIELD_MAP
from leadpilot.bitrix_1062_demo_qualification import (
    sync_one_demo, build_analysis, DemoBridgeBlocked, SCORE_FIELD, ACTION_FIELD,
    VERSION_FIELD, STOP_FIELD, AI_TEXT,
)

HOOK = "https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456"
TODAY = date(2026, 11, 1)


def card():
    d = {k: "DEMO" for k in ALL_FIELD_CODES}
    d.update({
        "id": 3, "entityTypeId": 1062, "categoryId": 21,
        "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
        "stageId": "DT1062_21:CLIENT", "contactIds": [], "companyId": 0,
        "UF_CRM_13_SVCIT_COMPANY_INN": "7700000009",
        "UF_CRM_13_SVCIT_COMPANY_NAME": "DEMO COMPANY-001",
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "synthetic-fixture",
        "UF_CRM_13_SVCIT_SIGNAL_URL": "https://example.invalid/serviceit-demo/request/001",
        "UF_CRM_13_SVCIT_SIGNAL_ID": "DEMO-ITREQ-001",
        "UF_CRM_13_SVCIT_SIGNAL_DATE": "2026-10-31",
        "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "DEMO-IDEMP-" + sha256(
            f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()
        ).hexdigest()[:32],
        **EXTRA_FIELDS,
    })
    d[SCORE_FIELD] = 86
    d[VERSION_FIELD] = "legacy-demo"
    d[ACTION_FIELD] = "DEMO: устаревшая тестовая рекомендация"
    d[STOP_FIELD] = ""
    return d


def portal(tmp_path, *, item=None, apply=False, ai=False, ai_result=None, deny=None):
    c = dict(card() if item is None else item)
    calls = []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        body = json.loads(request.content)
        calls.append((method, body))
        if method == "crm.item.update":
            assert body["entityTypeId"] == 1062 and body["id"] == 3
            assert body["useOriginalUfNames"] == "Y"
            fields = body["fields"]
            assert set(fields) == {SCORE_FIELD, VERSION_FIELD, ACTION_FIELD, STOP_FIELD}
            assert not any(x in fields for x in ("stageId", "contactIds", "companyId", "title"))
            c.update(fields)
            return httpx.Response(200, json={"result": {"item": c}})
        if method == "crm.type.getByEntityTypeId":
            result = {"type": {
                "entityTypeId": 1062, "isUseInUserfieldEnabled": "Y",
                "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "true"},
                "isAutomationEnabled": "N", "isBizProcEnabled": "N"}}
        elif method == "crm.enum.ownertype":
            result = [{"ID": 1062, "SYMBOL_CODE_SHORT": "T426"}]
        elif method == "tasks.task.getFields":
            result = {"fields": {"TITLE": {"type": "string"},
                      "RESPONSIBLE_ID": {"type": "integer"},
                      "UF_CRM_TASK": {"type": "crm"}}}
        elif method == "crm.item.get":
            assert body == {"entityTypeId":1062,"id":3,"useOriginalUfNames":"Y"}
            result = {"item": c}
        elif method == "crm.status.list":
            result = [{"ENTITY_ID":"DYNAMIC_1062_STAGE_21","CATEGORY_ID":21,
                       "STATUS_ID":"DT1062_21:NEW","SEMANTICS":None}]
        elif method == "crm.item.fields":
            assert body == {"entityTypeId":1062,"useOriginalUfNames":"Y"}
            result = {"fields":{
                SCORE_FIELD: {"type":"integer","isReadOnly":False,"isMultiple":False},
                VERSION_FIELD: {"type":"string","isReadOnly":False,"isMultiple":False},
                ACTION_FIELD: {"type":"text","isReadOnly":False,"isMultiple":False},
                STOP_FIELD: {"type":"string","isReadOnly":False,"isMultiple":False},
            }}
        else:
            raise AssertionError(method)
        if deny == method:
            return httpx.Response(403,json={"error":"ACCESS_DENIED","private":"NOT_FOR_LOG"})
        return httpx.Response(200, json={"result":result})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return sync_one_demo(HOOK, tmp_path/"ledger.sqlite",
                             apply=apply, use_ai=ai, client=client,
                             ai_classifier=ai_result, today=TODAY), calls, c


def good_ai(text):
    assert text == AI_TEXT
    return {"origin":"AI_hypothesis","service":"1С",
            "quality_status":"complete","evidence_quote":"три торговые точки",
            "confidence":0.9,"possible_need":"Возможное сопровождение 1С"}


def test_rules_are_deterministic_and_ai_cannot_change_score():
    item=card()
    base=build_analysis(item,today=TODAY)
    with_ai=build_analysis(item,today=TODAY,hypothesis=good_ai(AI_TEXT))
    assert base["score"]==90
    assert with_ai["score"]==base["score"]
    assert base["factors"]["contact"]==0
    assert base["ai_status"]=="NOT_RUN"
    assert with_ai["ai_status"]=="HYPOTHESIS_UNVERIFIED"
    assert base["real_lead_ready"] is False
    assert base["global_crm_duplicate_check"]=="NOT_PERFORMED"
    assert "не звонить" in base["fields"][ACTION_FIELD].lower()


def test_demo_crm_dry_run_never_writes(tmp_path):
    result,calls,_=portal(tmp_path)
    assert result["status"]=="DEMO_PREVIEW_READY"
    assert result["deterministic_score"]==90
    assert result["ai_status"]=="NOT_RUN"
    assert result["global_customer_dedup"]=="NOT_VERIFIED"
    assert result["crm_writes"]=="NO"
    assert not any(m=="crm.item.update" for m,_ in calls)
    assert not (tmp_path/"ledger.sqlite").exists()


def test_one_crm_item_update_and_readback_verified(tmp_path):
    result,calls,current=portal(tmp_path,apply=True)
    assert result["status"]=="DEMO_CRM_QUALIFICATION_VERIFIED"
    assert result["crm_writes"]=="ONE_DEMO_ITEM_UPDATE"
    assert sum(m=="crm.item.update" for m,_ in calls)==1
    assert current[SCORE_FIELD]==90
    assert current[VERSION_FIELD]=="mvp-2026-10-v1"
    assert current[STOP_FIELD].startswith("DEMO:")
    assert current["stageId"]=="DT1062_21:CLIENT"
    assert not any(m.startswith("tasks.task.add") for m,_ in calls)
    with sqlite3.connect(tmp_path/"ledger.sqlite") as con:
        assert con.execute("SELECT state FROM demo_score_sync").fetchone()[0]=="VERIFIED"


def test_same_item_is_idempotently_read_without_post(tmp_path):
    result,calls,current=portal(tmp_path,apply=True)
    again,more,_=portal(tmp_path,item=current,apply=True)
    assert again["status"]=="ALREADY_MATCHES"
    assert not any(m=="crm.item.update" for m,_ in more)


def test_stage_change_kept_when_registered_in_same_demo_funnel(tmp_path):
    item=card()
    item["stageId"]="DT1062_21:NEW"
    result,calls,current=portal(tmp_path,item=item,apply=True)
    assert result["status"]=="DEMO_CRM_QUALIFICATION_VERIFIED"
    assert result["stage_policy"]=="REGISTERED_NON_FAILURE_STAGE"
    assert current["stageId"]=="DT1062_21:NEW"
    assert any(m=="crm.status.list" for m,_ in calls)


@pytest.mark.parametrize("change,code", [
    ({"contactIds":[44]}, "DEMO_METADATA_NOT_READY"),
    ({"companyId":55}, "DEMO_METADATA_NOT_READY"),
    ({"UF_CRM_13_SVCIT_SIGNAL_SOURCE":"real-company"}, "DEMO_METADATA_NOT_READY"),
    ({"UF_CRM_13_SVCIT_COMPANY_INN":"7700000010"}, "DEMO_METADATA_NOT_READY"),
    ({STOP_FIELD:"PRODUCTION_STOP_LIST: DO NOT OVERWRITE"}, "EXISTING_STOP_REASON_MUST_NOT_BE_OVERWRITTEN"),
    ({"UF_CRM_13_SVCIT_EVIDENCE_STATUS":"validated real"}, "DEMO_EVIDENCE_STATUS_MISMATCH"),
])
def test_protected_card_or_real_client_never_updated(tmp_path,change,code):
    item=card()
    item.update(change)
    with pytest.raises(DemoBridgeBlocked, match=code):
        portal(tmp_path,item=item,apply=True)
    assert not (tmp_path/"ledger.sqlite").exists()


def test_ai_quality_gate_blocks_without_crm_write(tmp_path):
    def bad_ai(text):
        return {"origin":"AI_hypothesis","service":"1С",
                "quality_status":"needs_review","evidence_quote":"invented"}
    with pytest.raises(DemoBridgeBlocked, match="AI_HYPOTHESIS_QUALITY_GATE_BLOCKED"):
        portal(tmp_path,apply=True,ai=True,ai_result=bad_ai)
    assert not (tmp_path/"ledger.sqlite").exists()


def test_model_ai_synthetic_only_and_not_real_contact(tmp_path):
    result,calls,item=portal(tmp_path,apply=True,ai=True,ai_result=good_ai)
    assert result["ai_status"]=="HYPOTHESIS_UNVERIFIED"
    assert item[SCORE_FIELD]==90
    assert "гипотеза" in item[ACTION_FIELD].lower()
    assert result["real_leads_approved"] is False
    assert not any("contacts" in method.lower() or "company.list" in method
                   for method,_ in calls)


def test_api_access_denied_is_safe_block(tmp_path):
    with pytest.raises(DemoBridgeBlocked,match="DEMO_METADATA_NOT_READY"):
        portal(tmp_path,apply=True,deny="crm.item.get")
    assert not (tmp_path/"ledger.sqlite").exists()

def test_schema_denied_blocks_before_write_or_ledger(tmp_path):
    with pytest.raises(DemoBridgeBlocked, match="DEMO_FIELD_SCHEMA_READ_FAILED"):
        portal(tmp_path, apply=True, deny="crm.item.fields")
    assert not (tmp_path/"ledger.sqlite").exists()
