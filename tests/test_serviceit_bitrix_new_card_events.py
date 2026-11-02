"""Official event shape and durable dedup acceptance, no external Bitrix calls."""
from pathlib import Path
import json
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from leadpilot.serviceit_bitrix_new_card_events import (
    intake, parse_event, parse_body, queue_status, EventRejected,
)
from leadpilot.serviceit_bitrix_event_receiver import app

TOKEN="synthetic-application-token-DO-NOT-DEPLOY-000000"


def event(etype="ONCRMDYNAMICITEMADD",entity="1062",item="5"):
    return {
        "event":etype, "data":{"FIELDS":{"ID":item,"ENTITY_TYPE_ID":entity}},
        "auth":{"domain":"serviseit.bitrix24.ru",
                "application_token":TOKEN,
                "access_token":"SENSITIVE_ACCESS_TOKEN_NEVER_STORE",
                "refresh_token":"SENSITIVE_REFRESH_TOKEN_NEVER_STORE"},
    }


def form_event():
    return urlencode({
        "event":"ONCRMDYNAMICITEMADD_1062",
        "data[FIELDS][ID]":"123",
        "data[FIELDS][ENTITY_TYPE_ID]":"1062",
        "auth[domain]":"serviseit.bitrix24.ru",
        "auth[application_token]":TOKEN,
        "auth[access_token]":"SECRET_NEVER_STORE",
    }).encode()


def test_two_supported_event_transport_formats_queue_once(tmp_path):
    db=tmp_path/"events.sqlite3"
    first=intake(json.dumps(event()).encode(),"application/json",
                 token=TOKEN,db_file=db)
    second=intake(json.dumps(event()).encode(),"application/json",
                  token=TOKEN,db_file=db)
    third=intake(form_event(),"application/x-www-form-urlencoded; charset=utf-8",
                 token=TOKEN,db_file=db)
    assert first["status"]=="QUEUED_FOR_SOURCE_CHECK"
    assert first["crm_writes"]==first["crm_reads"]==first["gigachat_calls"]==0
    assert second["status"]=="DUPLICATE_EVENT_IGNORED"
    assert third["item_id"]==123
    assert queue_status(db)["pending"]==2
    raw=db.read_bytes()
    assert b"SENSITIVE_ACCESS_TOKEN_NEVER_STORE" not in raw
    assert b"SENSITIVE_REFRESH_TOKEN_NEVER_STORE" not in raw
    assert TOKEN.encode() not in raw
    assert b"serviseit" not in raw


@pytest.mark.parametrize("change",[
    lambda e:e["auth"].update(application_token="wrong"*8),
    lambda e:e["auth"].update(domain="evil.invalid"),
    lambda e:e.update(auth={}),
    lambda e:e["data"]["FIELDS"].update(ID="0"),
    lambda e:e["data"]["FIELDS"].update(ID="x"),
    lambda e:e["data"]["FIELDS"].update(ENTITY_TYPE_ID="invalid"),
])
def test_fail_closed_does_not_create_queue(tmp_path,change):
    x=event();change(x)
    with pytest.raises(EventRejected):
        intake(json.dumps(x).encode(),"application/json",
               token=TOKEN,db_file=tmp_path/"events.sqlite3")
    assert not list(tmp_path.iterdir())


def test_other_type_or_unrelated_event_ignored(tmp_path):
    db=tmp_path/"events.sqlite3"
    a=intake(json.dumps(event(entity="999")).encode(),"application/json",
             token=TOKEN,db_file=db)
    b=intake(json.dumps(event(etype="ONCRMDYNAMICITEMUPDATE")).encode(),
             "application/json",token=TOKEN,db_file=db)
    assert not a["queued"] and not b["queued"]
    assert not db.exists()


def test_body_size_and_invalid_mimetype_fail():
    with pytest.raises(EventRejected,match="EVENT_BODY_TOO_LARGE_OR_EMPTY"):
        parse_body(b"x"*20000,"application/json")
    with pytest.raises(EventRejected,match="CONTENT_TYPE_FORBIDDEN"):
        parse_body(b"{}", "text/plain")
    with pytest.raises(EventRejected,match="EVENT_BODY_INVALID"):
        parse_body(b"broken", "application/x-www-form-urlencoded")


def test_receiver_enabled_intake_duplicate_and_no_credential_reflection(tmp_path,monkeypatch):
    monkeypatch.setenv("SERVICEIT_BITRIX_APPLICATION_TOKEN",TOKEN)
    monkeypatch.setenv("SERVICEIT_BITRIX_EVENT_QUEUE_DB",str(tmp_path/"events.sqlite3"))
    with TestClient(app) as client:
        a=client.post("/integrations/serviceit/bitrix24/new-card",
                      json=event())
        b=client.post("/integrations/serviceit/bitrix24/new-card",
                      json=event())
        unauthorized=event()
        unauthorized["auth"]["application_token"]="totally-wrong-value-1234567890123456"
        c=client.post("/integrations/serviceit/bitrix24/new-card",
                      json=unauthorized)
        d=client.get("/health")
    assert a.status_code==202 and a.json()["queued"]
    assert b.status_code==200 and b.json()["status"]=="DUPLICATE_EVENT_IGNORED"
    assert c.status_code==403 and c.json()["status"]=="EVENT_REJECTED"
    assert queue_status(tmp_path/"events.sqlite3")["pending"]==1
    assert d.json()["production_writer_enabled"] is False
    for result in (a,b,c,d):
        txt=result.text
        assert TOKEN not in txt
        assert "SENSITIVE_ACCESS_TOKEN_NEVER_STORE" not in txt


def test_receiver_fails_closed_when_no_app_token(tmp_path,monkeypatch):
    monkeypatch.delenv("SERVICEIT_BITRIX_APPLICATION_TOKEN",raising=False)
    monkeypatch.setenv("SERVICEIT_BITRIX_EVENT_QUEUE_DB",str(tmp_path/"events.sqlite3"))
    with TestClient(app) as client:
        r=client.post("/integrations/serviceit/bitrix24/new-card",json=event())
    assert r.status_code==503
    assert not list(tmp_path.iterdir())


def test_legacy_cockpit_uncertain_journals_not_touched(tmp_path):
    old1=tmp_path/"cockpit-report-v1.sqlite3"
    old1.write_bytes(b"FROZEN_UNCERTAIN_LEDGER")
    queue=tmp_path/"safe"/"events.sqlite3"
    intake(json.dumps(event()).encode(),"application/json",token=TOKEN,db_file=queue)
    assert old1.read_bytes()==b"FROZEN_UNCERTAIN_LEDGER"


def test_explicit_no_write_or_gigachat_calls_in_source():
    src=(Path(__file__).resolve().parents[1]/"leadpilot"/
         "serviceit_bitrix_new_card_events.py").read_text(encoding="utf-8")
    app_src=(Path(__file__).resolve().parents[1]/"leadpilot"/
             "serviceit_bitrix_event_receiver.py").read_text(encoding="utf-8")
    for token in ("crm.item.update","crm.item.add","tasks.task.add",
                  "classify_cockpit_signal", "httpx.Client(", "event.bind",
                  "GigaChat(", "send_email(", "requests.post("):
        assert token not in src+app_src
