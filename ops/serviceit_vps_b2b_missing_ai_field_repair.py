"""One-use, exact missing field B2B HOLD repair; no repeated POST or card create.

The 2026-11-10 initial nine-field write created 6/9 values, leaving 3 empty.
Each *different, still-empty* field may be repaired once after an exact live
readback, independently scoped PG WRITE_INTENT, and source/category proof.
This is NOT a retry of the original nine-field update; never blindly replay.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import re
import secrets
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted
from leadpilot.serviceit_vps_native_fence import digested
from leadpilot.serviceit_enrichment_worker import (
    AI_FIELDS, FIELDS, PREFIX, Rest, fingerprint, normalized,
    validate_identity
)
from leadpilot.serviceit_windows_autofill import preliminary_fields, preliminary_schema_limits
from leadpilot.serviceit_bitrix_event_queue_readonly_worker import classify_item
from leadpilot.serviceit_bitrix_poll_fallback import after_activation, date_value


MISSING = (
    ("summary", PREFIX + "AI_SUMMARY", "AI_REPAIR_SUMMARY_V1"),
    ("manager_brief", PREFIX + "AI_MANAGER_BRIEF", "AI_REPAIR_BRIEF_V1"),
    ("full_report", PREFIX + "AI_FULL_REPORT", "AI_REPAIR_REPORT_V1"),
)
ALREADY_PRESENT = {
    PREFIX + "AI_STATUS",
    PREFIX + "AI_HYPOTHESIS",
    PREFIX + "AI_LIMITATIONS",
    PREFIX + "AI_NEXT_ACTION",
    PREFIX + "AI_QUOTE",
    PREFIX + "AI_RECOMMENDATION",
}
ALLOWED_QUEUE_STATUS = "REVIEW"
ALLOWED_NATIVE_STATUS = "WRITE_INTENT"


class Blocked(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Blocked(code)


def source_card(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.card_id,b.source_id
              FROM serviceit_vps_b2b_inbox b
              JOIN serviceit_vps_new_cards q ON q.card_id=b.card_id
              JOIN serviceit_vps_native_receipts r
                ON r.card_id=b.card_id AND r.namespace='AI_NINE_V1'
             WHERE b.state='VERIFIED'
               AND q.status='REVIEW'
               AND q.last_result='EXACT_WORKER_REVIEW'
               AND r.status='WRITE_INTENT'
               AND cardinality(r.field_names)=9
             LIMIT 2
        """)
        rows=cur.fetchall()
    need(len(rows)==1,"EXPECTED_EXACTLY_ONE_PARTIAL_RECEIPT")
    card,source=rows[0]
    need(type(card) is int and 8 <= card <= 2000000000
         and isinstance(source,str)
         and re.fullmatch(r"B2B-CENTER-[0-9]{5,12}",source),
         "UNTRUSTED_PARTIAL_SOURCE")
    return card,source


def previous_complete(conn,card,mode):
    index=next(i for i,row in enumerate(MISSING) if row[0]==mode)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT namespace,status
              FROM serviceit_vps_native_receipts
             WHERE card_id=%s AND namespace IN (%s,%s,%s)
        """,(card,*(row[2] for row in MISSING)))
        found=dict(cur.fetchall())
    for i,(_,_,ns) in enumerate(MISSING):
        if i<index:
            need(found.get(ns)=="VERIFIED","PREVIOUS_MISSING_FIELD_NOT_VERIFIED")
        else:
            need(ns not in found,"THIS_OR_LATER_FIELD_ALREADY_FENCED")


def verify_candidate(rest,card,source,mode,activation,limits):
    obj=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
    })["item"])
    validate_identity(obj,card)
    need(obj.get(PREFIX+"SIGNAL_ID")==source,"B2B_IDENTITY_CHANGED")
    created=date_value(obj.get("createdTime"))
    need(activation<=created<=datetime.now(timezone.utc)+timedelta(minutes=2),
         "HISTORIC_OR_FUTURE_CARD")
    decision=classify_item(obj,requested_id=card)
    need(decision.get("source_kind")=="b2b_center_hold",
         "B2B_SOURCE_CLASSIFICATION_CHANGED")
    need(all(k in obj for k in FIELDS)
         and all(obj[k] in (None,"") for k in FIELDS-AI_FIELDS),
         "SCORE_OR_QUALIFICATION_EXISTS")
    expected=preliminary_fields(obj)
    need(set(expected)==AI_FIELDS and
         all(isinstance(v,str) and 0<len(v)<=limits[k] for k,v in expected.items()),
         "GENERATED_FIELDS_OUT_OF_BOUNDS")
    # All six already-written fields must still equal the ORIGINAL exact
    # unqualified report. A manager edit or source-change blocks us.
    need(all(obj.get(k)==expected[k] for k in ALREADY_PRESENT),
         "ORIGINAL_SIX_FIELDS_CHANGED")
    i=next(i for i,x in enumerate(MISSING) if x[0]==mode)
    for j,(_,key,_) in enumerate(MISSING):
        if j<i:
            need(obj.get(key)==expected[key],"PREVIOUS_FIELD_CONTENT_CHANGED")
        else:
            need(obj.get(key) in (None,""),"TARGET_FIELD_NOT_EMPTY")
    return obj,expected


def fenced_write(conn,card,namespace,key,value):
    salt=secrets.token_bytes(16)
    digest=digested({key:value},salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO serviceit_vps_native_receipts
                    (namespace,card_id,status,field_names,digest_salt,expected_digest)
                VALUES (%s,%s,'WRITE_INTENT',%s,%s,%s)
                ON CONFLICT(namespace,card_id) DO NOTHING
                RETURNING card_id
            """,(namespace,card,[key],salt,digest))
            need(cur.fetchone() is not None,"SINGLE_FIELD_ALREADY_RESERVED")
    return salt,digest


