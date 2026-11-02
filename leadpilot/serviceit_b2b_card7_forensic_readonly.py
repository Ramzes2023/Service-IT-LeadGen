"""Forensic READ-ONLY diagnostics for existing B2B-Center item #7.

This deliberately does NOT invoke the ambiguous 78 backfill and does not
permit any crm.item.update; it only diagnoses WHY PR78's previous -InspectLive
failed. No source text, organizer, URL, user data, raw API response or secret
are returned, stored or logged. Field CODES and state-only booleans are safe.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_card7_preliminary_backfill import (
    ENTITY, CATEGORY, ITEM_ID, AI_FIELDS, SCORE, BADGE, SCORE_VERSION,
    _candidate, ledger_status, BackfillBlocked,
)


class ForensicBlocked(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise ForensicBlocked(code)


def _safe_state(item, key):
    if key not in item:
        return "FIELD_NOT_RETURNED"
    value = item[key]
    if value is None:
        return "NULL"
    if value == "":
        return "EMPTY"
    return "POPULATED"


def _field_readiness(meta):
    if not isinstance(meta, dict):
        return {"present":False,"writable":False,
                "legacy_gate_pass":False,"reason":"MISSING_METADATA"}
    valid_type=meta.get("type") in ("string","crm_userfield")
    writable=meta.get("isReadOnly") is False
    immutable=meta.get("isImmutable") in (None,False)
    multiple=meta.get("isMultiple") in (None,False)
    # Previous PR78 required exactly False. PR69 successful schema gate
    # accepted None too. We report this as a discrepancy; do NOT silently
    # relax any live write gate or infer actual API writeability.
    return {
        "present":True,
        "writable":valid_type and writable and immutable and multiple,
        "legacy_gate_pass":valid_type and writable
                            and meta.get("isMultiple") is False and immutable,
        "reason":("TYPE_UNEXPECTED" if not valid_type else
                  "READ_ONLY_OR_UNKNOWN" if not writable else
                  "IMMUTABLE" if not immutable else
                  "MULTIPLE" if not multiple else
                  "MULTIPLICITY_NOT_EXPLICIT_FALSE"
                  if meta.get("isMultiple") is None else "COMPATIBLE"),
    }


def inspect_forensic(rest, *, state_path: Path) -> dict:
    _require(callable(rest) and isinstance(state_path,Path),
             "DIAGNOSTIC_RUNTIME_INVALID")
    # Never call the old inspect_or_apply: it has a write-enabled branch.
    try:
        original, ai = _candidate()
    except (OSError, ValueError, TypeError, KeyError):
        raise ForensicBlocked("PINNED_ISSUE66_FIXTURE_INVALID") from None
    _require(len(ai)==9 and set(ai)==set(AI_FIELDS),
             "PINNED_AI_FIELD_SET_INVALID")
    response=rest("crm.item.get",{
        "entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"})
    _require(isinstance(response,dict) and isinstance(response.get("item"),dict),
             "ITEM_GET_SHAPE_UNEXPECTED")
    item=response["item"]
    _require(str(item.get("id"))==str(ITEM_ID)
             and str(item.get("entityTypeId"))==str(ENTITY)
             and str(item.get("categoryId"))==str(CATEGORY),
             "ITEM7_SCOPE_MISMATCH")
    mismatches=sorted(key for key,expected in original.items()
                      if item.get(key)!=expected)
    missing_protected=sorted(key for key in original if key not in item)
    ai_states={code:_safe_state(item,code) for code in sorted(AI_FIELDS)}
    existing_score={key:_safe_state(item,key)
                    for key in (SCORE,SCORE_VERSION,BADGE)}
    schema=rest("crm.item.fields",{
        "entityTypeId":ENTITY,"useOriginalUfNames":"Y"})
    _require(isinstance(schema,dict) and isinstance(schema.get("fields"),dict),
             "SCHEMA_GET_SHAPE_UNEXPECTED")
    meta=schema["fields"]
    fields={code:_field_readiness(meta.get(code)) for code in sorted(AI_FIELDS)}
    try:
        state=ledger_status(state_path)
    except (BackfillBlocked, OSError, ValueError, sqlite3.Error):
        state="LEDGER_READ_FAILED"
    issues=[]
    if mismatches: issues.append("PROTECTED_FIELDS_NOT_EXACT")
    if any(x=="POPULATED" for x in ai_states.values()):
        issues.append("AI_FIELDS_ALREADY_POPULATED")
    if any(x=="FIELD_NOT_RETURNED" for x in ai_states.values()):
        issues.append("AI_FIELDS_NOT_RETURNED")
    if any(x=="POPULATED" for x in existing_score.values()):
        issues.append("SCORE_OR_BADGE_ALREADY_POPULATED")
    if any(x=="FIELD_NOT_RETURNED" for x in existing_score.values()):
        issues.append("SCORING_FIELDS_NOT_RETURNED")
    if any(not x["legacy_gate_pass"] for x in fields.values()):
        issues.append("AI_SCHEMA_LEGACY_STRICTNESS_OR_MISSING")
    if state!="NOT_RESERVED": issues.append("BACKFILL_LEDGER_REVIEW")
    return {
        "status":"B2B_CARD7_FORENSIC_READONLY_RESULT",
        "item_id":ITEM_ID,
        "scope_verified":True,
        "protected_field_count":len(original),
        "protected_exact":len(mismatches)==0,
        "protected_mismatch_codes":mismatches,
        "protected_not_returned_codes":missing_protected,
        "ai_field_states":ai_states,
        "score_badge_states":existing_score,
        "ai_metadata":fields,
        "backfill_ledger_state":state,
        "issues":issues,
        "prior_pr78_readonly_ready_if_unchanged":not issues,
        "crm_reads":2,"crm_writes":0,"gigachat_calls":0,
        "crm_mutations":0,"ledger_mutations":0,
        "legacy_uncertain_ledgers_untouched":True,
        "live_backfill_authorized":False,
    }


def _read_only_rest(secret, client):
    base=validate_webhook(secret)
    seen=set()
    def read(method, params):
        expected={
            "crm.item.get":{"entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"},
            "crm.item.fields":{"entityTypeId":ENTITY,"useOriginalUfNames":"Y"},
        }
        _require(method in expected and params==expected[method]
                 and method not in seen, "METHOD_OR_PARAMS_NOT_READONLY")
        seen.add(method)
        try:
            reply=client.post(base+"/"+method+".json",json=params,
                              timeout=15,follow_redirects=False)
            _require(reply.status_code==200 and len(reply.content)<=512_000,
                     "READONLY_HTTP_OR_SIZE_BLOCKED")
            result=reply.json()
        except (httpx.HTTPError,ValueError,TypeError):
            raise ForensicBlocked("READONLY_HTTP_OR_JSON_ERROR") from None
        _require(isinstance(result,dict) and "error" not in result
                 and isinstance(result.get("result"),dict),
                 "READONLY_API_RESPONSE_ERROR")
        return result["result"]
    return read


def main(argv=None):
    p=argparse.ArgumentParser(description="Bitrix #7 diagnostic, NO write API")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    args=p.parse_args(argv)
    try:
        _require(args.inspect_live==args.webhook_from_env,
                 "LIVE_FLAGS_MUST_MATCH")
        if not args.inspect_live:
            print(json.dumps({"status":"B2B_CARD7_FORENSIC_OFFLINE",
                              "crm_reads":0,"crm_writes":0}))
            return 0
        secret=os.environ.pop("SERVICEIT_B2B_CARD7_DIAG_WEBHOOK","")
        _require(bool(secret),"SECURE_WEBHOOK_MISSING")
        local=os.environ.get("LOCALAPPDATA","")
        _require(bool(local),"WINDOWS_STATE_PATH_NOT_FOUND")
        state=Path(local)/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            output=inspect_forensic(_read_only_rest(secret,client),
                                    state_path=state)
        print(json.dumps(output,ensure_ascii=False,indent=2))
        return 0
    except (ForensicBlocked, OSError, TypeError, ValueError, KeyError,
            sqlite3.Error):
        print(json.dumps({"status":"B2B_CARD7_FORENSIC_READONLY_BLOCKED",
                          "crm_reads":"AT_MOST_TWO","crm_writes":0,
                          "ledger_mutations":0,"secrets_in_output":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
