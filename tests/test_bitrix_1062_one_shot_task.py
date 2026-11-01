"""Mock-only acceptance of one-shot task creation and no-blind-retry fence."""
from hashlib import sha256
import json
import sqlite3

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from leadpilot.bitrix_1062_one_shot_task import (
    execute_one_demo_task, OneShotTaskBlocked, CONFIRM, TASK_TITLE,
)
from leadpilot.bitrix_1062_task_dedupe_readonly import CRM_BINDING

HOOK = "https://serviseit.bitrix24.ru/rest/15/SAFEMOCKSECRET1234567"


def metadata():
    item = dict.fromkeys(ALL_FIELD_CODES, "DEMO")
    item.update({
        "id": 3, "entityTypeId": 1062, "categoryId": 21,
        "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
        "stageId": "DT1062_21:CLIENT",
        "contactIds": [], "companyId": 0,
        "UF_CRM_13_SVCIT_COMPANY_INN": "7700000009",
        "UF_CRM_13_SVCIT_COMPANY_NAME": "DEMO COMPANY-001",
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "synthetic-fixture",
        "UF_CRM_13_SVCIT_SIGNAL_ID": "DEMO-ITREQ-001",
        "UF_CRM_13_SVCIT_SIGNAL_URL": "https://example.invalid/serviceit-demo/request/001",
        "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "DEMO-IDEMP-" + sha256(
            f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()
        ).hexdigest()[:32],
    })
    return {
        "crm.type.getByEntityTypeId": {"type": {
            "entityTypeId": 1062, "isUseInUserfieldEnabled": "Y",
            "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "true"},
            "isAutomationEnabled": "N", "isBizProcEnabled": "N",
        }},
        "crm.enum.ownertype": [{"ID": 1062, "SYMBOL_CODE_SHORT": "T426"}],
        "tasks.task.getFields": {"fields": {
            "TITLE": {}, "RESPONSIBLE_ID": {}, "UF_CRM_TASK": {"type": "crm"},
        }},
        "crm.item.get": {"item": item},
    }


def invoke(tmp_path, *, mode="ok", ledger=None, confirm=CONFIRM, responsible=22):
    calls = []
    meta = metadata()
    def handler(req):
        meth = req.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        body = json.loads(req.content)
        calls.append((meth, body))
        if meth in meta:
            return httpx.Response(200, json={"result": meta[meth]})
        if meth == "tasks.task.list":
            if mode == "list_http400":
                return httpx.Response(400, json={
                    "error": "CUSTOM_PRIVATE_REQUEST_DATA",
                    "error_description": "Do not show task names or secrets",
                })
            if mode == "existing":
                return httpx.Response(200, json={"result": {"tasks": [{
                    "id": "100", "title": TASK_TITLE, "ufCrmTask": [CRM_BINDING],
                }]}})
            return httpx.Response(200, json={"result": {"tasks": []}})
        if meth == "tasks.task.add":
            assert body["fields"]["UF_CRM_TASK"] == [CRM_BINDING]
            assert body["fields"]["TITLE"] == TASK_TITLE
            assert body["fields"]["RESPONSIBLE_ID"] == responsible
            assert "НЕ ЗВОНИТЬ" in body["fields"]["DESCRIPTION"]
            if mode == "timeout":
                raise httpx.ConnectError("private URL simulated timeout")
            return httpx.Response(200, json={"result": {"task": {"id": "111"}}})
        if meth == "tasks.task.get":
            assert body["taskId"] == 111
            assert "UF_CRM_TASK" in body["select"]
            link = ["D_5"] if mode == "mismatch" else [CRM_BINDING]
            return httpx.Response(200, json={"result": {"task": {
                "id": "111", "title": TASK_TITLE, "responsibleId": str(responsible),
                "ufCrmTask": link,
            }}})
        raise AssertionError("UNEXPECTED_METHOD_"+meth)
    if ledger is None:
        ledger = tmp_path / "one-task.sqlite"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            result = execute_one_demo_task(
                HOOK, responsible_id=responsible, authorization=confirm,
                local_ledger=ledger, client=client,
            )
            return result, calls, ledger
        except OneShotTaskBlocked as ex:
            return str(ex), calls, ledger


