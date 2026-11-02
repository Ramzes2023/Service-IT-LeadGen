"""Secure, idempotent intake of NEW Bitrix24 cards for all ServiceIT sources.

The server can receive any onCrmDynamicItemAdd event for smart process 1062
and persist an item-ID-only pending review. It NEVER reads CRM, invokes AI,
creates tasks or updates any card. This explicit separation prevents an
unverified B2B-Center HOLD from being silently promoted to a sales lead.

Deploy behind TLS and use a dedicated Bitrix24 application_token obtained
from a properly installed app/outgoing callback configuration. Fail closed
if token is absent. Do not use an inbound REST webhook as event authentication.
"""
from __future__ import annotations

from contextlib import closing
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs

PORTAL="serviseit.bitrix24.ru"
TYPE=1062
EVENTS=frozenset({"ONCRMDYNAMICITEMADD","ONCRMDYNAMICITEMADD_1062"})
MAX_BODY_BYTES=16384
MAX_ITEM_ID=2_000_000_000
QUEUED="PENDING_SOURCE_CHECK"


class EventRejected(ValueError):
    pass


def _require(ok, reason):
    if not ok:
        raise EventRejected(reason)


def _int_id(value):
    if type(value) is int:
        number=value
    elif isinstance(value,str) and re.fullmatch(r"[1-9][0-9]{0,9}",value):
        number=int(value)
    else:
        raise EventRejected("ITEM_ID_OR_TYPE_INVALID")
    _require(0 < number <= MAX_ITEM_ID,"ITEM_ID_OR_TYPE_INVALID")
    return number


def _decode_form(data: str) -> dict:
    try:
        params=parse_qs(data,strict_parsing=True,keep_blank_values=True,
                        max_num_fields=35,encoding="utf-8",errors="strict")
    except (ValueError,UnicodeError):
        raise EventRejected("INVALID_FORM") from None
    def one(key):
        seq=params.get(key)
        _require(seq is not None and len(seq)==1,"REQUIRED_FORM_FIELD_MISSING")
        return seq[0]
    return {
        "event":one("event"),
        "data":{"FIELDS":{
            "ID":one("data[FIELDS][ID]"),
            "ENTITY_TYPE_ID":one("data[FIELDS][ENTITY_TYPE_ID]"),
        }},
        "auth":{
            "domain":one("auth[domain]"),
            "application_token":one("auth[application_token]"),
        },
    }


def parse_event(payload, *, expected_app_token: str) -> dict:
    _require(isinstance(expected_app_token,str) and len(expected_app_token)>=24,
             "SERVER_TOKEN_NOT_CONFIGURED")
    _require(isinstance(payload,dict),"INVALID_EVENT")
    auth=payload.get("auth")
    _require(isinstance(auth,dict),"AUTH_MISSING")
    token=auth.get("application_token")
    _require(isinstance(token,str) and len(token)>=24 and
             hmac.compare_digest(expected_app_token,token),"EVENT_TOKEN_INVALID")
    _require(auth.get("domain")==PORTAL,"PORTAL_NOT_APPROVED")
    event=payload.get("event")
    _require(isinstance(event,str),"EVENT_TYPE_INVALID")
    event=event.upper()
    # Ignore non-create events and other processes, without enqueuing or
    # revealing whether a card exists.
    if event not in EVENTS:
        return {"status":"IGNORED_EVENT","queued":False}
    data=payload.get("data")
    _require(isinstance(data,dict) and isinstance(data.get("FIELDS"),dict),
             "EVENT_FIELDS_INVALID")
    fields=data["FIELDS"]
    typeid=_int_id(fields.get("ENTITY_TYPE_ID"))
    if typeid != TYPE:
        return {"status":"IGNORED_OTHER_PROCESS","queued":False}
    itemid=_int_id(fields.get("ID"))
    return {"status":"ACCEPTED_NEW_ITEM","queued":True,
            "entity_type_id":TYPE,"item_id":itemid}


def parse_body(body: bytes, content_type: str):
    _require(isinstance(body,bytes) and 1<=len(body)<=MAX_BODY_BYTES,
             "EVENT_BODY_TOO_LARGE_OR_EMPTY")
    mimetype=content_type.split(";")[0].strip().lower()
    try:
        if mimetype=="application/json":
            return json.loads(body.decode("utf-8"))
        if mimetype=="application/x-www-form-urlencoded":
            return _decode_form(body.decode("utf-8"))
    except (ValueError,UnicodeError):
        raise EventRejected("EVENT_BODY_INVALID") from None
    raise EventRejected("CONTENT_TYPE_FORBIDDEN")


def enqueue_new_item(db_file: Path, verified: dict) -> dict:
    _require(verified.get("status")=="ACCEPTED_NEW_ITEM"
             and verified.get("entity_type_id")==TYPE
             and type(verified.get("item_id")) is int
             and 0<verified["item_id"]<=MAX_ITEM_ID,
             "QUEUE_ITEM_NOT_VERIFIED")
    _require(isinstance(db_file,Path) and db_file.suffix==".sqlite3",
             "QUEUE_PATH_INVALID")
    # Dedicated queue; never touch the two historical UNCERTAIN CRM ledgers.
    db_file.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(db_file,timeout=5)) as db:
        db.execute("PRAGMA synchronous=FULL")
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS new_cards (
                entity_type_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY(entity_type_id,item_id)
            )""")
            cursor=db.execute(
                "INSERT OR IGNORE INTO new_cards(entity_type_id,item_id,status) VALUES (?,?,?)",
                (TYPE,verified["item_id"],QUEUED))
            new=cursor.rowcount==1
    return {"status":"QUEUED_FOR_SOURCE_CHECK" if new else "DUPLICATE_EVENT_IGNORED",
            "queued":new,"entity_type_id":TYPE,"item_id":verified["item_id"],
            "crm_reads":0,"crm_writes":0,"gigachat_calls":0,
            "qualified_lead_created":False}


def intake(body: bytes, content_type: str, *, token: str, db_file: Path) -> dict:
    event=parse_body(body,content_type)
    verified=parse_event(event,expected_app_token=token)
    if not verified["queued"]:
        return {**verified,"crm_reads":0,"crm_writes":0,"gigachat_calls":0}
    return enqueue_new_item(db_file,verified)


def queue_status(db_file: Path) -> dict:
    if not db_file.is_file():
        return {"status":"QUEUE_NOT_INITIALIZED","pending":0,"crm_writes":0}
    # URI mode=ro doesn't initialize files, does not expose source or tokens.
    with closing(sqlite3.connect(db_file.resolve().as_uri()+"?mode=ro",
                                 uri=True,timeout=5)) as db:
        rows=db.execute("SELECT status,COUNT(*) FROM new_cards GROUP BY status").fetchall()
    _require(all(s in {QUEUED,'RUNNING','HOLD','NEEDS_REVIEW','VERIFIED','UNCERTAIN'}
                 for s,count in rows),"QUEUE_UNEXPECTED_STATE")
    return {"status":"QUEUE_READONLY","pending":sum(count for s,count in rows if s==QUEUED),
            "crm_writes":0}


def configured_queue():
    path=os.environ.get("SERVICEIT_BITRIX_EVENT_QUEUE_DB","")
    _require(bool(path),"QUEUE_NOT_CONFIGURED")
    db_path=Path(path)
    _require(db_path.is_absolute() and db_path.suffix==".sqlite3",
             "QUEUE_PATH_INVALID")
    return db_path
