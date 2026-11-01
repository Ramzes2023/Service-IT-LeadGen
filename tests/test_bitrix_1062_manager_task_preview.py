"""No-write Bitrix24 linked DEMO manager task contract."""
from copy import deepcopy
from hashlib import sha256

import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from leadpilot.bitrix_1062_manager_task_preview import (
    DemoTaskGateBlocked, task_link_preflight,
)


def fixture():
    item = {
        "id": 3, "entityTypeId": 1062, "categoryId": 21,
        "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
        "stageId": "DT1062_21:CLIENT",
        "contactIds": [], "companyId": None,
        "UF_CRM_13_SVCIT_COMPANY_INN": "7700000009",
        "UF_CRM_13_SVCIT_COMPANY_NAME": "DEMO COMPANY-001",
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "synthetic-fixture",
        "UF_CRM_13_SVCIT_SIGNAL_URL": "https://example.invalid/serviceit-demo/request/001",
        "UF_CRM_13_SVCIT_SIGNAL_ID": "DEMO-ITREQ-001",
        "UF_CRM_13_SVCIT_SIGNAL_KIND": "demo-synthetic",
        "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "DEMO-IDEMP-" + sha256(
            f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()
        ).hexdigest()[:32],
    }
    for code in ALL_FIELD_CODES:
        item.setdefault(code, 86 if code.endswith("LEAD_SCORE") else "DEMO SYNTHETIC")
    process = {
        "entityTypeId": 1062, "isUseInUserfieldEnabled": "Y",
        "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "true"},
        "isAutomationEnabled": "N", "isBizProcEnabled": "N",
    }
    types = [{"ID": 1062, "NAME": "Сервис ИТ — AI-сигналы (тест)",
              "SYMBOL_CODE_SHORT": "T426"}]
    user = {"ID": 42, "ACTIVE": "Y", "EXTERNAL_AUTH_ID": None}
    fields = {"TITLE": {}, "RESPONSIBLE_ID": {}, "UF_CRM_TASK": {"type": "crm"}}
    return {"item": item, "process_type": process, "owner_types": types,
            "responsible_user": user, "task_fields": fields}


def run(values):
    return task_link_preflight(**values)


def test_valid_synthetic_preview_never_activates():
    f = fixture()
    before = deepcopy(f)
    result = run(f)
    assert f == before
    assert result["status"] == "OFFLINE_LINKED_MANAGER_TASK_PREVIEW"
    assert result["task_created"] is False
    assert result["crm_writes"] == "NO"
    assert result["manager_identity_independently_confirmed"] is False
    assert result["params_not_sent"]["fields"]["UF_CRM_TASK"] == ["T426_3"]
    assert result["params_not_sent"]["fields"]["RESPONSIBLE_ID"] == 42
    assert "НЕ ЗВОНИТЬ" in result["params_not_sent"]["fields"]["TITLE"]


@pytest.mark.parametrize("path,value", [
    (("item","id"),5),
    (("item","title"),"DEMO ServiceIT AI — DEMO-ITREQ-002"),
    (("item","stageId"),"DT1062_21:FAIL"),
    (("item","companyId"),8),
    (("item","contactIds"),[12]),
    (("item","UF_CRM_13_SVCIT_COMPANY_INN"),"7700000001"),
    (("item","UF_CRM_13_SVCIT_SIGNAL_SOURCE"),"real-portal"),
    (("item","UF_CRM_13_SVCIT_SIGNAL_URL"),"https://other.example/request"),
    (("item","UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"),"incorrect"),
    (("process_type","isUseInUserfieldEnabled"),"N"),
    (("process_type","linkedUserFields"),{}),
    (("process_type","isAutomationEnabled"),"Y"),
    (("process_type","isBizProcEnabled"),"Y"),
    (("responsible_user","ACTIVE"),"N"),
    (("responsible_user","EXTERNAL_AUTH_ID"),"some-external-id"),
    (("responsible_user","ID"),0),
])
def test_mutations_block(path,value):
    f=fixture()
    f[path[0]][path[1]]=value
    with pytest.raises(DemoTaskGateBlocked):
        run(f)


def test_ambiguous_or_wrong_symbol_blocked():
    f=fixture()
    f["owner_types"].append(dict(f["owner_types"][0]))
    with pytest.raises(DemoTaskGateBlocked,match="CRM_OWNER_TYPE_NOT_UNIQUE"):
        run(f)
    f=fixture()
    f["owner_types"][0]["SYMBOL_CODE_SHORT"]="T999"
    with pytest.raises(DemoTaskGateBlocked,match="CRM_OWNER_SYMBOL_NOT_VERIFIED"):
        run(f)


def test_missing_task_field_schema_blocks():
    f=fixture();f["task_fields"].pop("RESPONSIBLE_ID")
    with pytest.raises(DemoTaskGateBlocked,match="TASK_FIELD_SCHEMA_NOT_CONFIRMED"):
        run(f)


def test_numeric_string_ids_allowed_from_approved_readback():
    f=fixture()
    f["item"]["id"]="3"
    f["responsible_user"]["ID"]="42"
    out=run(f)
    assert out["params_not_sent"]["fields"]["UF_CRM_TASK"] == ["T426_3"]


def test_no_effect_on_real_crm_or_network(monkeypatch):
    import socket
    f=fixture()
    def fail(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    result=run(f)
    assert result["outbound_communications"] == "NO"


def test_invalid_boolean_user_id_rejected():
    f=fixture();f["responsible_user"]["ID"]=True
    with pytest.raises(DemoTaskGateBlocked):
        run(f)


def test_missing_one_of_sixteen_original_uf_fields_blocks():
    f=fixture();f["item"].pop("UF_CRM_13_SVCIT_SCORE_VERSION")
    with pytest.raises(DemoTaskGateBlocked,match="REQUIRED_SIXTEEN_UF_FIELDS_MISSING"):
        run(f)


def test_task_crm_link_field_schema_invalid_blocks():
    f=fixture();f["task_fields"]["UF_CRM_TASK"]["type"]="string"
    with pytest.raises(DemoTaskGateBlocked,match="TASK_CRM_BINDING_FIELD_NOT_CRM_TYPE"):
        run(f)


@pytest.mark.parametrize("name", ["isAutomationEnabled", "isBizProcEnabled"])
def test_unknown_automation_state_blocks(name):
    f=fixture();f["process_type"].pop(name)
    with pytest.raises(DemoTaskGateBlocked,match="SMART_PROCESS_AUTOMATION_MUST_BE_CONFIRMED_OFF"):
        run(f)


def test_another_well_formed_demo_key_rejected():
    f=fixture();f["item"]["UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"]="DEMO-IDEMP-"+"a"*32
    with pytest.raises(DemoTaskGateBlocked,match="SYNTHETIC_KEY_MISMATCH"):
        run(f)
