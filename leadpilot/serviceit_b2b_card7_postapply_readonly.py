"""POST-incident READ-ONLY reconciliation of the ONE-SHOT B2B card #7 backfill.

After PR78 '-ApplyOnce' returned BLOCKED_OR_UNCERTAIN, do not issue a second
update. Read the SAME fixed ledger and the live item in BOTH Bitrix field
name formats; compare all nine actual AI values to pinned original Issue #66
expected values, without emitting any customer data or report text.

This module cannot update CRM, modify a ledger, reset any UNCERTAIN state,
create a card, or call GigaChat. Successful readback != authorization to retry.
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
from .serviceit_b2b_card7_preliminary_backfill import (
    ENTITY, CATEGORY, ITEM_ID, LEDGER_KEY, AI_FIELDS,
    SCORE, BADGE, SCORE_VERSION, _candidate,
)


class ReconcileBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ReconcileBlocked(code)


def fixed_ledger_path(localappdata: str) -> Path:
    require(isinstance(localappdata,str) and bool(localappdata),
            "STABLE_WINDOWS_STATE_REQUIRED")
    root=Path(localappdata)
    require(root.is_absolute(),"WINDOWS_STATE_MUST_BE_ABSOLUTE")
    return root/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"


def _digest(protected, expected_ai) -> str:
    return sha256(json.dumps({"protected":protected,"ai":expected_ai},
                             sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def inspect_ledger(db_file: Path, protected: dict, expected_ai: dict) -> dict:
    """Reads original PR78 ledger in SQLite URI mode=ro, never creates it."""
    require(isinstance(db_file,Path) and db_file.suffix==".sqlite3",
            "LEDGER_PATH_INVALID")
    if not db_file.is_file():
        return {"state":"NOT_RESERVED","expected_digest_matches":None}
    try:
        uri=db_file.resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            row=db.execute("SELECT state,digest FROM backfill_ops WHERE op_key=?",
                           (LEDGER_KEY,)).fetchone()
    except (sqlite3.Error,OSError,ValueError):
        return {"state":"LEDGER_UNREADABLE","expected_digest_matches":False}
    if row is None:
        return {"state":"NO_MATCHING_RESERVATION","expected_digest_matches":None}
    state,digest=row
    if state not in ("UNCERTAIN","VERIFIED") or not isinstance(digest,str):
        return {"state":"LEDGER_INVALID","expected_digest_matches":False}
    return {"state":state,
            "expected_digest_matches":digest==_digest(protected,expected_ai)}


def _norm(code):
    return re.sub(r"[^a-z0-9]","",code.lower())


def _value_state(item,key,expected):
    if not isinstance(key,str) or key not in item:
        return "FIELD_NOT_RETURNED"
    current=item[key]
    if current is None or current=="":
        return "EMPTY"
    if isinstance(current,str) and current==expected:
        return "EXACT_EXPECTED"
    return "DIFFERENT_NONEMPTY"


def _check_item_shape(item):
    require(isinstance(item,dict)
            and str(item.get("id"))==str(ITEM_ID)
            and str(item.get("entityTypeId"))==str(ENTITY)
            and str(item.get("categoryId"))==str(CATEGORY),
            "ITEM_SCOPE_INVALID")


def reconcile_readonly(rest, *, db_file:Path) -> dict:
    require(callable(rest),"REST_NOT_READY")
    # Compute expected values in memory; never print their content.
    try:
        protected,expected_ai=_candidate()
    except (OSError,ValueError,TypeError,KeyError):
        raise ReconcileBlocked("PINNED_SOURCE_INVALID") from None
    require(len(expected_ai)==9 and set(expected_ai)==set(AI_FIELDS),
            "EXPECTED_AI_FIELDS_INVALID")
    ledger=inspect_ledger(db_file,protected,expected_ai)
    original=rest("crm.item.get",{
        "entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"})
    require(isinstance(original,dict) and isinstance(original.get("item"),dict),
            "READ_ONLY_RESULT_SHAPE_INVALID")
    _check_item_shape(original["item"])
    camel=rest("crm.item.get",{
        "entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"N"})
    schema=rest("crm.item.fields",{
        "entityTypeId":ENTITY,"useOriginalUfNames":"N"})
    require(isinstance(original,dict) and isinstance(original.get("item"),dict)
            and isinstance(camel,dict) and isinstance(camel.get("item"),dict)
            and isinstance(schema,dict) and isinstance(schema.get("fields"),dict),
            "READ_ONLY_RESULT_SHAPE_INVALID")
    y=original["item"]
    n=camel["item"]
    _check_item_shape(y)
    _check_item_shape(n)
    norm_lookup={}
    for name in schema["fields"]:
        if isinstance(name,str):
            key=_norm(name)
            require(key not in norm_lookup or norm_lookup[key]==name,
                    "CAMEL_ALIAS_AMBIGUOUS")
            norm_lookup[key]=name
    protected_changed=sorted(k for k,v in protected.items() if y.get(k)!=v)
    fields={}
    for code in sorted(AI_FIELDS):
        aliased=norm_lookup.get(_norm(code))
        fields[code]={
            "original":_value_state(y,code,expected_ai[code]),
            "camel":_value_state(n,aliased,expected_ai[code]),
            "camel_alias_found":bool(aliased),
        }
    score_codes=(SCORE,SCORE_VERSION,BADGE)
    score_states={key:_value_state(y,key,"") for key in score_codes}
    all_y_exact=all(f["original"]=="EXACT_EXPECTED" for f in fields.values())
    all_n_exact=all(f["camel"]=="EXACT_EXPECTED" for f in fields.values())
    none_y_changed=all(f["original"]=="EMPTY" for f in fields.values())
    none_n_changed=all(f["camel"]=="EMPTY" for f in fields.values())
    partial=not (all_y_exact or all_n_exact or (none_y_changed and none_n_changed))
    state=ledger["state"]
    digest_ok=ledger["expected_digest_matches"]
    if state in ("LEDGER_UNREADABLE","LEDGER_INVALID"):
        decision="LEDGER_FORENSIC_REQUIRED"
    elif state in ("UNCERTAIN","VERIFIED") and digest_ok is not True:
        decision="LEDGER_DIGEST_MISMATCH_BLOCKED"
    elif all_y_exact and all_n_exact:
        decision=("LIVE_NINE_FIELDS_MATCH_LEDGER_UNCERTAIN" if state=="UNCERTAIN"
                  else "LIVE_NINE_FIELDS_MATCH_VERIFIED_LEDGER" if state=="VERIFIED"
                  else "LIVE_NINE_FIELDS_MATCH_WITHOUT_RESERVATION")
    elif none_y_changed and none_n_changed:
        decision=("LIVE_NINE_FIELDS_EMPTY_LEDGER_UNCERTAIN" if state=="UNCERTAIN"
                  else "LIVE_NINE_FIELDS_EMPTY_NO_RESERVATION" if
                  state in ("NOT_RESERVED","NO_MATCHING_RESERVATION")
                  else "LIVE_EMPTY_CONTRADICTS_VERIFIED_LEDGER")
    else:
        decision="PARTIAL_OR_ALIAS_CONFLICT_READONLY_REVIEW"
    return {
        "status":"B2B_CARD7_POSTAPPLY_READONLY_RECONCILIATION",
        "item_id":ITEM_ID,
        "scope_verified":True,
        "ledger":ledger,
        "decision":decision,
        "nine_fields_checked":len(fields),
        "nine_exact_original":sum(f["original"]=="EXACT_EXPECTED" for f in fields.values()),
        "nine_exact_camel":sum(f["camel"]=="EXACT_EXPECTED" for f in fields.values()),
        "nine_empty_original":sum(f["original"]=="EMPTY" for f in fields.values()),
        "nine_empty_camel":sum(f["camel"]=="EMPTY" for f in fields.values()),
        "field_readbacks":fields,
        "protected_mismatch_codes":protected_changed,
        "score_version_badge_states":score_states,
        "all_nine_match_both_modes":all_y_exact and all_n_exact,
        "partial_or_alias_disagreement":partial,
        "crm_reads":3,"crm_writes":0,"gigachat_calls":0,
        "ledger_mutations":0,"backfill_post_retried":False,
        "safe_to_repeat_previous_apply":False,
        "live_writer_authorized":False,
        "historical_badge_ledgers_untouched":True,
    }


def _transport(secret,client):
    base=validate_webhook(secret)
    calls=set()
    expected={
        ("crm.item.get","Y"):{
            "entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"Y"},
        ("crm.item.get","N"):{
            "entityTypeId":ENTITY,"id":ITEM_ID,"useOriginalUfNames":"N"},
        ("crm.item.fields","N"):{
            "entityTypeId":ENTITY,"useOriginalUfNames":"N"},
    }
    def read(method,params):
        mode=params.get("useOriginalUfNames")
        key=(method,mode)
        require(key in expected and params==expected[key] and key not in calls,
                "ONLY_EXACT_ITEM_READ_ALLOWED")
        calls.add(key)
        try:
            response=client.post(base+"/"+method+".json",json=params,
                                 timeout=15,follow_redirects=False)
            require(response.status_code==200 and len(response.content)<=512000,
                    "READONLY_HTTP_BLOCKED")
            result=response.json()
        except (httpx.HTTPError,TypeError,ValueError):
            raise ReconcileBlocked("READONLY_HTTP_OR_JSON_FAILED") from None
        require(isinstance(result,dict) and "error" not in result
                and isinstance(result.get("result"),dict),
                "READONLY_API_RESULT_INVALID")
        return result["result"]
    return read


def main(argv=None):
    p=argparse.ArgumentParser(description="Exact item #7 GET + unchanged ledger reconciliation")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    args=p.parse_args(argv)
    try:
        require(args.inspect_live==args.webhook_from_env,
                "LIVE_FLAGS_MISMATCH")
        if not args.inspect_live:
            print(json.dumps({"status":"B2B_CARD7_POSTAPPLY_OFFLINE",
                              "crm_reads":0,"crm_writes":0,
                              "backfill_post_retried":False}))
            return 0
        secret=os.environ.pop("SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK","")
        require(bool(secret),"WEBHOOK_NOT_CONFIGURED")
        fixed=fixed_ledger_path(os.environ.get("LOCALAPPDATA",""))
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            out=reconcile_readonly(_transport(secret,client),db_file=fixed)
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0
    except (ReconcileBlocked,OSError,KeyError,TypeError,ValueError,sqlite3.Error):
        print(json.dumps({
            "status":"B2B_CARD7_POSTAPPLY_READONLY_BLOCKED",
            "crm_reads":"MAX_3","crm_writes":0,
            "ledger_mutations":0,"secrets_in_output":False,
            "safe_to_repeat_previous_apply":False,
        }))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
