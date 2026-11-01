"""Read-only task preflight diagnostics: never log secrets or REST contents."""
from hashlib import sha256
import json

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from leadpilot.bitrix_1062_task_diagnostics import diagnose_task_metadata

HOOK = "https://serviseit.bitrix24.ru/rest/1/SYNTHETICDEMOSECRET123"


def results():
    item = dict.fromkeys(ALL_FIELD_CODES, "DEMO")
    item.update({
        "id": 3, "entityTypeId": 1062, "categoryId": 21,
        "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
        "stageId": "DT1062_21:CLIENT",
        "contactIds": [], "companyId": None,
        "UF_CRM_13_SVCIT_COMPANY_INN": "7700000009",
        "UF_CRM_13_SVCIT_COMPANY_NAME": "DEMO COMPANY-001",
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "synthetic-fixture",
        "UF_CRM_13_SVCIT_SIGNAL_URL": "https://example.invalid/serviceit-demo/request/001",
        "UF_CRM_13_SVCIT_SIGNAL_ID": "DEMO-ITREQ-001",
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
            "TITLE": {"type": "string"},
            "RESPONSIBLE_ID": {"type": "integer"},
            "UF_CRM_TASK": {"type": "crm"},
        }},
        "crm.item.get": {"item": item},
    }


def check(*, status=None, code="ACCESS_DENIED", payload=None, oversized=False):
    data = results() if payload is None else payload
    calls = []
    def handler(request):
        method = request.url.path.split("/")[-1].removesuffix(".json")
        assert method in data
        assert request.url.host == "serviseit.bitrix24.ru"
        calls.append(method)
        if method == status:
            if oversized:
                return httpx.Response(200, content=b"A" * (512_001))
            return httpx.Response(403, json={
                "error": code,
                "error_description": "PRIVATE_TOKEN_AND_CORPORATE_DATA_SHOULD_NOT_BE_SHOWN",
            })
        return httpx.Response(200, json={"result": data[method]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return diagnose_task_metadata(HOOK, client=client), calls


def test_all_four_read_methods_then_metadata_ready_but_no_task():
    out, calls = check()
    assert out["status"] == "METADATA_READY_TASK_NOT_CREATED"
    assert out["methods_ok"] == 4
    assert calls == [
        "crm.type.get", "crm.enum.ownertype", "tasks.task.getFields", "crm.item.get",
        "crm.type.get", "crm.enum.ownertype", "tasks.task.getFields", "crm.item.get",
    ]
    assert out["metadata_gates"]["task_link_enabled"] is True
    assert out["task_created"] is False
    assert out["crm_writes"] == "NO"
    assert out["customer_contact"] == "NO"


def test_missing_task_scope_is_method_specific_and_does_not_echo_secret():
    out, calls = check(status="tasks.task.getFields")
    assert len(calls) == 4
    assert out["status"] == "BLOCKED_READ_ONLY"
    assert out["methods"]["tasks.task.getFields"] == {
        "state": "HTTP_ERROR", "http_status": 403, "api_error_code": "ACCESS_DENIED",
    }
    assert out["methods"]["crm.item.get"]["state"] == "OK"
    assert out["task_scope_missing_possible"] is True
    body = json.dumps(out)
    assert "PRIVATE_TOKEN_AND" not in body
    assert "SYNTHETICDEMOSECRET123" not in body
    assert "7700000009" not in body


def test_missing_crm_scope_is_separate_from_task_scope():
    out, calls = check(status="crm.enum.ownertype")
    assert len(calls) == 4
    assert out["crm_scope_missing_possible"] is True
    assert out["task_scope_missing_possible"] is False


def test_arbitrary_api_error_is_never_echoed():
    out, _ = check(status="crm.item.get", code="DONT_PRINT_THIS_VALUE")
    assert out["methods"]["crm.item.get"]["api_error_code"] == "OTHER_OR_NOT_PROVIDED"
    assert "DONT_PRINT_THIS_VALUE" not in json.dumps(out)


def test_oversize_fails_closed():
    out, _ = check(status="tasks.task.getFields", oversized=True)
    assert out["methods"]["tasks.task.getFields"]["state"] == "OVERSIZED_RESPONSE"
    assert out["status"] == "BLOCKED_READ_ONLY"


def test_setting_disabled_reports_flags_not_private_data():
    data = results()
    data["crm.type.get"]["type"]["linkedUserFields"] = {}
    out, _ = check(payload=data)
    assert out["status"] == "BLOCKED_READ_ONLY"
    assert out["metadata_gates"]["task_link_enabled"] is False
    assert out["methods_ok"] == 4
    assert "DEMO COMPANY-001" not in json.dumps(out)


def test_wrong_exact_item_is_blocked():
    data = results()
    data["crm.item.get"]["item"]["id"] = 5
    out, _ = check(payload=data)
    assert out["metadata_gates"]["exact_demo_card_readback"] is False
    assert out["task_created"] is False


def test_webhook_restricted_to_corporate_portal():
    with pytest.raises(ValueError):
        diagnose_task_metadata("https://other.example/rest/1/token")
