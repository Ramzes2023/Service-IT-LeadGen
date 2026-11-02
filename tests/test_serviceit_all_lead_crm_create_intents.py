"""All-company, multi-signal complete 1062 create-intents. Offline tests only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_all_lead_crm_create_intents import (
    build_all_lead_intents, safe_summary, main, CreateIntentRejected,
    CODES, INTENT_CONTRACT,
)
from leadpilot.serviceit_kanban_presentation import BADGE_FIELD, SCORE_FIELD, VERSION_FIELD

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "serviceit_source_batch_demo.json"


def sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_complete_one_request_per_company_includes_score_version_badge():
    plan=build_all_lead_intents(sample())
    assert plan["status"]=="SERVICEIT_ALL_LEAD_CRM_CREATE_INTENTS_PREVIEW"
    assert plan["unique_companies"]==2
    assert plan["repeated_signals_ignored"]==1
    assert plan["crm_writes"]==0
    assert plan["production_writer_activated"] is False
    assert len(plan["intents"])==2
    assert sorted(x["score"] for x in plan["intents"])==[85,90]
    for op in plan["intents"]:
        assert op["contract_version"]==INTENT_CONTRACT
        assert op["status"]=="PREVIEW_ONLY_NOT_AUTHORIZED"
        assert op["can_execute"] is False
        assert op["crm_write_performed"] is False
        assert op["corporate_dedupe_verified"] is False
        assert op["method_if_future_authorized"]=="crm.item.add"
        params=op["params_NOT_SENT"]
        assert params["entityTypeId"]==1062
        assert params["useOriginalUfNames"]=="Y"
        fields=params["fields"]
        assert len(fields)==20
        assert set(fields)==CODES|{"title","categoryId","stageId"}
        assert fields["categoryId"]==21
        assert fields["stageId"]=="DT1062_21:NEW"
        assert fields[SCORE_FIELD]==op["score"]
        assert fields[VERSION_FIELD]=="mvp-2026-10-v1"
        assert fields[BADGE_FIELD]==op["color_badge"]
        assert fields["UF_CRM_13_SVCIT_STOP_REASON"]=="DEMO / НЕ ЗВОНИТЬ"
        assert fields["UF_CRM_13_SVCIT_SIGNAL_KIND"]=="demo-synthetic"
        assert "phone" not in fields and "email" not in fields


def test_same_batch_is_deterministic_and_repeated_source_not_new_card():
    source=sample()
    expected=build_all_lead_intents(source)
    reversed_input=deepcopy(source)
    reversed_input["entries"].reverse()
    for entry in reversed_input["entries"]:
        entry["signals"].reverse()
    got=build_all_lead_intents(reversed_input)
    assert got==expected


def test_research_cannot_be_marked_executable_by_supplied_verified_claims():
    source=sample()
    source["source_kind"]="research"
    verified={k:"VERIFIED" for k in (
        "company_registry","source","existing_customer","crm_dedup",
        "stop_list","legal_basis","contact")}
    for entry in source["entries"]:
        entry["checks"]=verified
    plan=build_all_lead_intents(source)
    for op in plan["intents"]:
        assert op["can_execute"] is False
        assert op["corporate_dedupe_verified"] is False
        assert op["params_NOT_SENT"]["fields"]["UF_CRM_13_SVCIT_STOP_REASON"].startswith("HOLD")
        assert op["params_NOT_SENT"]["fields"]["UF_CRM_13_SVCIT_SIGNAL_KIND"]=="research-unverified"
        assert op["idempotency_key"].startswith("RESEARCH-HOLD-")
        assert op["params_NOT_SENT"]["fields"][BADGE_FIELD]==op["color_badge"]


def test_no_production_keys_or_contacts_in_cli(capsys):
    assert main(["--input",str(FIXTURE)])==0
    printed=capsys.readouterr().out
    assert "DEMO COMPANY" not in printed
    assert "7700000009" not in printed
    assert "example.invalid" not in printed
    assert "params_NOT_SENT" not in printed
    summary=json.loads(printed)
    assert summary["unique_companies"]==2
    assert summary["items"][0]["field_count"]==20
    assert all(x["status"]=="PREVIEW_ONLY_NOT_AUTHORIZED" for x in summary["items"])


def test_deny_injected_title_even_if_scoring_accepted():
    data=sample()
    data["entries"][0]["company"]["name"]="DEMO <bad>"
    data["entries"][1]["company"]["name"]="DEMO <bad>"
    with pytest.raises(CreateIntentRejected,match="INTENT_LABEL_UNSAFE"):
        build_all_lead_intents(data)


def test_preview_contains_no_live_client_or_webhook():
    source=Path(__file__).resolve().parents[1]/"leadpilot"/"serviceit_all_lead_crm_create_intents.py"
    content=source.read_text(encoding="utf-8")
    for disallowed in ('crm.item.update.json','crm.item.add.json',
                       'rest("crm.item.add"', 'httpx.Client(',
                       'requests.post(', 'State(', 'state.reserve(',
                       'send_email(', 'tasks.task.add'):
        assert disallowed not in content


def test_two_card_add_get_mock_roundtrip_with_duplicate_guard():
    # Mocked end-to-end semantics only. NEVER a live Bitrix HTTP request.
    plans=build_all_lead_intents(sample())
    stored={}
    ids={}
    def handler(request):
        assert request.url.host=="bitrix-mock.invalid"
        body=json.loads(request.content)
        if request.url.path.endswith("/crm.item.add.json"):
            params=body
            fields=params["fields"]
            identity=fields["UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"]
            # Simulation refuses a duplicate key instead of creating two cards.
            if identity in ids:
                return httpx.Response(409,json={"error":"EXACT_DUPLICATE"})
            record_id=len(ids)+100
            ids[identity]=record_id
            stored[record_id]={"id":record_id,"entityTypeId":1062,**fields}
            return httpx.Response(200,json={"result":{"item":{"id":record_id}}})
        if request.url.path.endswith("/crm.item.get.json"):
            assert body["entityTypeId"]==1062
            return httpx.Response(200,json={"result":{"item":stored[body["id"]]}})
        raise AssertionError("UNEXPECTED_METHOD")
    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        for plan in plans["intents"]:
            payload=plan["params_NOT_SENT"]
            assert plan["can_execute"] is False  # bypass only within test fake sink
            response=fake.post("https://bitrix-mock.invalid/crm.item.add.json",json=payload)
            assert response.status_code==200
            identifier=response.json()["result"]["item"]["id"]
            item=fake.post("https://bitrix-mock.invalid/crm.item.get.json",
                           json={"entityTypeId":1062,"id":identifier})
            assert item.status_code==200
            fields=item.json()["result"]["item"]
            assert fields[BADGE_FIELD]==plan["color_badge"]
            assert fields[SCORE_FIELD]==plan["score"]
            assert fields[VERSION_FIELD]=="mvp-2026-10-v1"
        same=fake.post("https://bitrix-mock.invalid/crm.item.add.json",
                       json=plans["intents"][0]["params_NOT_SENT"])
        assert same.status_code==409
    assert len(stored)==2 and len(ids)==2


def test_invalid_payload_redacted_cli(tmp_path,capsys):
    file=tmp_path/"input.json"
    file.write_text('{"secret":"NO_PRINT_DO_NOT_LEAK"}',encoding="utf-8")
    assert main(["--input",str(file)])==2
    printed=capsys.readouterr().out
    assert "NO_PRINT_DO_NOT_LEAK" not in printed
    assert json.loads(printed)["crm_writes"]==0
