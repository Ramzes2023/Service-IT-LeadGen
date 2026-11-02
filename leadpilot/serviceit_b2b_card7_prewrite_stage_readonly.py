"""Diagnose why card #7 PR78 stopped BEFORE its one-shot reservation.

Exactly three allowlisted read-only Bitrix REST operations: GET item #7,
fields schema, GET item #7 again. Never create a DB file, reserve a ledger,
update CRM, invoke GigaChat or retry historical writes. Output only safe
stage codes and property NAMES, never values or secrets.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_card7_preliminary_backfill import (
    _candidate, _read_item, _read_schema, _verify_snapshot,
    ledger_status, BackfillBlocked, ENTITY, ITEM_ID,
    SCORE, BADGE, SCORE_VERSION, AI_FIELDS,
)

class StageDiagnosticBlocked(ValueError):
    pass


def _stage_gate(fn):
    try:
        fn()
    except (BackfillBlocked, ValueError, TypeError, KeyError, OSError,
            sqlite3.Error) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{4,100}",code):
            code="CHECK_FAILED"
        return code
    return "PASS"


def _safe_code(name):
    return name if isinstance(name,str) and re.fullmatch(r"[A-Za-z0-9_]{1,128}",name) else "UNSAFE_FIELD_NAME_REDACTED"


def _path_access(path:Path):
    parent=path.parent
    found=parent
    for _ in range(12):
        if found.exists() or found==found.parent:
            break
        found=found.parent
    return {
        "ledger_file_exists":path.is_file(),
        "ledger_parent_exists":parent.is_dir(),
        "closest_existing_parent_accessible":(
            found.is_dir() and os.access(found,os.R_OK|os.W_OK|os.X_OK)),
        "no_file_created":True,
    }


def diagnostic(rest, *, fixed_ledger:Path) -> dict:
    if not callable(rest) or not isinstance(fixed_ledger,Path):
        raise StageDiagnosticBlocked("DIAGNOSTIC_INPUT_INVALID")
    trace={}
    try:
        protected,expected_ai=_candidate()
    except (OSError, ValueError, TypeError, KeyError):
        raise StageDiagnosticBlocked("PINNED_SOURCE_INVALID") from None
    if set(expected_ai)!=set(AI_FIELDS) or len(expected_ai)!=9:
        raise StageDiagnosticBlocked("PINNED_NINE_FIELDS_INVALID")
    first=_read_item(rest)
    trace["first_snapshot"]=_stage_gate(lambda:_verify_snapshot(first,protected))
    # Metadata and ledger checks mirror PR78's order, without writes.
    trace["schema"]=_stage_gate(lambda:_read_schema(rest))
    state_holder=[]
    def read_ledger():
        state_holder.append(ledger_status(fixed_ledger))
    trace["ledger"]=_stage_gate(read_ledger)
    state=state_holder[0] if state_holder else "READ_ERROR"
    trace["ledger_not_reserved"]= "PASS" if state=="NOT_RESERVED" else "BACKFILL_ALREADY_RESERVED_DO_NOT_REPEAT"
    second=_read_item(rest)
    trace["second_snapshot"]=_stage_gate(lambda:_verify_snapshot(second,protected))
    diff=sorted(set(first)|set(second))
    changed=[_safe_code(k) for k in diff if first.get(k)!=second.get(k)]
    trace["entire_item_equal"]="PASS" if first==second else "CRM_ITEM_CHANGED_BETWEEN_READS"
    trace["pinned_fields_stable"]="PASS" if all(first.get(k)==second.get(k)
                                for k in set(protected)|set(AI_FIELDS)
                                |{SCORE,BADGE,SCORE_VERSION}) else "PINNED_FIELD_CHANGED"
    # Code stops BEFORE reserve: do not call _reserve_once or even create dirs.
    reasons=[f"{key}:{value}" for key,value in trace.items() if value!="PASS"]
    return {
        "status":"B2B_CARD7_PREWRITE_STAGE_READONLY",
        "item_id":ITEM_ID,
        "checks":trace,
        "first_blocking_stage":reasons[0] if reasons else "NONE_IN_READONLY_REPLAY",
        "changing_field_codes":changed,
        "changing_field_count":len(changed),
        "ledger_state":state,
        "filesystem_readiness":_path_access(fixed_ledger),
        "crm_reads":3,"crm_writes":0,"gigachat_calls":0,
        "ledger_mutations":0,"historical_write_retried":False,
        "safe_to_repeat_previous_apply":False,
        "no_write_authorized":True,
    }


def _transport(secret, client):
    base=validate_webhook(secret)
    expected=[
        ("crm.item.get",{"entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"}),
        ("crm.item.fields",{"entityTypeId":ENTITY,"useOriginalUfNames":"Y"}),
        ("crm.item.get",{"entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"}),
    ]
    n=[0]
    def only_read(method, params):
        if n[0]>=len(expected) or (method,params)!=expected[n[0]]:
            raise StageDiagnosticBlocked("METHOD_OR_REQUEST_NOT_ALLOWED")
        n[0]+=1
        try:
            response=client.post(base+"/"+method+".json",json=params,
                                 timeout=15,follow_redirects=False)
            if response.status_code!=200 or len(response.content)>512_000:
                raise StageDiagnosticBlocked("HTTP_READ_BLOCKED")
            obj=response.json()
        except (httpx.HTTPError,ValueError,TypeError):
            raise StageDiagnosticBlocked("READONLY_HTTP_FAILED") from None
        if not isinstance(obj,dict) or "error" in obj or not isinstance(obj.get("result"),dict):
            raise StageDiagnosticBlocked("READONLY_API_ERROR")
        return obj["result"]
    return only_read


def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only stage replay of PR78 before ledger reserve")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.inspect_live != args.webhook_from_env:
            raise StageDiagnosticBlocked("LIVE_FLAGS_MUST_MATCH")
        if not args.inspect_live:
            print(json.dumps({"status":"B2B_CARD7_PREWRITE_STAGE_OFFLINE",
                              "crm_writes":0,"ledger_mutations":0}))
            return 0
        secret=os.environ.pop("SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK","")
        if not secret:
            raise StageDiagnosticBlocked("WEBHOOK_MISSING")
        local=os.environ.get("LOCALAPPDATA","")
        if not local or not Path(local).is_absolute():
            raise StageDiagnosticBlocked("STATE_ROOT_INVALID")
        path=Path(local)/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            out=diagnostic(_transport(secret,client),fixed_ledger=path)
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0
    except (StageDiagnosticBlocked,BackfillBlocked,OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        print(json.dumps({"status":"B2B_CARD7_PREWRITE_STAGE_BLOCKED",
                          "crm_writes":0,"ledger_mutations":0,
                          "secrets_in_output":False,"retry_previous_apply":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
