"""Read-only consumer of Bitrix24 1062 new-card event IDs.

Connects PR72's durable event queue to exact crm.item.get, validates that the
item belongs to ServiceIT category 21, and produces fail-closed eligibility
decisions for a future AI enrichment worker. No AI, score changes, outbox
claiming, updates, contacts, stage moves or task creation.

A B2B-Center supplier email is a HOLD tender opportunity, NOT a lead; the
manager can review its nine preliminary AI fields but must not get a
fabricated 90/100, FNS or confirmation of an actual procurement.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_new_card_events import TYPE, QUEUED, MAX_ITEM_ID

CATEGORY=21
MAX_BATCH=10
SIGNAL_ID="UF_CRM_13_SVCIT_SIGNAL_ID"
SOURCE="UF_CRM_13_SVCIT_SIGNAL_SOURCE"
KIND="UF_CRM_13_SVCIT_SIGNAL_KIND"
STOP="UF_CRM_13_SVCIT_STOP_REASON"
EVIDENCE="UF_CRM_13_SVCIT_EVIDENCE_STATUS"
INN="UF_CRM_13_SVCIT_COMPANY_INN"
URL="UF_CRM_13_SVCIT_SIGNAL_URL"
AI_STATUS="UF_CRM_13_SVCIT_AI_STATUS"
AI_REPORT="UF_CRM_13_SVCIT_AI_FULL_REPORT"
SCORE="UF_CRM_13_SVCIT_LEAD_SCORE"
BADGE="UF_CRM_13_SVCIT_AI_SCORE_BADGE"
DEMO_ID="DEMO-ITREQ-001"


class ReadOnlyWorkerBlocked(ValueError):
    pass


def must(ok: bool, code: str):
    if not ok:
        raise ReadOnlyWorkerBlocked(code)


def pending_ids(queue: Path, limit: int=MAX_BATCH) -> list[int]:
    must(isinstance(queue,Path) and queue.is_file()
         and type(limit) is int and 1<=limit<=MAX_BATCH,
         "QUEUE_NOT_AVAILABLE_OR_LIMIT_INVALID")
    # SQLite read-only mode: do not claim, delete or even update a queue row.
    with closing(sqlite3.connect(queue.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
        rows=db.execute(
            "SELECT entity_type_id,item_id,status FROM new_cards "
            "WHERE status='PENDING_SOURCE_CHECK' "
            "ORDER BY item_id ASC LIMIT ?",(limit,)).fetchall()
    ids=[]
    for typeid,item,status in rows:
        must(type(typeid) is int and typeid==TYPE
             and type(item) is int and 0<item<=MAX_ITEM_ID
             and status==QUEUED,"QUEUE_ROW_UNTRUSTED")
        ids.append(item)
    must(len(set(ids))==len(ids),"QUEUE_DUPLICATE_INVALID")
    return ids


def _text_or_empty(val,maximum=2000):
    return val is None or (type(val) is str and len(val)<=maximum)


def classify_item(item: dict, *, requested_id: int) -> dict:
    """No qualification inferred from absent CRM data or external email."""
    must(isinstance(item,dict)
         and type(requested_id) is int and 0<requested_id<=MAX_ITEM_ID,
         "CRM_ITEM_INVALID")
    must(str(item.get("id"))==str(requested_id)
         and str(item.get("entityTypeId"))==str(TYPE),
         "CRM_ITEM_IDENTITY_CONFLICT")
    if str(item.get("categoryId"))!=str(CATEGORY):
        return {"item_id":requested_id,"decision":"OUTSIDE_SERVICEIT_CATEGORY",
                "source_kind":"out_of_scope","analysis_allowed":False}
    signal=item.get(SIGNAL_ID)
    kind=item.get(KIND)
    source=item.get(SOURCE)
    stop=item.get(STOP)
    ai_status=item.get(AI_STATUS)
    report=item.get(AI_REPORT)
    score=item.get(SCORE)
    badge=item.get(BADGE)
    if not all(_text_or_empty(val) for val in
               (signal,kind,source,stop,ai_status,report)):
        raise ReadOnlyWorkerBlocked("CRM_SOURCE_FIELD_TYPES_INVALID")
    if score is not None and score!="" and (type(score) is not int or not 0<=score<=100):
        raise ReadOnlyWorkerBlocked("CRM_EXISTING_SCORE_TYPE_INVALID")
    if badge not in (None,"") and not _text_or_empty(badge,200):
        raise ReadOnlyWorkerBlocked("CRM_BADGE_FIELD_TYPE_INVALID")
    signal=signal or ""
    kind=kind or ""
    source=source or ""
    stop=stop or ""
    ai_status=ai_status or ""
    report=report or ""
    source_hash=(sha256(signal.encode()).hexdigest()[:16] if signal else "")
    result={"item_id":requested_id,"source_fingerprint":source_hash,
            "source_kind":"unknown","decision":"SOURCE_CONTRACT_REVIEW",
            "analysis_allowed":False,"gigachat_requested":False,
            "crm_writes":0,"manual_contact_authorized":False}
    if (signal==DEMO_ID or kind=="demo-synthetic" or source=="synthetic-fixture"):
        return {**result,"source_kind":"synthetic","decision":"EXCLUDED_SYNTHETIC_DEMO"}
    if kind=="tender-opportunity" and source=="B2B-Center email subscription":
        must(re.fullmatch(r"B2B-CENTER-[0-9]{5,12}",signal) is not None,
             "B2B_SOURCE_ID_CONFLICT")
        evidence=item.get(EVIDENCE)
        must(_text_or_empty(evidence,2000),"CRM_EVIDENCE_FIELD_INVALID")
        must(stop.strip() and ("HOLD" in (evidence or "").upper()
             or "не" in stop.lower()),"B2B_HOLD_GUARD_MISSING")
        # The original source is unverified; even genuine GigaChat on email
        # subject is only an unverified HYPOTHESIS. No Score before buyer checks.
        if score not in (None,"") or badge not in (None,""):
            return {**result,"source_kind":"b2b_center_hold",
                    "decision":"HOLD_WITH_UNEXPECTED_SCORE_REVIEW"}
        if ai_status and report:
            return {**result,"source_kind":"b2b_center_hold",
                    "decision":"HOLD_PRELIMINARY_AI_ALREADY_PRESENT"}
        return {**result,"source_kind":"b2b_center_hold",
                "decision":"HOLD_MISSING_PRELIMINARY_AI"}
    if kind=="tender-opportunity":
        return {**result,"source_kind":"other_tender",
                "decision":"TENDER_SOURCE_CONTRACT_UNVERIFIED"}
    if score not in (None,"") or ai_status or report or badge not in (None,""):
        return {**result,"source_kind":"unknown",
                "decision":"EXISTING_ANALYSIS_REQUIRES_REVIEW"}
    return result


def examine_ids(ids: list[int], fetch) -> dict:
    """Read only exact item IDs; fetch method injected and allowlisted."""
    must(isinstance(ids,list) and len(ids)<=MAX_BATCH
         and all(type(i) is int and 0<i<=MAX_ITEM_ID for i in ids)
         and len(set(ids))==len(ids) and callable(fetch),
         "ITEM_BATCH_INVALID")
    items=[]
    for item_id in ids:
        body=fetch("crm.item.get",{
            "entityTypeId":TYPE,"id":item_id,"useOriginalUfNames":"Y"})
        must(isinstance(body,dict) and isinstance(body.get("item"),dict),
             "CRM_ITEM_READBACK_UNTRUSTED")
        items.append(classify_item(body["item"],requested_id=item_id))
    return {"status":"SERVICEIT_EVENT_QUEUE_CRM_READONLY",
            "inspected":len(items),
            "items":items,
            "crm_reads":len(ids),"crm_writes":0,
            "gigachat_calls":0,"production_writer_enabled":False,
            "queue_claims":0,"already_existing_badge_ledgers_touched":False}


def examine_queue(queue: Path, fetch,limit:int=MAX_BATCH)->dict:
    return examine_ids(pending_ids(queue,limit),fetch)


def _rest_reader(webhook: str, client: httpx.Client):
    base=validate_webhook(webhook)
    calls=[0]
    def read(method,params):
        must(method=="crm.item.get" and params.get("entityTypeId")==TYPE
             and params.get("useOriginalUfNames")=="Y"
             and type(params.get("id")) is int
             and 0<params["id"]<=MAX_ITEM_ID, "ONLY_EXACT_ITEM_READ_ALLOWED")
        calls[0]+=1
        must(calls[0]<=MAX_BATCH,"CRM_READ_BUDGET_EXCEEDED")
        try:
            response=client.post(base+"/crm.item.get.json",json=params,
                                 follow_redirects=False,timeout=12)
            must(response.status_code==200 and len(response.content)<=262144,
                 "CRM_READ_HTTP_BLOCKED")
            obj=response.json()
        except (httpx.HTTPError,ValueError,TypeError):
            raise ReadOnlyWorkerBlocked("CRM_READ_FAILED") from None
        must(isinstance(obj,dict) and "error" not in obj
             and isinstance(obj.get("result"),dict),
             "CRM_READ_RESPONSE_BLOCKED")
        return obj["result"]
    return read


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read queued 1062 cards only, never write")
    parser.add_argument("--inspect-live",action="store_true")
    parser.add_argument("--webhook-from-env",action="store_true")
    parser.add_argument("--queue-db",type=Path)
    parser.add_argument("--item-id",type=int,default=0)
    parser.add_argument("--limit",type=int,default=MAX_BATCH)
    args=parser.parse_args(argv)
    try:
        must(not (args.webhook_from_env and not args.inspect_live),
             "LIVE_FLAG_REQUIRED")
        must(type(args.limit) is int and 1<=args.limit<=MAX_BATCH,"BAD_LIMIT")
        if not args.inspect_live:
            print(json.dumps({"status":"SERVICEIT_EVENT_QUEUE_WORKER_OFFLINE",
                 "crm_reads":0,"crm_writes":0,"gigachat_calls":0,
                 "production_writer_enabled":False},ensure_ascii=False))
            return 0
        must(args.webhook_from_env and (args.queue_db is not None) != (args.item_id > 0),
             "LIVE_READONLY_EXACTLY_ONE_SOURCE_REQUIRED")
        must(type(args.item_id) is int and 0<=args.item_id<=MAX_ITEM_ID,
             "EXACT_ITEM_ID_INVALID")
        secret=os.environ.pop("SERVICEIT_EVENT_READONLY_WEBHOOK","")
        must(bool(secret),"LIVE_WEBHOOK_MISSING")
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            reader=_rest_reader(secret,client)
            result=(examine_ids([args.item_id],reader) if args.item_id
                    else examine_queue(args.queue_db,reader,args.limit))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ReadOnlyWorkerBlocked,OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        print(json.dumps({"status":"SERVICEIT_EVENT_QUEUE_READONLY_BLOCKED",
            "crm_writes":0,"gigachat_calls":0,
            "secrets_in_output":False,"manual_review_required":True}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
