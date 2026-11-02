"""Explicit, one-time reconciliation of an ignored Kanban badge field update.

This is NOT a retry of the prior fenced command. The original UNCERTAIN key is
retained and re-verified; a different bounded repair intent needs explicit
operator approval and a fresh empty value in BOTH Bitrix name formats.
No updates to score, GigaChat, stage, title, task or other CRM records.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re

import httpx

from .bitrix_1062_ai_cockpit import CockpitBlocked, ITEM_SCOPE, Rest, State, require
from .bitrix_1062_mapping import FIELD_MAP
from .serviceit_kanban_score_badge import CODE, VERSION, score_badge
from .serviceit_kanban_badge_recovery import ledger_readonly

REPAIR_KEY = "badge:1062:21:3:reconciliation-v1"
NEW_SCOPE = {"entityTypeId":1062,"id":3,"useOriginalUfNames":"N"}
OLD_SCOPE = deepcopy(ITEM_SCOPE)
OLD_FIELD_KEY = "badge:1062:13:field:v1"
OLD_ITEM_KEY = "badge:1062:21:3:v1"


def normalized(key):
    return re.sub("[^a-z0-9]", "", str(key).lower())


def find_field_key(fields):
    if not isinstance(fields, dict):
        raise CockpitBlocked("REPAIR_METADATA_INVALID")
    choices = [(k,v) for k,v in fields.items() if normalized(k) == normalized(CODE)]
    require(len(choices)==1,"REPAIR_FIELD_ALIAS_MISSING_OR_AMBIGUOUS")
    key, meta=choices[0]
    require(isinstance(meta,dict) and meta.get("type") in ("string","crm_userfield")
            and meta.get("isReadOnly") is False
            and meta.get("isImmutable",False) is False
            and meta.get("isMultiple",False) is False,
            "REPAIR_FIELD_NOT_WRITABLE")
    return key


def badge_value(item):
    require(isinstance(item,dict),"REPAIR_ITEM_INVALID")
    keys=[k for k in item if normalized(k)==normalized(CODE)]
    require(len(keys)==1,"REPAIR_ITEM_FIELD_AMBIGUOUS")
    return item[keys[0]]


def get_card(rest,scope):
    result=rest("crm.item.get",deepcopy(scope))
    require(isinstance(result,dict) and isinstance(result.get("item"),dict),"REPAIR_ITEM_INVALID")
    return result["item"]


def verify_identity(item):
    require(str(item.get("id"))=="3" and str(item.get("entityTypeId"))=="1062"
            and str(item.get("categoryId"))=="21"
            and item.get("title")=="DEMO ServiceIT AI — DEMO-ITREQ-001",
            "REPAIR_NOT_EXACT_DEMO")
    require(str(item.get(FIELD_MAP["lead_score"]))=="90"
            and item.get(FIELD_MAP["score_version"])==VERSION,
            "REPAIR_SCORE_CHANGED")
    require(isinstance(item.get("UF_CRM_13_SVCIT_AI_FULL_REPORT"),str)
            and len(item["UF_CRM_13_SVCIT_AI_FULL_REPORT"])>=100,
            "REPAIR_REPORT_MISSING")


def state_gate(directory):
    require(bool(directory),"REPAIR_ORIGINAL_STATE_DIR_REQUIRED")
    path=Path(directory)/"cockpit-report-v1.sqlite3"
    require(path.is_file(),"REPAIR_ORIGINAL_LEDGER_NOT_FOUND")
    ledger=ledger_readonly(directory)
    ops=ledger["items"]
    require(ops.get(OLD_FIELD_KEY)=="VERIFIED"
            and ops.get(OLD_ITEM_KEY)=="UNCERTAIN",
            "REPAIR_ORIGINAL_STATE_NOT_EXPECTED")
    return ledger


def snapshot(rest,directory):
    ledger=state_gate(directory)
    item_y=get_card(rest,OLD_SCOPE)
    item_n=get_card(rest,NEW_SCOPE)
    verify_identity(item_y)
    require(str(item_n.get("id"))=="3","REPAIR_ALTERNATE_ID_MISMATCH")
    require(badge_value(item_y) in (None,"") and badge_value(item_n) in (None,""),
            "REPAIR_FIELD_ALREADY_NONEMPTY")
    desc=rest("crm.item.fields",{"entityTypeId":1062,"useOriginalUfNames":"N"})
    require(isinstance(desc,dict),"REPAIR_METADATA_INVALID")
    key=find_field_key(desc.get("fields"))
    # No schema changes, category changes, stage transitions or other field updates.
    return {"ledger":ledger,"item_y":item_y,"item_n":item_n,
            "field_key":key, "expected":score_badge(90)}


def apply_reconciliation(rest,directory):
    initial=snapshot(rest,directory)
    # Assert that this is a *new*, explicitly authorized reconciliation, not a
    # second invocation of the original item key. The prior UNCERTAIN persists.
    state=State(directory)
    again=snapshot(rest,directory)
    require(initial["item_y"]==again["item_y"]
            and initial["item_n"]==again["item_n"]
            and initial["field_key"]==again["field_key"],
            "REPAIR_CONCURRENT_CHANGE")
    payload={"entityTypeId":1062,"id":3,"useOriginalUfNames":"N",
             "fields":{again["field_key"]:again["expected"]}}
    # Snapshot only relevant state metadata; original report is not copied.
    state.backup("badge-reconciliation-before",{
        "item_id":3,"field":CODE,"before":"EMPTY",
        "expected_sha256":sha256(again["expected"].encode()).hexdigest(),
        "original_ledger_status":"UNCERTAIN",
        "write_field_name":again["field_key"]})
    state.reserve(REPAIR_KEY,payload)
    # Intentionally one POST; no retry on 401, timeout, silent write ignore, or
    # ambiguous response. Keep REPAIR_KEY UNCERTAIN in those cases.
    result=rest("crm.item.update",payload)
    require(isinstance(result,dict),"REPAIR_WRITE_RESPONSE_UNCERTAIN")
    after_y=get_card(rest,OLD_SCOPE)
    after_n=get_card(rest,NEW_SCOPE)
    require(badge_value(after_y)==again["expected"]
            and badge_value(after_n)==again["expected"],
            "REPAIR_READBACK_UNCERTAIN_NO_RETRY")
    require(all(after_y.get(k)==v for k,v in again["item_y"].items()
                if normalized(k)!=normalized(CODE)
                and k not in {"updatedTime","updatedBy","movedTime"}),
            "REPAIR_PROTECTED_FIELDS_CHANGED")
    # Old uncertain reservation is intentionally *never changed*.
    state.verified(REPAIR_KEY)
    return {"status":"BADGE_RECONCILIATION_VERIFIED",
            "badge":again["expected"],"score":90,
            "crm_item":3,"crm_writes":1,
            "original_reservation_preserved":"UNCERTAIN",
            "kanban_display_configured":False}


def main(argv=None):
    p=argparse.ArgumentParser(description="Bounded Bitrix score badge repair")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--apply-reconciliation",action="store_true")
    p.add_argument("--approve-one-reconciliation",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    p.add_argument("--state-dir",required=True)
    args=p.parse_args(argv)
    if not args.inspect_live:
        print(json.dumps({"status":"BADGE_RECONCILIATION_OFFLINE_READY",
                          "crm_writes":0,"original_ledger_unchanged":True}))
        return 0
    try:
        require(args.webhook_from_env,"REPAIR_SECURE_INPUT_REQUIRED")
        hook=os.environ.pop("SERVICEIT_AI_COCKPIT_WEBHOOK","")
        require(bool(hook),"REPAIR_WEBHOOK_MISSING")
        require(not args.apply_reconciliation or args.approve_one_reconciliation,
                "REPAIR_EXPLICIT_APPROVAL_REQUIRED")
        with httpx.Client() as client:
            rest=Rest(hook,client)
            if args.apply_reconciliation:
                output=apply_reconciliation(rest,args.state_dir)
            else:
                pre=snapshot(rest,args.state_dir)
                output={"status":"BADGE_RECONCILIATION_READONLY_READY",
                        "score":90,"badge_expected":pre["expected"],
                        "field_name_mode":"N_FROM_LIVE_METADATA",
                        "original_reservation":pre["ledger"]["items"][OLD_ITEM_KEY],
                        "schema_reservation":pre["ledger"]["items"][OLD_FIELD_KEY],
                        "requires_separate_write_opt_in":True,"crm_writes":0}
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return 0
    except (CockpitBlocked,KeyError,ValueError,TypeError,OSError,httpx.HTTPError):
        print(json.dumps({"status":"BADGE_RECONCILIATION_BLOCKED_OR_UNCERTAIN",
                          "no_blind_retry":True,"secrets_in_output":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
