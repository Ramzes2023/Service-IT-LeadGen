"""Exactly-once preliminary AI HOLD backfill for existing B2B item 7.

Only nine existing AI fields may change. Not a GigaChat call, not a score,
not a proof of buyer demand. Source is pinned to the original user-authored
Issue #66 notice and independently checked against the live CRM card.

No implicit write. A write requires fresh two-pass read, metadata check,
an immutable one-shot local reservation, and explicit operator opt-in.
NEVER retry apply after an uncertain result or reset its ledger.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

import httpx

from .bitrix_metadata_audit import validate_webhook
from .b2b_github_to_bitrix import (
    KEY, SIGNAL, HOLD_STAGE, hold_payload, notice_from_event, BEGIN, REPO, OWNER, ISSUE,
)
from .serviceit_b2b_hold_ai_fields import AI_FIELDS

ENTITY=1062
CATEGORY=21
ITEM_ID=7
SOURCE_ID="B2B-CENTER-4661617"
LEDGER_KEY="b2b:1062:21:7:preliminary-ai-issue66:v1"
FIXTURE=Path(__file__).resolve().parents[1]/"fixtures"/"serviceit_b2b_center_4661617_issue66_original.json"
SCORE="UF_CRM_13_SVCIT_LEAD_SCORE"
BADGE="UF_CRM_13_SVCIT_AI_SCORE_BADGE"
SCORE_VERSION="UF_CRM_13_SVCIT_SCORE_VERSION"


class BackfillBlocked(ValueError):
    pass


def require(ok,code):
    if not ok:
        raise BackfillBlocked(code)


def _notice():
    data=json.loads(FIXTURE.read_text(encoding="utf-8"))
    require(isinstance(data,dict)
            and data.get("source_issue_comment_url") ==
            "https://github.com/Ramzes2023/Service-IT-LeadGen/issues/66#issuecomment-6579109393",
            "ORIGINAL_PROVENANCE_INVALID")
    original=data.get("notice")
    require(isinstance(original,dict),"NOTICE_NOT_AVAILABLE")
    # Reuse exact existing source validation (format only). The synthetic
    # event envelope is NOT a cryptographic signature of the source.
    event={"repository":{"full_name":REPO},"issue":{"number":ISSUE},
           "sender":{"login":OWNER},"comment":{"user":{"login":OWNER},
           "body":BEGIN+json.dumps(original,ensure_ascii=False)},
           "action":"created"}
    notice,groups=notice_from_event(event)
    require(notice["tender_id"]=="4661617"
            and notice["original_publication_verified"] is False
            and notice["qualified_lead"] is False
            and notice["contact_approved"] is False,"NOTICE_WRONG_SCOPE")
    return notice,groups


def _candidate():
    notice,groups=_notice()
    payload=hold_payload(notice,groups)
    fields=payload["fields"]
    ai={name:fields[name] for name in sorted(AI_FIELDS)}
    protected={key:value for key,value in fields.items() if key not in AI_FIELDS}
    require(set(ai)==set(AI_FIELDS) and len(ai)==9,"NINE_FIELD_PAYLOAD_INVALID")
    require(protected[SIGNAL]==SOURCE_ID and protected["stageId"]==HOLD_STAGE,
            "SOURCE_ID_CONFLICT")
    return protected,ai


def _read_item(rest):
    result=rest("crm.item.get",{"entityTypeId":ENTITY,"id":ITEM_ID,
                                 "useOriginalUfNames":"Y"})
    require(isinstance(result,dict) and isinstance(result.get("item"),dict),
            "READBACK_MISSING")
    item=result["item"]
    require(str(item.get("id"))==str(ITEM_ID)
            and str(item.get("entityTypeId"))==str(ENTITY)
            and str(item.get("categoryId"))==str(CATEGORY),
            "LIVE_ITEM_SCOPE_CONFLICT")
    return item


def _verify_snapshot(item,protected):
    for field,expected in protected.items():
        require(item.get(field)==expected,"SOURCE_OR_HOLD_FIELDS_CHANGED")
    for key in AI_FIELDS:
        require(item.get(key) in ("",None),"AI_FIELDS_ALREADY_EXIST_NO_OVERWRITE")
    for key in (SCORE,BADGE,SCORE_VERSION):
        require(item.get(key) in ("",None),"SCORE_OR_BADGE_PRESENT_BLOCK")
    return True


def _read_schema(rest):
    r=rest("crm.item.fields",{"entityTypeId":ENTITY,"useOriginalUfNames":"Y"})
    fields=r.get("fields") if isinstance(r,dict) else None
    require(isinstance(fields,dict),"CRM_METADATA_MISSING")
    for code in AI_FIELDS:
        metadata=fields.get(code)
        require(isinstance(metadata,dict) and metadata.get("type") in ("string","crm_userfield")
                and metadata.get("isReadOnly") is False
                and metadata.get("isMultiple") is False
                and metadata.get("isImmutable") in (None,False),
                "NINE_AI_FIELDS_NOT_WRITABLE")
    return True


def ledger_status(db_file:Path)->str:
    if not db_file.is_file():
        return "NOT_RESERVED"
    with closing(sqlite3.connect(db_file.resolve().as_uri()+"?mode=ro",
                                 uri=True,timeout=5)) as db:
        try:
            row=db.execute("SELECT state FROM backfill_ops WHERE op_key=?",
                           (LEDGER_KEY,)).fetchone()
        except sqlite3.OperationalError:
            raise BackfillBlocked("LEDGER_SCHEMA_UNKNOWN") from None
    require(row is None or row[0] in ("UNCERTAIN","VERIFIED"),
            "LEDGER_CORRUPT")
    return row[0] if row else "NOT_RESERVED"


def _reserve_once(db_file:Path,digest:str):
    require(ledger_status(db_file)=="NOT_RESERVED",
            "BACKFILL_ALREADY_RESERVED_DO_NOT_REPEAT")
    db_file.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(db_file,timeout=5)) as db:
        db.execute("PRAGMA synchronous=FULL")
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS backfill_ops(
                op_key TEXT PRIMARY KEY, state TEXT NOT NULL, digest TEXT NOT NULL
            )""")
            old=db.execute("SELECT state FROM backfill_ops WHERE op_key=?",
                           (LEDGER_KEY,)).fetchone()
            require(old is None,"BACKFILL_ALREADY_RESERVED_DO_NOT_REPEAT")
            db.execute("INSERT INTO backfill_ops VALUES (?,?,?)",
                       (LEDGER_KEY,"UNCERTAIN",digest))


