"""Read-only all-lead Bitrix smart-process schema contract mock tests."""
import json
import os
from pathlib import Path

import pytest

from leadpilot import serviceit_all_lead_crm_schema_preflight as pre
from leadpilot.serviceit_kanban_presentation import BADGE_FIELD, SCORE_FIELD, VERSION_FIELD


class MockSchema:
    def __init__(self):
        self.calls=[]
        self.extra_regexp=False
        self.badge_length=120
        self.read_only=False
        self.missing=False
        self.automation=False
        self.mode_n_keys={
            SCORE_FIELD:"ufCrm13SvcitLeadScore",
            VERSION_FIELD:"ufCrm13SvcitScoreVersion",
            BADGE_FIELD:"ufCrm13SvcitAiScoreBadge",
        }

    def __call__(self,method,params):
        self.calls.append((method,params))
        if method=="crm.type.getByEntityTypeId":
            assert params == {"entityTypeId":1062}
            return {"type":{"id":13,"entityTypeId":1062,
                            "isAutomationEnabled":self.automation,"isBizProcEnabled":False}}
        if method=="crm.item.fields":
            assert params["entityTypeId"]==1062
            mode=params["useOriginalUfNames"]
            return {"fields":{
                (code if mode=="Y" else self.mode_n_keys[code]):{
                    "type":"string" if code!=SCORE_FIELD else "double",
                    "isReadOnly":self.read_only,
                    "isImmutable":False, "isMultiple":False,
                    "upperName":code,
                }
                for code in pre.FIELDS if not (self.missing and code==BADGE_FIELD)
            }}
        if method=="userfieldconfig.list":
            assert params["filter"]=={"entityId":"CRM_13"}
            return {"fields":[{
                "id":index+1, "fieldName":code, "entityId":"CRM_13"
            } for index,code in enumerate(pre.FIELDS)]}
        if method=="userfieldconfig.get":
            idx=params["id"]-1
            code=pre.FIELDS[idx]
            return {"field":{
                "entityId":"CRM_13","fieldName":code,
                "multiple":"N",
                "userTypeId":"double" if code==SCORE_FIELD else "string",
                "mandatory":"N",
                "settings":{
                    "REGEXP":"^X+" if self.extra_regexp and code==BADGE_FIELD else "",
                    "MAX_LENGTH":self.badge_length if code==BADGE_FIELD else 0,
                    "MIN_LENGTH":0,
                }
            }}
        raise AssertionError("Unexpected CRM REST method " + method)


def test_corporate_readonly_gate_accepts_writable_schema_and_live_aliases():
    mock=MockSchema()
    out=pre.inspect_all_lead_schema(mock)
    assert out["status"]=="SERVICEIT_ALL_LEAD_SCHEMA_READONLY_PASS"
    assert out["checked_field_count"]==3
    assert out["crm_writes"]==0
    assert out["production_writer_activated"] is False
    assert out["fields"][BADGE_FIELD]["camel_key"]=="ufCrm13SvcitAiScoreBadge"
    assert len(mock.calls)==1+2+1+3
    assert {m for m,p in mock.calls} == {
        "crm.type.getByEntityTypeId","crm.item.fields",
        "userfieldconfig.list","userfieldconfig.get"
    }
    for i in out["fields"].values():
        assert i["original_writable"] and i["camel_writable"]


@pytest.mark.parametrize("failure",[
    "regex","too_short","read_only","field_missing","automation","bad_alias"
])
def test_bad_fields_block_before_writer(failure):
    mock=MockSchema()
    if failure=="regex":mock.extra_regexp=True
    if failure=="too_short":mock.badge_length=10
    if failure=="read_only":mock.read_only=True
    if failure=="field_missing":mock.missing=True
    if failure=="automation":mock.automation=True
    if failure=="bad_alias":mock.mode_n_keys[BADGE_FIELD]="unexpected"
    with pytest.raises(pre.SchemaPreflightBlocked):
        pre.inspect_all_lead_schema(mock)
    assert all(method!="crm.item.update" and method!="crm.item.add"
               for method,params in mock.calls)


def test_offline_never_uses_network(monkeypatch,capsys):
    monkeypatch.setattr(pre.httpx,"Client",
                        lambda: (_ for _ in ()).throw(AssertionError("network forbidden")))
    assert pre.main([])==0
    result=json.loads(capsys.readouterr().out)
    assert result["crm_writes"]==0
    assert "OFFLINE" in result["status"]


def test_invalid_secure_flag_does_not_print_webhook(monkeypatch,capsys):
    monkeypatch.setenv("SERVICEIT_AI_COCKPIT_WEBHOOK","SECRET_NEVER_PRINT")
    assert pre.main(["--inspect-live"])==2
    result=capsys.readouterr().out
    assert "SECRET_NEVER_PRINT" not in result
    assert json.loads(result)["crm_writes"]==0


def test_source_does_not_call_crm_mutations():
    src=Path(pre.__file__).read_text(encoding="utf-8")
    for x in ('rest("crm.item.add"', 'rest("crm.item.update"',
              'rest("userfieldconfig.add"', 'state.reserve(',
              'tasks.task.add','crm.item.details.configuration.set',
              'crm.item.delete','httpx.post('):
        assert x not in src
