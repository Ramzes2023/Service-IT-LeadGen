"""Live-shaped but entirely offline Bitrix task metadata probes."""
from hashlib import sha256

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from leadpilot.bitrix_1062_task_metadata import (
    audit_synthetic_task_link, TaskMetadataReadError,
)

WEBHOOK = "https://serviseit.bitrix24.ru/rest/1/INTEGRATIONDEMOONLY0000"


def fixture():
    item = {key: "DEMO SYNTHETIC" for key in ALL_FIELD_CODES}
    item.update({
        "id": "3", "entityTypeId": 1062, "categoryId": 21,
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
        "crm.type.get": {"type": {
            "entityTypeId": 1062, "isUseInUserfieldEnabled": "Y",
            "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "true"},
            "isAutomationEnabled": "N", "isBizProcEnabled": "N",
        }},
        "crm.enum.ownertype": [{"ID": 1062, "SYMBOL_CODE_SHORT": "T426"}],
        "tasks.task.getFields": {"fields": {
            "TITLE": {"type": "string"}, "RESPONSIBLE_ID": {"type": "integer"},
            "UF_CRM_TASK": {"type": "crm"},
        }},
        "crm.item.get": {"item": item},
    }


def inspect(data, *, fail_method=None):
    methods = []
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        methods.append(method)
        if fail_method == method:
            return httpx.Response(403, json={"error": "ACCESS_DENIED"})
        return httpx.Response(200, json={"result": data[method]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return audit_synthetic_task_link(WEBHOOK, client=client), methods


def test_mock_portal_four_read_calls_and_no_task_add():
    out, methods = inspect(fixture())
    assert methods == ["crm.type.get", "crm.enum.ownertype",
                       "tasks.task.getFields", "crm.item.get"]
    assert out["status"] == "LINKED_TASK_METADATA_READY"
    assert out["task_created"] is False and out["crm_writes"] == "NO"
    assert out["safe_to_create_task_now"] is False
    assert out["manager_employee_id_verified"] is False


@pytest.mark.parametrize("section,field,new", [
    ("crm.type.get", "isUseInUserfieldEnabled", "N"),
    ("crm.type.get", "linkedUserFields", {}),
    ("crm.type.get", "isAutomationEnabled", "Y"),
    ("crm.type.get", "isBizProcEnabled", "Y"),
    ("crm.type.get", "isBizProcEnabled", None),
])
def test_process_not_ready_blocks(section, field, new):
    data=fixture();data[section]["type"][field]=new
    result,_=inspect(data)
    assert result["status"] == "LINKED_TASK_METADATA_BLOCKED"
    assert result["safe_to_create_task_now"] is False


def test_bad_task_schema_or_owner_symbol_blocks():
    data=fixture();data["tasks.task.getFields"]["fields"]["UF_CRM_TASK"]["type"]="string"
    assert inspect(data)[0]["task_fields_ready"] is False
    data=fixture();data["crm.enum.ownertype"][0]["SYMBOL_CODE_SHORT"]="T999"
    assert inspect(data)[0]["owner_symbol_verified"] is False


@pytest.mark.parametrize("field,value", [
    ("title","DEMO-OTHER"),("stageId","DT1062_21:FAIL"),
    ("companyId",50),("contactIds",[99]),
    ("UF_CRM_13_SVCIT_COMPANY_INN","7700000010"),
    ("UF_CRM_13_SVCIT_IDEMPOTENCY_KEY","DEMO-IDEMP-"+"b"*32),
])
def test_wrong_crm_card_blocks(field,value):
    data=fixture();data["crm.item.get"]["item"][field]=value
    assert inspect(data)[0]["exact_demo_card_readback"] is False


def test_missing_field_blocks():
    data=fixture()
    del data["crm.item.get"]["item"]["UF_CRM_13_SVCIT_SCORE_VERSION"]
    assert inspect(data)[0]["exact_demo_card_readback"] is False


def test_forbidden_read_error_sanitized():
    with pytest.raises(TaskMetadataReadError):
        inspect(fixture(),fail_method="crm.item.get")