def test_one_demo_task_verified_exactly_once(tmp_path):
    result, calls,ledger = invoke(tmp_path)
    assert result["status"] == "ONE_DEMO_MANAGER_TASK_VERIFIED"
    assert result["task_id"] == 111
    assert result["crm_item_id"] == 3
    assert result["recipient_is_mikhail_verified_by_api"] is False
    assert sum(m == "tasks.task.add" for m,_ in calls) == 1
    assert sum(m == "tasks.task.get" for m,_ in calls) == 1
    with sqlite3.connect(ledger) as con:
        assert con.execute("SELECT state FROM one_demo_task").fetchone()[0] == "VERIFIED"


def test_second_operator_run_never_sends_duplicate(tmp_path):
    first, _,ledger=invoke(tmp_path)
    assert first["status"] == "ONE_DEMO_MANAGER_TASK_VERIFIED"
    second, calls,_=invoke(tmp_path,ledger=ledger)
    assert second == "LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY"
    assert not any(m=="tasks.task.add" for m,_ in calls)


def test_missing_authorization_cannot_touch_network_or_ledger(tmp_path):
    result,calls,ledger=invoke(tmp_path,confirm="YES")
    assert result=="EXPLICIT_ONE_TASK_CONFIRMATION_REQUIRED"
    assert not calls and not ledger.exists()


@pytest.mark.parametrize("value", [0,-1,True,1000000000])
def test_bad_recipient_id_blocked_before_post(tmp_path,value):
    result,calls,ledger=invoke(tmp_path,responsible=value)
    assert result in {"RESPONSIBLE_ID_OUT_OF_RANGE","RESPONSIBLE_ID_INVALID"}
    assert not calls and not ledger.exists()


def test_existing_visible_task_forbids_new_one(tmp_path):
    result,calls,ledger=invoke(tmp_path,mode="existing")
    assert result=="REMOTE_VISIBLE_DEMO_TASK_ALREADY_EXISTS"
    assert not any(m=="tasks.task.add" for m,_ in calls)
    assert not ledger.exists()


def test_post_timeout_is_persisted_uncertain_no_retry(tmp_path):
    result,calls,ledger=invoke(tmp_path,mode="timeout")
    assert result=="TASK_REST_TRANSPORT_OR_TIMEOUT"
    assert sum(m=="tasks.task.add" for m,_ in calls)==1
    with sqlite3.connect(ledger) as con:
        assert con.execute("SELECT state FROM one_demo_task").fetchone()[0] == "UNCERTAIN_POST_RESERVED"
    retried, new_calls,_=invoke(tmp_path,ledger=ledger)
    assert retried=="LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY"
    assert not any(m=="tasks.task.add" for m,_ in new_calls)


def test_post_ok_but_wrong_link_is_uncertain(tmp_path):
    result,calls,ledger=invoke(tmp_path,mode="mismatch")
    assert result=="TASK_READBACK_MISMATCH"
    with sqlite3.connect(ledger) as con:
        assert con.execute("SELECT state,remote_task_id FROM one_demo_task").fetchone()==("POST_ACK_NEEDS_GET",111)
    second, new_calls,_=invoke(tmp_path,ledger=ledger)
    assert second=="LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY"
    assert not any(m=="tasks.task.add" for m,_ in new_calls)


def test_preflight_rest_400_is_precise_and_sends_no_post(tmp_path):
    result,calls,ledger=invoke(tmp_path,mode="list_http400")
    assert result == "REMOTE_PREFLIGHT_TASK_LIST_HTTP_400"
    assert not ledger.exists()
    assert not any(name == "tasks.task.add" for name,_ in calls)
    assert not any("PRIVATE_REQUEST_DATA" in str(v) for _,v in calls)
