#!/usr/bin/env python3
"""V26 explicit owner-only PG-only closure of superseded B2B AI_SUMMARY fence.

An old single-field AI_REPAIR_SUMMARY_V1 WRITE_INTENT is NOT replayed or
called VERIFIED. A later signed V11 nine-field receipt, including its
salted digest, MUST exactly match TWO fresh Bitrix readbacks. Only then may
the obsolete historical one-field intent be marked NEEDS_REVIEW. This
preserves its original salt/digest and keeps historical evidence visible.
NO CRM, email, score, HOLD, task, contacts or stages may be written.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,os,re,secrets,sys
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,exact_environment,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import select_one,REPORT
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX,AI_FIELDS
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_vps_native_fence import digested
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import LOCK_ID,compute_digest
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.serviceit_enrichment_worker import (
    Rest,normalized,validate_identity,fingerprint,
)
from leadpilot.bitrix_metadata_audit import validate_webhook

FILES=("serviceit_vps_native_fence.py","serviceit_vps_b2b_dossier_v11_worker.py",
       "serviceit_b2b_v19_backfill.py","serviceit_vps_worker.py")
NS="AI_REPAIR_SUMMARY_V1"
KEY=PREFIX+"AI_SUMMARY"
TARGET="NEEDS_REVIEW"

class Blocked(ValueError):pass

def need(ok,reason):
    if not ok:raise Blocked(reason)

def emit(name,value):
    need(bool(re.fullmatch(r"[A-Z0-9_]{3,75}",name)),"BAD_OUTPUT_NAME")
    need(value in ("YES","NO","PREVIEW","APPLY",TARGET),"BAD_OUTPUT_VALUE")
    print("V26_"+name+"="+value,flush=True)

def preflight(conn,rest,card,source,notice):
    with conn.cursor() as cur:
        cur.execute("""
          SELECT status,field_names,digest_salt,expected_digest,updated_at,
                 now()-updated_at > interval '60 minutes'
          FROM serviceit_vps_native_receipts
          WHERE namespace=%s AND card_id=%s
        """,(NS,card))
        old=cur.fetchone()
        need(old is not None,"LEGACY_RECEIPT_MISSING")
        state,fields,salt,digest,stamp,aged=old
        need(state=="WRITE_INTENT" and fields==[KEY] and aged
             and isinstance(salt,bytes) and len(salt)==16
             and isinstance(digest,bytes) and len(digest)==32,
             "LEGACY_SINGLE_WRITE_INTENT_UNVERIFIED")
        cur.execute("""
          SELECT count(*) FROM serviceit_vps_native_receipts
          WHERE card_id=%s AND namespace<>%s
          AND status IN ('RESERVED','WRITE_INTENT')
        """,(card,NS))
        need(int(cur.fetchone()[0])==0,"OTHER_NATIVE_WRITER_PENDING")
        for table in ("serviceit_vps_b2b_v19_research_receipts",
                      "serviceit_vps_b2b_v20_revision_receipts"):
            cur.execute("SELECT to_regclass(%s)",("public."+table,))
            if cur.fetchone()[0] is None:continue
            cur.execute("SELECT count(*) FROM "+table+
                        " WHERE card_id=%s AND status IN "
                        "('RESERVED','WRITE_INTENT','UNCERTAIN','CONFLICT')",
                        (card,))
            need(int(cur.fetchone()[0])==0,"OTHER_DOSSIER_WRITE_PENDING")
        cur.execute("""
          SELECT status,source_hash,after_fields,salt,digest
          FROM serviceit_vps_b2b_dossier_v11_receipts
          WHERE card_id=%s
        """,(card,))
        later=cur.fetchone()
    need(later is not None,"SIGNED_V11_MISSING")
    status,src_hash,after,salt_v11,digest_v11=later
    expected_src=hashlib.sha256(source.encode("ascii")).hexdigest()
    need(status=="VERIFIED" and src_hash==expected_src
         and type(after) is dict and set(after)==AI_FIELDS
         and isinstance(salt_v11,bytes) and isinstance(digest_v11,bytes)
         and secrets.compare_digest(compute_digest(after,salt_v11),digest_v11),
         "SIGNED_V11_TARGET_DIGEST_INVALID")
    # Two independent CRM GETs, also require ALL non-AI fields stable across
    # both, not merely AI_SUMMARY, before releasing the old single-field gate.
    first=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    second=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    for item in (first,second):
        validate_identity(item,card)
        url,title=safe_notice(notice,card,source,item)
        need(bool(title) and url==notice["source_url"],
             "SIGNED_NOTICE_IDENTITY_CHANGED")
        need(original_nine(item)==after,"NEWER_V11_NOT_LIVE_ANYMORE")
        old_value={KEY:item.get(KEY)}
        need(not secrets.compare_digest(
            digested(old_value,bytes(salt)),bytes(digest)),
            "LEGACY_WRITE_ALREADY_MATCHES")
    need(fingerprint(first)==fingerprint(second),"CRM_CHANGED_DURING_PREFLIGHT")
    return (stamp,salt,digest,fields)

def run(mode,shas):
    need(len(shas)==len(FILES) and
         all(re.fullmatch(r"[a-f0-9]{64}",x) for x in shas),
         "EXPECTED_FOUR_SOURCE_SHAS")
    hashes=[hashlib.sha256((Path("/app/leadpilot")/f).read_bytes()).hexdigest()
            for f in FILES]
    need(hashes==shas,"LIVE_MODULE_SHA_DRIFT")
    env=dict(os.environ)
    need(live_permitted(env) and INTERVAL_SECONDS==300
         and env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED")=="YES"
         and exact_environment(env).get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED")=="YES",
         "V19_V20_AUTOMATION_GATES_MISSING")
    emit("CODE_AND_GATES","YES")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
            locked=bool(cur.fetchone()[0])
        need(locked,"ANOTHER_B2B_WRITER_ACTIVE")
        try:
            records=select_one(conn,preview=True)
            need(len(records)==4,"NOT_EXACT_FOUR_SIGNED")
            card,source,notice=records[0]
            need(type(card) is int and
                 re.fullmatch(r"B2B-CENTER-\d{5,12}",source) and
                 notice["tender_id"]==source.removeprefix("B2B-CENTER-"),
                 "ORIGINAL_SCOPE_CHANGED")
            emit("SIGNED_FIRST","YES")
            with httpx.Client(trust_env=False,follow_redirects=False) as client:
                rest=Rest(webhook,client)
                stamp,salt,digest,fields=preflight(
                    conn,rest,card,source,notice)
            emit("NEWER_V11_SUPERSEDES_OLD","YES")
            if mode=="apply":
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("""
                          UPDATE serviceit_vps_native_receipts
                          SET status='NEEDS_REVIEW',updated_at=now()
                          WHERE namespace=%s AND card_id=%s
                            AND status='WRITE_INTENT'
                            AND field_names=%s
                            AND digest_salt=%s AND expected_digest=%s
                            AND updated_at=%s
                          RETURNING card_id
                        """,(NS,card,fields,salt,digest,stamp))
                        need(cur.fetchone() is not None,
                             "EXACT_LEGACY_RECEIPT_CAS_FAILED")
                with conn.cursor() as cur:
                    cur.execute("""
                      SELECT status,field_names,digest_salt,expected_digest
                      FROM serviceit_vps_native_receipts
                      WHERE namespace=%s AND card_id=%s
                    """,(NS,card))
                    changed=cur.fetchone()
                need(changed==(TARGET,fields,salt,digest),
                     "PG_ONLY_STATUS_READBACK_UNVERIFIED")
                emit("HISTORIC_RECEIPT_STATUS","NEEDS_REVIEW")
                emit("PG_STATUS_UPDATED","YES")
            else:
                emit("PG_STATUS_UPDATED","NO")
            emit("CRM_WRITES","NO")
            emit("MODE","APPLY" if mode=="apply" else "PREVIEW")
            return 0
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))

def main():
    need(len(sys.argv)==2+len(FILES) and sys.argv[1] in (
        "--preview","--apply"),"EXACT_OWNER_MODE_REQUIRED")
    try:
        return run("apply" if sys.argv[1]=="--apply" else "preview",sys.argv[2:])
    except Exception:
        emit("SAFE_ABORT","YES")
        return 2

if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        emit("SAFE_ABORT","YES")
        raise SystemExit(2)
