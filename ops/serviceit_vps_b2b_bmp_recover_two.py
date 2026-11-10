"""One-time exact two-card B2B FULL nine-field BMP-safe recovery.

Observed: both B2B cards exist and are source-verified, the first has 6/9 AI
fields, the second 0/9. Only the three missing fields contained non-BMP emoji;
Bitrix24 silently ignored those even in a single-field REST write. Preserve
ALL text and replace only supplementary-plane decorative characters.

This operation is a *new, independently fenced BMP payload*, NOT a retry of
the previous uncertain 9-field or summary REST calls. Fail closed on any
manager edit, ambiguous result, unexpected prior recovery, or modified source.
No model calls, contact, task, score, source intake or new card creation.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import re
import secrets
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted
from leadpilot.serviceit_vps_native_fence import digested
from leadpilot.serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint, FIELDS, AI_FIELDS, PREFIX,
)
from leadpilot.serviceit_windows_autofill import preliminary_fields, preliminary_schema_limits
from leadpilot.serviceit_bitrix_event_queue_readonly_worker import classify_item
from leadpilot.serviceit_bitrix_poll_fallback import after_activation, date_value

NAMESPACE="AI_BMP_REPAIR_V1"
OLD_NAMESPACE="AI_NINE_V1"
MISSING=frozenset({PREFIX+"AI_SUMMARY",PREFIX+"AI_MANAGER_BRIEF",
                   PREFIX+"AI_FULL_REPORT"})
SIX=AI_FIELDS-MISSING

class Blocked(Exception):
    pass


def need(ok,code):
    if not ok:
        raise Blocked(code)


def rows_to_repair(connection):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT b.card_id,b.source_id,q.status,q.last_result,
                   r.status,cardinality(r.field_names)
              FROM serviceit_vps_b2b_inbox b
              JOIN serviceit_vps_new_cards q ON q.card_id=b.card_id
              JOIN serviceit_vps_native_receipts r
                ON r.namespace='AI_NINE_V1' AND r.card_id=b.card_id
             WHERE b.state='VERIFIED'
             ORDER BY b.received_at,b.source_id LIMIT 3
        """)
        rows=cur.fetchall()
    need(len(rows)==2,"EXACTLY_TWO_VERIFIED_B2B_REQUIRED")
    for index,(card,source,queue,result,original,count) in enumerate(rows):
        need(type(card) is int and card>=8
             and isinstance(source,str)
             and re.fullmatch(r"B2B-CENTER-[0-9]{5,12}",source)
             and queue=="REVIEW"
             and result=="EXACT_WORKER_REVIEW",
             "VERIFIED_B2B_ROW_CHANGED")
        if index==0:
            need(original=="WRITE_INTENT" and count==9,
                 "PARTIAL_WRITE_INTENT_NOT_PRESENT")
        else:
            need(original=="HOLD" and count is None,
                 "SECOND_UNWRITTEN_HOLD_NOT_PRESENT")
    with connection.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM serviceit_vps_native_receipts
             WHERE namespace=%s AND card_id IN (%s,%s)
        """,(NAMESPACE,rows[0][0],rows[1][0]))
        need(cur.fetchone()[0]==0,"BMP_RECOVERY_ALREADY_ATTEMPTED")
    return rows


def check_card(rest, card, source, index, active, limits):
    item=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
    })["item"])
    validate_identity(item,card)
    need(item.get(PREFIX+"SIGNAL_ID")==source,"SOURCE_ID_CHANGED")
    created=date_value(item.get("createdTime"))
    need(active<=created<=datetime.now(timezone.utc)+timedelta(minutes=2),
         "CARD_OUTSIDE_NATIVE_ACTIVATION")
    decision=classify_item(item,requested_id=card)
    need(decision.get("source_kind")=="b2b_center_hold",
         "NO_LONGER_UNQUALIFIED_B2B_HOLD")
    need(all(k in item for k in FIELDS)
         and all(item[k] in (None,"") for k in FIELDS-AI_FIELDS),
         "SCORE_OR_QUALIFICATION_EXISTS")
    generated=preliminary_fields(item)
    need(set(generated)==AI_FIELDS
         and all(isinstance(val,str) and 0<len(val)<=limits[key]
                 and all(ord(ch)<=0xFFFF for ch in val)
                 for key,val in generated.items()),
         "GENERATED_BMP_NINE_FIELDS_INVALID")
    if index==0:
        need(all(item.get(k)==generated[k] for k in SIX)
             and all(item.get(k) in (None,"") for k in MISSING),
             "FIRST_CARD_NO_LONGER_EXACT_SIX_OF_NINE")
        patch={k:generated[k] for k in MISSING}
    else:
        need(all(item.get(k) in (None,"") for k in AI_FIELDS),
             "SECOND_CARD_NOT_EMPTY_AI")
        patch=dict(generated)
    return item,generated,patch


def reserve_bmp(connection,card,patch):
    salt=secrets.token_bytes(16)
    digest=digested(patch,salt)
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                INSERT INTO serviceit_vps_native_receipts
                    (namespace,card_id,status,field_names,digest_salt,expected_digest)
                VALUES (%s,%s,'WRITE_INTENT',%s,%s,%s)
                ON CONFLICT(namespace,card_id) DO NOTHING RETURNING card_id
            """,(NAMESPACE,card,sorted(patch),salt,digest))
            need(cur.fetchone() is not None,"BMP_WRITE_ALREADY_FENCED")
    return salt,digest


