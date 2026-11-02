"""Exactly three reads and no reservation in PR78 pre-write forensic replay."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import httpx
import pytest

from leadpilot import serviceit_b2b_card7_prewrite_stage_readonly as diag
from leadpilot import serviceit_b2b_card7_preliminary_backfill as old


def initial():
    fields,ai=old._candidate()
    return {"id":7,"entityTypeId":1062,**fields,
            **{k:None for k in ai},
            old.SCORE:None,old.SCORE_VERSION:None,old.BADGE:None}


class Fake:
    def __init__(self,changed=None,mutable_source=False):
        self.item=initial()
        self.changed=changed
        self.mutable_source=mutable_source
        self.calls=[]
        self.reads=0
    def __call__(self,method,params):
        self.calls.append(method)
        if method=="crm.item.get":
            assert params=={"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"}
            self.reads+=1
            item=deepcopy(self.item)
            if self.reads==2:
                if self.changed:
                    item[self.changed]="synthetic-other-value"
                if self.mutable_source:
                    item["title"]="OTHER_TITLE"
            return {"item":item}
        if method=="crm.item.fields":
            assert params=={"entityTypeId":1062,"useOriginalUfNames":"Y"}
            return {"fields":{key:{"type":"string","isReadOnly":False,
                                   "isMultiple":False,"isImmutable":False}
                              for key in old.AI_FIELDS}}
        raise AssertionError("Only read API allowed")


def test_all_prewrite_stages_pass_without_creating_state(tmp_path):
    rest=Fake()
    path=tmp_path/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
    result=diag.diagnostic(rest,fixed_ledger=path)
    assert result["status"]=="B2B_CARD7_PREWRITE_STAGE_READONLY"
    assert result["first_blocking_stage"]=="NONE_IN_READONLY_REPLAY"
    assert set(result["checks"].values())=={"PASS"}
    assert result["ledger_state"]=="NOT_RESERVED"
    assert result["filesystem_readiness"]["no_file_created"] is True
    assert not path.parent.exists()
    assert result["crm_writes"]==result["ledger_mutations"]==0
    assert rest.calls==["crm.item.get","crm.item.fields","crm.item.get"]


def test_automatic_updated_at_causes_old_item_equality_failure(tmp_path):
    rest=Fake(changed="updatedTime")
    path=tmp_path/"backfill-v1.sqlite3"
    out=diag.diagnostic(rest,fixed_ledger=path)
    assert out["checks"]["first_snapshot"]=="PASS"
    assert out["checks"]["schema"]=="PASS"
    assert out["checks"]["ledger"]=="PASS"
    assert out["checks"]["second_snapshot"]=="PASS"
    assert out["checks"]["entire_item_equal"]=="CRM_ITEM_CHANGED_BETWEEN_READS"
    assert out["checks"]["pinned_fields_stable"]=="PASS"
    assert out["changing_field_codes"]==["updatedTime"]
    assert out["first_blocking_stage"].startswith("entire_item_equal:")
    assert not path.exists()


def test_mutating_real_source_field_is_reported_but_never_saved(tmp_path):
    rest=Fake(mutable_source=True)
    out=diag.diagnostic(rest,fixed_ledger=tmp_path/"backfill-v1.sqlite3")
    assert out["checks"]["second_snapshot"]=="SOURCE_OR_HOLD_FIELDS_CHANGED"
    assert out["checks"]["pinned_fields_stable"]=="PINNED_FIELD_CHANGED"
    assert out["changing_field_codes"]==["title"]
    assert out["crm_writes"]==0


def test_uncertain_reservation_is_read_not_reset(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE backfill_ops (op_key TEXT PRIMARY KEY,state TEXT,digest TEXT)")
        c.execute("INSERT INTO backfill_ops VALUES (?,?,?)",(old.LEDGER_KEY,"UNCERTAIN","f"*64))
    prior=path.read_bytes()
    out=diag.diagnostic(Fake(),fixed_ledger=path)
    assert out["ledger_state"]=="UNCERTAIN"
    assert out["checks"]["ledger_not_reserved"]=="BACKFILL_ALREADY_RESERVED_DO_NOT_REPEAT"
    assert out["safe_to_repeat_previous_apply"] is False
    assert path.read_bytes()==prior


def test_http_allowlist_three_reads_only():
    calls=[]
    fake=Fake()
    def responder(request):
        payload=json.loads(request.content)
        method=request.url.path.rsplit("/",1)[-1].removesuffix(".json")
        calls.append(method)
        return httpx.Response(200,json={"result":fake(method,payload)})
    with httpx.Client(transport=httpx.MockTransport(responder)) as client:
        reader=diag._transport(
            "https://serviseit.bitrix24.ru/rest/2/SYNTHETICMOCKTOKEN123456",client)
        reader("crm.item.get",{"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"})
        reader("crm.item.fields",{"entityTypeId":1062,"useOriginalUfNames":"Y"})
        reader("crm.item.get",{"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"})
        with pytest.raises(diag.StageDiagnosticBlocked):
            reader("crm.item.update",{"entityTypeId":1062,"id":7,"fields":{}})
    assert calls==["crm.item.get","crm.item.fields","crm.item.get"]


def test_cli_offline_cannot_write(capsys):
    assert diag.main([])==0
    assert json.loads(capsys.readouterr().out)["crm_writes"]==0
    assert diag.main(["--inspect-live"])==2
    assert json.loads(capsys.readouterr().out)["retry_previous_apply"] is False


def test_no_write_in_diagnostic_source():
    src=Path(diag.__file__).read_text(encoding="utf-8")
    assert 'rest("crm.item.update"' not in src
    assert '_reserve_once(' not in src
    assert '_mark_verified(' not in src
    assert 'event.bind' not in src
    assert 'requests.post' not in src