def _mark_verified(db_file:Path):
    with closing(sqlite3.connect(db_file,timeout=5)) as db:
        with db:
            cur=db.execute("UPDATE backfill_ops SET state='VERIFIED' "
                           "WHERE op_key=? AND state='UNCERTAIN'",(LEDGER_KEY,))
            require(cur.rowcount==1,"LEDGER_VERIFICATION_CONFLICT")


def inspect_or_apply(rest,*,db_file:Path,apply=False,approved=False)->dict:
    require(callable(rest) and isinstance(db_file,Path),"INVALID_RUNTIME")
    require(not apply or approved is True,"EXPLICIT_WRITE_AUTHORIZATION_REQUIRED")
    protected,ai=_candidate()
    item=_read_item(rest)
    _verify_snapshot(item,protected)
    _read_schema(rest)
    ledger=ledger_status(db_file)
    if not apply:
        return {
            "status":"B2B_CARD7_PRELIMINARY_AI_READONLY_READY" if ledger=="NOT_RESERVED"
                     else "B2B_CARD7_PRELIMINARY_AI_LEDGER_REVIEW",
            "item_id":ITEM_ID,"signal_id":SOURCE_ID,"field_count":9,
            "provider":"NOT_RUN","score_not_assigned":True,
            "state":ledger,"crm_reads":2,"crm_writes":0,
            "has_verified_original_tender":False,
            "prior_badge_ledgers_untouched":True,
        }
    require(ledger=="NOT_RESERVED","BACKFILL_ALREADY_RESERVED_DO_NOT_REPEAT")
    current=_read_item(rest)
    _verify_snapshot(current,protected)
    # Guard exact before-image of ALL returned CRM fields to prevent writing
    # while another team member changes the card.
    require(item==current,"CRM_ITEM_CHANGED_BETWEEN_READS")
    digest=sha256(json.dumps({"protected":protected,"ai":ai},
                    sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    _reserve_once(db_file,digest)
    # After this line any exception means uncertain. Never automatically retry.
    response=rest("crm.item.update",{"entityTypeId":ENTITY,"id":ITEM_ID,
                         "useOriginalUfNames":"Y","fields":ai})
    require(isinstance(response,dict) and isinstance(response.get("item"),dict),
            "POST_RESULT_UNCERTAIN")
    after=_read_item(rest)
    require(all(after.get(k)==v for k,v in ai.items()),
            "AI_READBACK_UNCERTAIN")
    require(all(after.get(k)==v for k,v in protected.items()),
            "SOURCE_CHANGED_AFTER_POST")
    require(all(after.get(k) in ("",None) for k in (SCORE,BADGE,SCORE_VERSION)),
            "SCORE_CHANGED_AFTER_POST")
    _mark_verified(db_file)
    return {"status":"B2B_CARD7_PRELIMINARY_AI_VERIFIED",
            "item_id":ITEM_ID,"fields_verified":9,"crm_writes":1,
            "provider":"NOT_RUN","score_not_assigned":True,
            "prior_badge_ledgers_untouched":True}


def _transport(secret,client):
    base=validate_webhook(secret)
    counter=[0]
    def request(method,params):
        require(method in {"crm.item.get","crm.item.fields","crm.item.update"},
                "CRM_METHOD_BLOCKED")
        counter[0]+=1
        require(counter[0]<=6,"CRM_REQUEST_BUDGET_EXCEEDED")
        try:
            response=client.post(base+"/"+method+".json",json=params,
                                 timeout=15,follow_redirects=False)
            require(response.status_code==200 and len(response.content)<=512000,
                    "CRM_HTTP_OR_LIMIT_FAILED")
            obj=response.json()
        except (httpx.HTTPError,ValueError,TypeError):
            raise BackfillBlocked("CRM_HTTP_RESULT_UNCERTAIN") from None
        require(isinstance(obj,dict) and "error" not in obj
                and isinstance(obj.get("result"),dict),"CRM_RESPONSE_UNCERTAIN")
        return obj["result"]
    return request


def main(argv=None):
    p=argparse.ArgumentParser(description="One-shot controlled B2B item7 HOLD AI backfill")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    p.add_argument("--apply-once",action="store_true")
    p.add_argument("--approve-exact-item-7",action="store_true")
    args=p.parse_args(argv)
    try:
        require(not (args.apply_once or args.approve_exact_item_7)
                or (args.inspect_live and args.webhook_from_env
                    and args.apply_once and args.approve_exact_item_7),
                "EXACT_APPROVAL_REQUIRED")
        if not args.inspect_live:
            require(not args.webhook_from_env,"LIVE_FLAG_REQUIRED")
            print(json.dumps({"status":"B2B_CARD7_BACKFILL_OFFLINE",
                              "crm_writes":0,"provider":"NOT_RUN"}))
            return 0
        require(args.webhook_from_env,"SECURE_LIVE_FLAG_REQUIRED")
        secret=os.environ.pop("SERVICEIT_B2B_BACKFILL_WEBHOOK","")
        require(bool(secret),"WEBHOOK_MISSING")
        state_root=os.environ.get("LOCALAPPDATA","")
        require(bool(state_root),"STABLE_WINDOWS_STATE_REQUIRED")
        db_file=Path(state_root)/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            report=inspect_or_apply(_transport(secret,client),db_file=db_file,
                                    apply=args.apply_once,
                                    approved=args.approve_exact_item_7)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except (BackfillBlocked,OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        print(json.dumps({"status":"B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN",
                          "crm_writes":"UNCONFIRMED",
                          "no_blind_retry":True,"secrets_in_output":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
