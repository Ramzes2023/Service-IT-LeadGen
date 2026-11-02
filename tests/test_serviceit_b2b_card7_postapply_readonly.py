"""READ ONLY reconciliation after an ambiguous item7 9-field CRM write."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import httpx
import pytest

from leadpilot import serviceit_b2b_card7_postapply_readonly as reconc
from leadpilot.serviceit_b2b_card7_preliminary_backfill import (
    _candidate, LEDGER_KEY, AI_FIELDS, SCORE, BADGE, SCORE_VERSION,
)


def sample(*, mode="EMPTY", count=9):
    protected,ai=_candidate()
    item={"id":7,"entityTypeId":1062,**protected,
          **{k:None for k in AI_FIELDS},
          SCORE:None,BADGE:None,SCORE_VERSION:None}
    if mode=="EXACT":
        item.update(ai)
    if mode=="PARTIAL":
        item.update({k:ai[k] for k in sorted(AI_FIELDS)[:count]})
    return item


def alias(key):
    return "ufCrm13"+"".join(x.title() for x in key[len("UF_CRM_13_"):].split("_"))


def fake(*, mode="EMPTY", count=9, mismatch=None):
    item=sample(mode=mode,count=count)
    if mismatch:
        item[mismatch]="DO_NOT_LEAK_PRIVATE_SOURCE_VALUE"
    calls=[]
    def request(method,params):
        calls.append((method,deepcopy(params)))
        if method=="crm.item.get":
            if params["useOriginalUfNames"]=="Y":
                return {"item":deepcopy(item)}
            return {"item":{alias(k):v for k,v in item.items() if k in AI_FIELDS}
                    | {k:v for k,v in item.items() if k not in AI_FIELDS}}
        if method=="crm.item.fields":
            assert params["useOriginalUfNames"]=="N"
            return {"fields":{alias(k):{"upperName":k} for k in AI_FIELDS}}
        raise AssertionError("Never allow mutation")
    return request,calls


def write_test_ledger(path,state="UNCERTAIN",digest=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    protected,ai=_candidate()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE backfill_ops (op_key TEXT PRIMARY KEY,state TEXT NOT NULL,digest TEXT NOT NULL)")
        db.execute("INSERT INTO backfill_ops VALUES (?,?,?)",(
            LEDGER_KEY,state,digest or reconc._digest(protected,ai)))


def test_uncertain_ledger_but_all_nine_fields_present(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    write_test_ledger(path)
    before=path.read_bytes()
    fetch,calls=fake(mode="EXACT")
    result=reconc.reconcile_readonly(fetch,db_file=path)
    assert result["decision"]=="LIVE_NINE_FIELDS_MATCH_LEDGER_UNCERTAIN"
    assert result["nine_exact_original"]==result["nine_exact_camel"]==9
    assert result["all_nine_match_both_modes"] is True
    assert result["crm_writes"]==0 and result["ledger_mutations"]==0
    assert result["safe_to_repeat_previous_apply"] is False
    assert path.read_bytes()==before
    assert [m for m,p in calls]==["crm.item.get","crm.item.get","crm.item.fields"]


def test_uncertain_ledger_empty_readback_does_not_authorize_retry(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    write_test_ledger(path)
    fetch,calls=fake()
    result=reconc.reconcile_readonly(fetch,db_file=path)
    assert result["decision"]=="LIVE_NINE_FIELDS_EMPTY_LEDGER_UNCERTAIN"
    assert result["nine_empty_original"]==result["nine_empty_camel"]==9
    assert result["safe_to_repeat_previous_apply"] is False
    assert result["backfill_post_retried"] is False
    assert all(method!="crm.item.update" for method,params in calls)


def test_not_reserved_ledger_means_prior_write_path_not_reached(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    fetch,_=fake()
    result=reconc.reconcile_readonly(fetch,db_file=path)
    assert result["ledger"]["state"]=="NOT_RESERVED"
    assert result["decision"]=="LIVE_NINE_FIELDS_EMPTY_NO_RESERVATION"
    assert not path.exists()


def test_one_field_from_nine_is_not_mistaken_for_success(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    write_test_ledger(path)
    fetch,_=fake(mode="PARTIAL",count=1)
    result=reconc.reconcile_readonly(fetch,db_file=path)
    assert result["decision"]=="PARTIAL_OR_ALIAS_CONFLICT_READONLY_REVIEW"
    assert result["nine_exact_original"]==1
    assert result["nine_empty_original"]==8
    assert result["safe_to_repeat_previous_apply"] is False


def test_mismatched_digest_blocks_success_inference(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    write_test_ledger(path,digest="0"*64)
    fetch,_=fake(mode="EXACT")
    result=reconc.reconcile_readonly(fetch,db_file=path)
    assert result["decision"]=="LEDGER_DIGEST_MISMATCH_BLOCKED"
    assert result["ledger"]["expected_digest_matches"] is False


def test_changed_protected_field_emits_only_key_name(tmp_path):
    fetch,_=fake(mismatch="UF_CRM_13_SVCIT_COMPANY_NAME")
    result=reconc.reconcile_readonly(fetch,db_file=tmp_path/"backfill-v1.sqlite3")
    assert "UF_CRM_13_SVCIT_COMPANY_NAME" in result["protected_mismatch_codes"]
    assert "DO_NOT_LEAK_PRIVATE_SOURCE_VALUE" not in json.dumps(result)


def test_wrong_item_identity_stops_processing(tmp_path):
    def wrong(method,params):
        return {"item":{"id":3,"entityTypeId":1062,"categoryId":21}}
    with pytest.raises(reconc.ReconcileBlocked,match="ITEM_SCOPE_INVALID"):
        reconc.reconcile_readonly(wrong,db_file=tmp_path/"backfill-v1.sqlite3")


def test_bad_sqlite_kept_unchanged_and_reported(tmp_path):
    path=tmp_path/"backfill-v1.sqlite3"
    path.write_bytes(b"NOT_A_SQLITE_DATABASE")
    fetch,_=fake()
    report=reconc.reconcile_readonly(fetch,db_file=path)
    assert report["decision"]=="LEDGER_FORENSIC_REQUIRED"
    assert path.read_bytes()==b"NOT_A_SQLITE_DATABASE"


def test_http_transport_allows_only_three_read_requests():
    paths=[]
    fetch,_=fake(mode="EXACT")
    def handler(request):
        method=request.url.path.rsplit("/",1)[-1].removesuffix(".json")
        paths.append(method)
        payload=json.loads(request.content)
        return httpx.Response(200,json={"result":fetch(method,payload)})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        read=reconc._transport(
            "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY123456789",client)
        for key in [
            ("crm.item.get",{"entityTypeId":1062,"id":7,"useOriginalUfNames":"Y"}),
            ("crm.item.get",{"entityTypeId":1062,"id":7,"useOriginalUfNames":"N"}),
            ("crm.item.fields",{"entityTypeId":1062,"useOriginalUfNames":"N"}),
        ]:
            read(*key)
        with pytest.raises(reconc.ReconcileBlocked,match="ONLY_EXACT_ITEM_READ_ALLOWED"):
            read("crm.item.update",{"entityTypeId":1062,"id":7,"fields":{}})
    assert paths==["crm.item.get","crm.item.get","crm.item.fields"]


def test_missing_configuration_prints_no_secret(monkeypatch,capsys):
    monkeypatch.setenv("SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK","EXAMPLE_SECRET_NOT_FOR_OUTPUT")
    monkeypatch.delenv("LOCALAPPDATA",raising=False)
    assert reconc.main(["--inspect-live","--webhook-from-env"])==2
    data=capsys.readouterr().out
    assert "EXAMPLE_SECRET_NOT_FOR_OUTPUT" not in data
    assert json.loads(data)["crm_writes"]==0


def test_no_possible_write_calls_in_reconciler():
    src=Path(reconc.__file__).read_text(encoding="utf-8")
    for forbidden in ('rest("crm.item.update"','rest("crm.item.add"',
                      'client.post(base+"/crm.item.update',
                      '_reserve_once(', '_mark_verified(', 'event.bind',
                      'tasks.task.add','GIGACHAT_CREDENTIALS'):
        assert forbidden not in src
