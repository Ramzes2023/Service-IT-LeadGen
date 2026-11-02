"""No-write forensic diagnosis of legacy exact B2B card7 issue66 source."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from leadpilot import serviceit_b2b_card7_forensic_readonly as diag
from leadpilot import serviceit_b2b_card7_preliminary_backfill as backfill


def original_card():
    original,_=backfill._candidate()
    return {"id":7,"entityTypeId":1062,**original,
            **{code:None for code in backfill.AI_FIELDS},
            **{code:None for code in (backfill.SCORE,backfill.SCORE_VERSION,backfill.BADGE)}}


def original_schema(*, missing_multiple=False):
    return {"fields":{code:{"type":"string","isReadOnly":False,
             "isImmutable":False, **({} if missing_multiple else {"isMultiple":False})}
             for code in backfill.AI_FIELDS}}


class Fake:
    def __init__(self,item=None,schema=None):
        self.item=deepcopy(item if item is not None else original_card())
        self.schema=deepcopy(schema if schema is not None else original_schema())
        self.calls=[]
    def __call__(self,method,params):
        self.calls.append(method)
        if method=="crm.item.get":
            assert params=={"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"}
            return {"item":deepcopy(self.item)}
        if method=="crm.item.fields":
            assert params=={"entityTypeId":1062,"useOriginalUfNames":"Y"}
            return deepcopy(self.schema)
        raise AssertionError("NO_UPDATES_ALLOWED")


def test_exact_item_and_empty_state_results_no_issues_and_no_db(tmp_path):
    rest=Fake()
    db=tmp_path/"missing.sqlite3"
    out=diag.inspect_forensic(rest,state_path=db)
    assert out["status"]=="B2B_CARD7_FORENSIC_READONLY_RESULT"
    assert out["protected_exact"] is True
    assert out["issues"]==[]
    assert out["prior_pr78_readonly_ready_if_unchanged"] is True
    assert out["backfill_ledger_state"]=="NOT_RESERVED"
    assert out["crm_writes"]==out["ledger_mutations"]==0
    assert rest.calls==["crm.item.get","crm.item.fields"]
    assert not db.exists()


def test_source_mismatch_reports_field_code_not_content(tmp_path):
    item=original_card()
    item["UF_CRM_13_SVCIT_COMPANY_NAME"]="PERSON_PRIVATE_COMPANY_NAME_DO_NOT_OUTPUT"
    item["title"]="PRIVATE_TITLE_DO_NOT_OUTPUT"
    rest=Fake(item=item)
    out=diag.inspect_forensic(rest,state_path=tmp_path/"missing.sqlite3")
    assert out["issues"]==["PROTECTED_FIELDS_NOT_EXACT"]
    assert out["protected_mismatch_codes"]==[
        "UF_CRM_13_SVCIT_COMPANY_NAME","title"]
    serialized=json.dumps(out,ensure_ascii=False)
    assert "PERSON_PRIVATE_COMPANY_NAME" not in serialized
    assert "PRIVATE_TITLE" not in serialized
    assert "http" not in serialized.lower()
    assert out["crm_writes"]==0


def test_missing_metadata_mult_flag_detected_without_weakened_writer(tmp_path):
    rest=Fake(schema=original_schema(missing_multiple=True))
    out=diag.inspect_forensic(rest,state_path=tmp_path/"missing.sqlite3")
    assert "AI_SCHEMA_LEGACY_STRICTNESS_OR_MISSING" in out["issues"]
    assert all(v["writable"] and not v["legacy_gate_pass"]
               and v["reason"]=="MULTIPLICITY_NOT_EXPLICIT_FALSE"
               for v in out["ai_metadata"].values())
    assert out["live_backfill_authorized"] is False


def test_existing_ai_values_not_overwritten_or_emitted(tmp_path):
    item=original_card()
    field="UF_CRM_13_SVCIT_AI_FULL_REPORT"
    item[field]="REAL_PRIVATE_REPORT_NOT_FOR_LOG"
    item[backfill.SCORE]=90
    rest=Fake(item=item)
    out=diag.inspect_forensic(rest,state_path=tmp_path/"missing.sqlite3")
    assert "AI_FIELDS_ALREADY_POPULATED" in out["issues"]
    assert "SCORE_OR_BADGE_ALREADY_POPULATED" in out["issues"]
    assert out["ai_field_states"][field]=="POPULATED"
    assert "REAL_PRIVATE_REPORT" not in json.dumps(out)
    assert out["live_backfill_authorized"] is False


def test_wrong_scope_does_not_request_schema(tmp_path):
    item=original_card()
    item["categoryId"]=99
    rest=Fake(item=item)
    with pytest.raises(diag.ForensicBlocked,match="ITEM7_SCOPE_MISMATCH"):
        diag.inspect_forensic(rest,state_path=tmp_path/"missing.sqlite3")
    assert rest.calls==["crm.item.get"]


def test_corrupt_ledger_reported_without_create_or_change(tmp_path):
    db=tmp_path/"backfill-v1.sqlite3"
    db.write_bytes(b"NOT_A_SQLITE_FILE")
    snapshot=db.read_bytes()
    out=diag.inspect_forensic(Fake(),state_path=db)
    assert out["backfill_ledger_state"]=="LEDGER_READ_FAILED"
    assert "BACKFILL_LEDGER_REVIEW" in out["issues"]
    assert db.read_bytes()==snapshot


def test_only_two_exact_read_calls_allowed_in_transport():
    paths=[]
    def handler(request):
        paths.append(request.url.path)
        method=request.url.path.rsplit("/",1)[-1]
        if method=="crm.item.get.json":
            return httpx.Response(200,json={"result":{"item":original_card()}})
        if method=="crm.item.fields.json":
            return httpx.Response(200,json={"result":original_schema()})
        return httpx.Response(400,json={"error":"WRONG_METHOD"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        read=diag._read_only_rest(
            "https://serviseit.bitrix24.ru/rest/2/SYNTHETICTOKENONLY1234",client)
        assert "item" in read("crm.item.get",{
            "entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"})
        assert "fields" in read("crm.item.fields",{
            "entityTypeId":1062,"useOriginalUfNames":"Y"})
        with pytest.raises(diag.ForensicBlocked,match="METHOD_OR_PARAMS_NOT_READONLY"):
            read("crm.item.update",{"entityTypeId":1062,"id":7})
        with pytest.raises(diag.ForensicBlocked):
            read("crm.item.get",{"entityTypeId":1062,"id":8,"useOriginalUfNames":"Y"})
    assert len(paths)==2
    assert all(p.endswith(("/crm.item.get.json","/crm.item.fields.json")) for p in paths)


def test_no_token_leak_when_missing_win_state(monkeypatch,capsys):
    monkeypatch.setenv("SERVICEIT_B2B_CARD7_DIAG_WEBHOOK",
        "https://serviseit.bitrix24.ru/rest/2/SUPERSECRETEXAMPLE123")
    monkeypatch.delenv("LOCALAPPDATA",raising=False)
    assert diag.main(["--inspect-live","--webhook-from-env"])==2
    out=capsys.readouterr().out
    assert "SUPERSECRET" not in out
    assert json.loads(out)["crm_writes"]==0


def test_file_has_no_crm_mutation_calls():
    s=Path(diag.__file__).read_text(encoding="utf-8")
    for snippet in ('rest("crm.item.update"', 'rest("crm.item.add"',
                    'event.bind', 'requests.post(', 'ledger.reserve(',
                    'GIGACHAT_CREDENTIALS','_mark_verified('):
        assert snippet not in s