def bitrix_single_field(client,base,card,key,value):
    # Single exact allowlisted AI field. Rest's generic nine-field writer
    # intentionally doesn't support subsets; this one-shot recovery does.
    need(key in {x[1] for x in MISSING}
         and type(card) is int and type(value) is str and bool(value.strip()),
         "SINGLE_FIELD_SCOPE_UNTRUSTED")
    params={"entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
            "fields":{key:value}}
    try:
        resp=client.post(base+"/crm.item.update.json",
                         json=params,follow_redirects=False,timeout=20)
        if resp.status_code!=200 or len(resp.content)>512000:
            return "HTTP_UNVERIFIED"
        body=resp.json()
        if not isinstance(body,dict) or "error" in body:
            return "API_UNVERIFIED"
        return "RESPONSE_RECEIVED"
    except (httpx.HTTPError,ValueError):
        return "HTTP_UNVERIFIED"


def ack_repair(conn,card,namespace,salt,digest):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_native_receipts
                   SET status='VERIFIED',updated_at=now()
                 WHERE namespace=%s AND card_id=%s AND status='WRITE_INTENT'
                   AND digest_salt=%s AND expected_digest=%s
                 RETURNING card_id
            """,(namespace,card,salt,digest))
            need(cur.fetchone() is not None,"SINGLE_FIELD_ACK_NOT_EXACT")


def reconcile_original(conn,card,actual):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT field_names,digest_salt,expected_digest
              FROM serviceit_vps_native_receipts
             WHERE namespace='AI_NINE_V1' AND card_id=%s
               AND status='WRITE_INTENT'
        """,(card,))
        row=cur.fetchone()
    need(row is not None and len(row[0] or [])==9
         and row[1] is not None and row[2] is not None,
         "ORIGINAL_NINE_FIELD_INTENT_UNVERIFIED")
    candidate={key:actual.get(key) for key in row[0]}
    need(digested(candidate,bytes(row[1]))==bytes(row[2]),
         "ORIGINAL_NINE_FIELD_DIGEST_NOT_MATCHED")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_native_receipts
                   SET status='HOLD_9_WRITTEN',updated_at=now()
                 WHERE namespace='AI_NINE_V1' AND card_id=%s
                   AND status='WRITE_INTENT'
                 RETURNING card_id
            """,(card,))
            need(cur.fetchone() is not None,"ORIGINAL_NINE_ACK_LOST")
            cur.execute("""
                UPDATE serviceit_vps_new_cards
                   SET status='DONE',finished_at=now(),
                       last_result='B2B_EXACT_NINE_FIELD_RECONCILED'
                 WHERE card_id=%s AND status='REVIEW'
                   AND last_result='EXACT_WORKER_REVIEW'
                 RETURNING card_id
            """,(card,))
            need(cur.fetchone() is not None,"CORE_QUEUE_DONE_NOT_ACKED")


def main():
    need(len(sys.argv)==2 and sys.argv[1] in {x[0] for x in MISSING},
         "INVALID_MISSING_FIELD_REQUEST")
    mode=sys.argv[1]
    _,field,namespace=next(x for x in MISSING if x[0]==mode)
    env=dict(os.environ)
    need(os.geteuid()!=0 and live_permitted(env),"NATIVE_GATES_OR_USER_NOT_APPROVED")
    start=env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
    need(isinstance(start,str) and start,"NATIVE_ACTIVATION_MISSING")
    active=after_activation({**env,"SERVICEIT_BITRIX_POLL_START_UTC":start},
                            datetime.now(timezone.utc))
    with db(env) as conn:
        card,source=source_card(conn)
        previous_complete(conn,card,mode)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=20) as client:
            rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
            limits=preliminary_schema_limits(rest)
            first,expected=verify_candidate(rest,card,source,mode,active,limits)
            second,_=verify_candidate(rest,card,source,mode,active,limits)
            need(fingerprint(first)==fingerprint(second),
                 "CRM_CHANGED_DURING_PREFLIGHT")
            print("B2B_FIELD_REPAIR_PREFLIGHT=YES",flush=True)
            salt,digest=fenced_write(conn,card,namespace,field,expected[field])
            print("B2B_FIELD_REPAIR_INTENT=YES",flush=True)
            status=bitrix_single_field(client,rest.base,card,field,expected[field])
            print("B2B_FIELD_REPAIR_API_"+status+"=YES",flush=True)
            after=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
            })["item"])
            validate_identity(after,card)
            need(after.get(PREFIX+"SIGNAL_ID")==source
                 and after.get(field)==expected[field],
                 "SINGLE_FIELD_READBACK_UNCERTAIN_NO_RETRY")
            ack_repair(conn,card,namespace,salt,digest)
            print("B2B_FIELD_REPAIR_READBACK=YES",flush=True)
            if mode=="full_report":
                need(all(after.get(k)==v for k,v in expected.items()),
                     "FINAL_NINE_FIELD_EXACT_READBACK_FAILED")
                reconcile_original(conn,card,after)
                print("B2B_FIELD_REPAIR_ORIGINAL_NINE_RECONCILED=YES",flush=True)
            print("B2B_FIELD_REPAIR_DONE=YES",flush=True)


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        code=str(error) if isinstance(error,Blocked) else "UNEXPECTED_FAILURE"
        if re.fullmatch(r"[A-Z0-9_]{3,100}",code) is None:
            code="UNEXPECTED_FAILURE"
        print("B2B_FIELD_REPAIR_BLOCKED_"+code+"=YES",flush=True)
        raise SystemExit(2)
