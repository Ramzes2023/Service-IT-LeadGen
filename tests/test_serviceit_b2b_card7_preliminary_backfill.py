"""Exact pre-existing #7 guarded AI-only HOLD backfill, mock-only tests."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.b2b_github_to_bitrix import hold_payload
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS
from leadpilot import serviceit_b2b_card7_preliminary_backfill as backfill

PROTECTED=backfill._candidate()[0]
EXPECTED_AI=backfill._candidate()[1]


def initial_item():
    # Identical source fields from historical original user-authored Issue #66.
    return {"id":7,"entityTypeId":1062,**PROTECTED,
            **{key:None for key in AI_FIELDS},
            backfill.SCORE:None,backfill.BADGE:None,
            backfill.SCORE_VERSION:None}


class FakeRest:
    def __init__(self,*,bad_readback=False,timeout=False,mutate_between=False):
        self.item=initial_item()
        self.calls=[]
        self.bad_readback=bad_readback
        self.timeout=timeout
        self.mutate_between=mutate_between
        self.get_count=0

    def __call__(self,method,params):
        self.calls.append((method,deepcopy(params)))
        if method=="crm.item.get":
            assert params=={"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"}
            self.get_count+=1
            if self.mutate_between and self.get_count==2:
                self.item["UF_CRM_13_SVCIT_REVIEW_DECISION"]="CHANGED"
            return {"item":deepcopy(self.item)}
        if method=="crm.item.fields":
            assert params=={"entityTypeId":1062,"useOriginalUfNames":"Y"}
            return {"fields":{key:{"type":"string","isReadOnly":False,
                                   "isMultiple":False,"isImmutable":False}
                               for key in AI_FIELDS}}
        if method=="crm.item.update":
            assert params["entityTypeId"]==1062 and params["id"]==7
            assert params["useOriginalUfNames"]=="Y"
            assert set(params["fields"])==set(AI_FIELDS)
            if self.timeout:raise backfill.BackfillBlocked("CRM_POST_UNCERTAIN")
            self.item.update(params["fields"])
            if self.bad_readback:
                self.item["UF_CRM_13_SVCIT_AI_STATUS"]=""
            return {"item":deepcopy(self.item)}
        raise AssertionError("UNEXPECTED_REST_METHOD")


def test_readonly_preview_exact_card7_does_not_reserve_or_write(tmp_path):
    remote=FakeRest()
    db=tmp_path/"backfill-v1.sqlite3"
    result=backfill.inspect_or_apply(remote,db_file=db)
    assert result["status"]=="B2B_CARD7_PRELIMINARY_AI_READONLY_READY"
    assert result["field_count"]==9 and result["state"]=="NOT_RESERVED"
    assert result["crm_writes"]==0 and result["provider"]=="NOT_RUN"
    assert len(remote.calls)==2
    assert [p for p,_ in remote.calls]==["crm.item.get","crm.item.fields"]
    assert db.exists() is False
    assert all(remote.item[k] is None for k in AI_FIELDS)


def test_exact_one_shot_post_only_nine_fields_with_full_readback(tmp_path):
    remote=FakeRest()
    db=tmp_path/"backfill-v1.sqlite3"
    result=backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert result["status"]=="B2B_CARD7_PRELIMINARY_AI_VERIFIED"
    assert result["crm_writes"]==1 and result["fields_verified"]==9
    assert backfill.ledger_status(db)=="VERIFIED"
    assert [m for m,p in remote.calls] == [
        "crm.item.get","crm.item.fields","crm.item.get",
        "crm.item.update","crm.item.get"]
    assert all(remote.item[k]==v for k,v in EXPECTED_AI.items())
    assert remote.item[backfill.SCORE] is None
    assert remote.item[backfill.BADGE] is None
    assert remote.item[backfill.SCORE_VERSION] is None
    assert remote.item["stageId"]=="DT1062_21:NEW"
    assert "NOT_RUN" in remote.item["UF_CRM_13_SVCIT_AI_STATUS"]
    # Once a record was reserved, no second POST from this process.
    with pytest.raises(backfill.BackfillBlocked):
        backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert len([m for m,p in remote.calls if m=="crm.item.update"])==1


def test_ambiguous_post_never_retries_and_ledger_persists(tmp_path):
    remote=FakeRest(timeout=True)
    db=tmp_path/"backfill-v1.sqlite3"
    with pytest.raises(backfill.BackfillBlocked,match="CRM_POST_UNCERTAIN"):
        backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert backfill.ledger_status(db)=="UNCERTAIN"
    with pytest.raises(backfill.BackfillBlocked,match="BACKFILL_ALREADY_RESERVED"):
        backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert sum(m=="crm.item.update" for m,p in remote.calls)==1


def test_readback_mismatch_quarantines_uncertain_without_retry(tmp_path):
    remote=FakeRest(bad_readback=True)
    db=tmp_path/"backfill-v1.sqlite3"
    with pytest.raises(backfill.BackfillBlocked,match="AI_READBACK_UNCERTAIN"):
        backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert backfill.ledger_status(db)=="UNCERTAIN"
    assert sum(m=="crm.item.update" for m,p in remote.calls)==1


def test_item_modified_between_reads_fails_before_reservation(tmp_path):
    remote=FakeRest(mutate_between=True)
    db=tmp_path/"backfill-v1.sqlite3"
    with pytest.raises(backfill.BackfillBlocked):
        backfill.inspect_or_apply(remote,db_file=db,apply=True,approved=True)
    assert not db.exists()
    assert sum(m=="crm.item.update" for m,p in remote.calls)==0


@pytest.mark.parametrize("field,value",[
    ("id",8),("categoryId",22),("title","MOVED"),
    ("UF_CRM_13_SVCIT_SIGNAL_KIND","unknown"),
    ("UF_CRM_13_SVCIT_EVIDENCE_STATUS","CONFIRMED"),
    ("UF_CRM_13_SVCIT_AI_STATUS","ALREADY_WRITTEN"),
    ("UF_CRM_13_SVCIT_AI_SCORE_BADGE","🟢 90/100"),
    ("UF_CRM_13_SVCIT_LEAD_SCORE",90),
])
def test_nonexact_source_or_existing_ai_blocks_before_write(tmp_path,field,value):
    remote=FakeRest()
    remote.item[field]=value
    with pytest.raises(backfill.BackfillBlocked):
        backfill.inspect_or_apply(remote,db_file=tmp_path/"backfill-v1.sqlite3",
                                  apply=True,approved=True)
    assert all(m!="crm.item.update" for m,p in remote.calls)


def test_bad_ai_metadata_is_fail_closed(tmp_path):
    remote=FakeRest()
    base=remote.__call__
    def broken(method,params):
        output=base(method,params)
        if method=="crm.item.fields":
            output["fields"]["UF_CRM_13_SVCIT_AI_QUOTE"]["isReadOnly"]=True
        return output
    with pytest.raises(backfill.BackfillBlocked,match="NINE_AI_FIELDS_NOT_WRITABLE"):
        backfill.inspect_or_apply(broken,db_file=tmp_path/"backfill-v1.sqlite3",
                                 apply=True,approved=True)
    assert not any(m=="crm.item.update" for m,p in remote.calls)


def test_approval_required_and_cli_default_no_credentials(capsys,monkeypatch):
    assert backfill.main([])==0
    assert json.loads(capsys.readouterr().out)["crm_writes"]==0
    assert backfill.main(["--apply-once"])==2
    assert json.loads(capsys.readouterr().out)["status"]=="B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN"


def test_original_fixture_is_grounded_in_user_issue66_not_fabricated():
    notice,services=backfill._notice()
    assert notice["tender_id"]=="4661617"
    assert notice["subject"].startswith("Право заключения договора")
    assert "1С:ЗУП" in notice["subject"]
    assert notice["original_publication_verified"] is False
    assert services==["1C"]
    assert set(backfill._candidate()[1])==set(AI_FIELDS)


def test_no_old_badge_ledger_names_or_unrelated_crm_methods_in_writer():
    src=Path(backfill.__file__).read_text(encoding="utf-8")
    assert "cockpit-report-v1.sqlite3" not in src
    assert "badge:1062:21:3" not in src
    assert "crm.item.add" not in src
    assert "crm.item.list" not in src
    assert "tasks.task.add" not in src