def write_exact_bmp(client,base,card,patch):
    need(type(card) is int and 8<=card<=2000000000
         and set(patch) in (MISSING,AI_FIELDS)
         and all(type(v) is str and v and all(ord(x)<=0xFFFF for x in v)
                 for v in patch.values()),
         "UNSAFE_BMP_UPDATE_SCOPE")
    request={"entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
             "fields":patch}
    try:
        response=client.post(base+"/crm.item.update.json",
                             json=request,follow_redirects=False,timeout=22)
        if response.status_code!=200 or len(response.content)>512000:
            return "HTTP_UNVERIFIED"
        body=response.json()
        if not isinstance(body,dict) or "error" in body:
            return "API_UNVERIFIED"
        return "ACK_RECEIVED"
    except (httpx.HTTPError,ValueError):
        return "HTTP_UNVERIFIED"


def exact_observation(rest,card,source,expected):
    after=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
    })["item"])
    validate_identity(after,card)
    need(after.get(PREFIX+"SIGNAL_ID")==source
         and all(after.get(k)==v for k,v in expected.items()),
         "BMP_NINE_READBACK_UNCERTAIN_NO_RETRY")
    return after


def commit_success(connection,card,old_state,salt,digest):
    # Original receipt records a failed non-BMP attempt; do not falsely
    # claim its old salted digest matched. Keep it as NEEDS_REVIEW history.
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_native_receipts
                   SET status='VERIFIED',updated_at=now()
                 WHERE namespace=%s AND card_id=%s AND status='WRITE_INTENT'
                   AND digest_salt=%s AND expected_digest=%s
                 RETURNING card_id
            """,(NAMESPACE,card,salt,digest))
            need(cur.fetchone() is not None,"BMP_RECEIPT_ACK_FAILED")
            cur.execute("""
                UPDATE serviceit_vps_native_receipts
                   SET status='NEEDS_REVIEW',updated_at=now()
                 WHERE namespace='AI_NINE_V1' AND card_id=%s AND status=%s
                 RETURNING card_id
            """,(card,old_state))
            need(cur.fetchone() is not None,"ORIGINAL_RECEIPT_MISMATCH")
            cur.execute("""
                UPDATE serviceit_vps_new_cards
                   SET status='DONE',finished_at=now(),
                       last_result='B2B_UTF8BMP_NINE_FIELDS_EXACT'
                 WHERE card_id=%s AND status='REVIEW'
                   AND last_result='EXACT_WORKER_REVIEW'
                 RETURNING card_id
            """,(card,))
            need(cur.fetchone() is not None,"NATIVE_QUEUE_DONE_MISMATCH")


def main():
    need(len(sys.argv)==1,"NO_EXTERNAL_IDS_OR_MODES")
    env=dict(os.environ)
    need(os.geteuid()!=0 and live_permitted(env),"NATIVE_LIVE_NOT_AUTHORIZED")
    activation=env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
    need(isinstance(activation,str) and activation,"ACTIVATION_MISSING")
    start=after_activation({
        **env,"SERVICEIT_BITRIX_POLL_START_UTC":activation,
    },datetime.now(timezone.utc))
    with db(env) as conn:
        rows=rows_to_repair(conn)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=22) as client:
            rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
            limits=preliminary_schema_limits(rest)
            # Both exact cards are vetted before either PG reservation.
            for index,(card,source,_,_,_,_) in enumerate(rows):
                check_card(rest,card,source,index,start,limits)
            print("B2B_BMP_BOTH_PREFLIGHT=YES",flush=True)
            done=0
            for index,(card,source,_,_,old_status,_) in enumerate(rows):
                before,expected,patch=check_card(rest,card,source,index,start,limits)
                current,_,_=check_card(rest,card,source,index,start,limits)
                need(fingerprint(before)==fingerprint(current),
                     "CRM_EDIT_RACE_DETECTED")
                salt,digest=reserve_bmp(conn,card,patch)
                print("B2B_BMP_CARD_"+str(index+1)+"_INTENT=YES",flush=True)
                code=write_exact_bmp(client,rest.base,card,patch)
                print("B2B_BMP_CARD_"+str(index+1)+"_"+code+"=YES",flush=True)
                exact_observation(rest,card,source,expected)
                commit_success(conn,card,old_status,salt,digest)
                done+=1
                print("B2B_BMP_CARD_"+str(index+1)+"_NINE_FIELDS_VERIFIED=YES",flush=True)
            need(done==2,"TWO_CARDS_NOT_VERIFIED")
            print("B2B_BMP_RECOVERY_COMPLETE=YES",flush=True)


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        code=str(error) if isinstance(error,Blocked) else "UNEXPECTED_FAILURE"
        if re.fullmatch(r"[A-Z0-9_]{3,100}",code) is None:
            code="UNEXPECTED_FAILURE"
        print("B2B_BMP_RECOVERY_BLOCKED_"+code+"=YES",flush=True)
        raise SystemExit(2)
