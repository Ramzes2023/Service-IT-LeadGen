"""Versioned, source-attested V37 passport/CRM consistency worker.

No new lead, no outreach, no task, stage, score or HOLD change. One signed
card per 300-second coordinator cycle. Uses dedicated PostgreSQL receipts;
external Bitrix write is durable-intent fenced and independently read back.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_dossier_v11 import original_nine
from .serviceit_b2b_dossier_consistency_v37 import (
    prepare, PROVEN_QUOTE, REPORT, ReconcileBlocked,
)
from .serviceit_b2b_v19_backfill import active_card_write
from .serviceit_enrichment_worker import Rest, normalized, validate_identity, fingerprint
from .serviceit_vps_b2b_deep_research import safe_notice
from .serviceit_vps_b2b_dossier_v11_worker import LOCK_ID
from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import live_permitted, exact_environment

ENABLED = "SERVICEIT_VPS_B2B_DOSSIER_CONSISTENCY_ENABLED"
TABLE = "serviceit_vps_b2b_dossier_consistency_v37_receipts"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_dossier_consistency_v37_receipts (
 card_id BIGINT NOT NULL CHECK(card_id BETWEEN 8 AND 2000000000),
 attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 1000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN (
   'RESERVED','WRITE_INTENT','VERIFIED','NO_ATTESTED_NEW_FACTS',
   'INSUFFICIENT_CAPACITY','NONSTANDARD_REPORT','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(card_id,attempt_no),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""
RESULT = frozenset(("VERIFIED","NO_ATTESTED_NEW_FACTS","INSUFFICIENT_CAPACITY",
                    "NONSTANDARD_REPORT","CONFLICT","UNCERTAIN"))
# This is a standalone, explicitly audited subset update contract. The
# universal Rest deliberately permits only complete nine/twelve-field
# bundles, and must NOT be relaxed to support V41.
ALLOWED_PATCH = AI_FIELDS | {
    PREFIX+"COMPANY_INN", PREFIX+"COMPANY_NAME", PREFIX+"REGION"
}
MAX_CRM_REPORT=12000
MAX_CRM_OTHER=4096

class V37Blocked(ValueError):
    pass


def need(value, name):
    if not value:
        raise V37Blocked(name)


def verify_patch_schema(rest,patch):
    """Fail-closed against the real live Bitrix 1062 field definitions."""
    need(isinstance(patch,dict) and 0<len(patch)<=len(ALLOWED_PATCH)
         and set(patch)<=ALLOWED_PATCH
         and all(isinstance(value,str) and value.strip()
                 and len(value)<=(
                    MAX_CRM_REPORT if key==REPORT else MAX_CRM_OTHER)
                 for key,value in patch.items()),
         "V41_SCOPED_UPDATE_FIELDS_INVALID")
    definitions=rest("crm.item.fields",{
        "entityTypeId":1062,"useOriginalUfNames":"Y"})["fields"]
    need(isinstance(definitions,dict),"V41_SCHEMA_READ_FAILED")
    for key in patch:
        detail=definitions.get(key)
        need(isinstance(detail,dict)
             and detail.get("type")=="string"
             and detail.get("isReadOnly") is False
             and detail.get("isImmutable") in (None,False)
             and detail.get("isMultiple") in (None,False),
             "V41_SCHEMA_FIELD_NOT_WRITABLE")


def scoped_subset_update(rest,card,patch):
    """Only verified 1062/21 subset; do NOT weaken general Rest safety.

    Called only after verification of signed notice, snapshot, live field
    schema and durable PostgreSQL WRITE_INTENT. Zero retries on uncertainty.
    """
    need(isinstance(rest,Rest) and type(card) is int and 8<=card<=2_000_000_000
         and card not in (3,7),"V41_REST_ITEM_ID_INVALID")
    need(isinstance(patch,dict) and 0<len(patch)<=len(ALLOWED_PATCH)
         and set(patch)<=ALLOWED_PATCH
         and all(type(value) is str and value.strip()
                 and len(value)<=(
                     MAX_CRM_REPORT if key==REPORT else MAX_CRM_OTHER)
                 for key,value in patch.items()),
         "V41_SCOPED_UPDATE_FIELDS_INVALID")
    request={
        "entityTypeId":1062,"id":card,
        "useOriginalUfNames":"Y","fields":patch,
    }
    response=rest.client.post(rest.base+"/crm.item.update.json",json=request,
                              follow_redirects=False,timeout=20)
    need(response.status_code==200 and len(response.content)<=512000,
         "V41_BITRIX_UPDATE_HTTP_UNVERIFIED")
    try:
        obj=response.json()
    except ValueError:
        raise V37Blocked("V41_BITRIX_UPDATE_JSON_UNVERIFIED") from None
    need(isinstance(obj,dict) and "error" not in obj
         and isinstance(obj.get("result"),dict),
         "V41_BITRIX_UPDATE_REJECTED")
    return obj["result"]


def uncertain_no_effect_verified(conn,card,hash_,item):
    """Allow a fresh attempt only if a prior UNCERTAIN write was proven to
    have affected absolutely ZERO targeted fields. Do not alter old receipt.
    """
    row=conn.execute(f"""
       SELECT before_fields,after_fields,salt,digest
       FROM {TABLE}
       WHERE card_id=%s AND status='UNCERTAIN' AND source_hash=%s
       ORDER BY attempt_no DESC LIMIT 1
    """,(card,hash_)).fetchone()
    if row is None:
        return False
    old,desired,salt,proof=row
    if (not isinstance(old,dict) or not isinstance(desired,dict)
            or not old or set(old)!=set(desired)
            or not set(old)<=ALLOWED_PATCH
            or not isinstance(salt,(bytes,memoryview))
            or not isinstance(proof,(bytes,memoryview))):
        return False
    if (not secrets.compare_digest(_digest(desired,bytes(salt)),bytes(proof))
            or not all(item.get(key)==value for key,value in old.items())
            or not all(item.get(key)!=value for key,value in desired.items())):
        return False
    return True


def _sha(source):
    need(type(source) is str and re.fullmatch(r"B2B-CENTER-\d{5,12}",source),
         "SIGNED_SOURCE_REQUIRED")
    return hashlib.sha256(source.encode("ascii")).hexdigest()


def eligible(conn, target_ids=None):
    """One due card, even if V19/V20 already VERIFIED: no stale-source replay."""
    targets = sorted(set(target_ids)) if target_ids is not None else []
    if target_ids is not None:
        need(targets and len(targets)<=4 and
             all(type(x) is int and 8<=x<=2000000000 for x in targets),
             "TARGET_SCOPE_INVALID")
    with conn.cursor() as cur:
        cur.execute(f"""
           SELECT b.card_id,b.source_id,b.notice_json,last.status,last.source_hash
           FROM serviceit_vps_b2b_inbox b
           JOIN serviceit_vps_b2b_dossier_v11_receipts v
             ON v.card_id=b.card_id AND v.status='VERIFIED'
           LEFT JOIN LATERAL (
             SELECT r.status,r.source_hash,r.updated_at
             FROM {TABLE} r WHERE r.card_id=b.card_id
             ORDER BY attempt_no DESC LIMIT 1
           ) last ON true
           WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
             AND b.received_at < now() - interval '20 minutes'
             AND (%s::BIGINT[] IS NULL OR b.card_id=ANY(%s::BIGINT[]))
             AND (last.status IS NULL
               OR (last.status='VERIFIED'
                   AND last.updated_at < now() - interval '72 hours')
               OR (last.status IN (
                    'NO_ATTESTED_NEW_FACTS','INSUFFICIENT_CAPACITY',
                    'NONSTANDARD_REPORT')
                   AND last.updated_at < now() - interval '24 hours')
               OR (last.status='UNCERTAIN'
                   AND last.updated_at < now() - interval '15 minutes'))
             AND NOT EXISTS(
               SELECT 1 FROM serviceit_vps_native_receipts n
               WHERE n.card_id=b.card_id AND n.status IN ('RESERVED','WRITE_INTENT'))
           ORDER BY COALESCE(last.updated_at,b.received_at),
                    b.received_at,b.source_id LIMIT 1
        """,(targets if target_ids is not None else None,
              targets if target_ids is not None else None))
        return cur.fetchone()


def verified_lines(conn,card,source_hash,report):
    """Only source quotes in signed VERIFIED receipts and the current report."""
    lines = []
    with conn.cursor() as cur:
        for name in ("serviceit_vps_b2b_v19_research_receipts",
                     "serviceit_vps_b2b_v20_revision_receipts"):
            cur.execute("SELECT to_regclass(%s)", ("public."+name,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f"""
                SELECT after_fields FROM {name}
                WHERE card_id=%s AND status='VERIFIED' AND source_hash=%s
                ORDER BY updated_at DESC LIMIT 8
            """,(card,source_hash))
            for (fields,) in cur.fetchall():
                if not isinstance(fields,dict):
                    continue
                prior=fields.get(REPORT)
                if not isinstance(prior,str):
                    continue
                for line in prior.splitlines():
                    if (PROVEN_QUOTE.fullmatch(line.strip())
                            and line in report and line not in lines):
                        lines.append(line)
                    if len(lines)>=32:
                        return lines
    return lines


def _digest(patch,salt):
    need(type(patch) is dict and len(patch)>0 and
         type(salt) is bytes and len(salt)==16,
         "V37_DIGEST_INVALID")
    raw=json.dumps(patch,ensure_ascii=False,sort_keys=True,
                   separators=(",",":")).encode("utf-8")
    return hashlib.sha256(salt+raw).digest()


def reserve(conn,card,hash_,before):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {TABLE}
                (card_id,attempt_no,source_hash,status,before_fields)
                SELECT %s,COALESCE(MAX(attempt_no),0)+1,%s,'RESERVED',%s::jsonb
                FROM {TABLE} WHERE card_id=%s RETURNING attempt_no
            """,(card,hash_,json.dumps(before,ensure_ascii=False),card))
            row=cur.fetchone()
            need(row is not None and 1<=int(row[0])<=1000000,
                 "V37_RESERVE_FAILED")
            return int(row[0])


def mark(conn,card,attempt,status,patch=None):
    need(status in RESULT | {"WRITE_INTENT"}, "V37_STATUS_INVALID")
    with conn.transaction():
        with conn.cursor() as cur:
            if status == "WRITE_INTENT":
                need(type(patch) is dict and len(patch)>0,
                     "V37_PATCH_REQUIRED")
                salt=secrets.token_bytes(16)
                proof=_digest(patch,salt)
                cur.execute(f"""
                    UPDATE {TABLE}
                    SET status='WRITE_INTENT',after_fields=%s::jsonb,
                        salt=%s,digest=%s,updated_at=now()
                    WHERE card_id=%s AND attempt_no=%s AND status='RESERVED'
                    RETURNING attempt_no
                """,(json.dumps(patch,ensure_ascii=False),
                      salt,proof,card,attempt))
                need(cur.fetchone() is not None,"V37_WRITE_INTENT_NOT_DURABLE")
                return salt,proof
            previous = "WRITE_INTENT" if status in ("VERIFIED","UNCERTAIN") else "RESERVED"
            cur.execute(f"""
               UPDATE {TABLE} SET status=%s,updated_at=now()
               WHERE card_id=%s AND attempt_no=%s AND status=%s
               RETURNING attempt_no
            """,(status,card,attempt,previous))
            need(cur.fetchone() is not None,"V37_RECEIPT_STATE_CONFLICT")


def _get(rest,card):
    obj=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(obj,card)
    return obj


def _non_target_unchanged(before,after,changed):
    ignored=set(changed) | {
        "updatedTime","updatedBy","lastActivityTime","lastActivityBy"}
    return all(after.get(key)==value for key,value in before.items()
               if key not in ignored)


def run_once(env,client,conn,*,target_ids=None,preview=False):
    """Never mutate on preview; one card per normal cycle; versioned receipts."""
    if env.get(ENABLED)!="YES":
        return {"status":"V37_DISABLED","crm_writes":0}
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "V37_PRIVACY_GATES_MISSING")
    # Do not permit a pending V36 code-only deployment to change
    # reviewed source evidence/authorizations inside the same call.
    exact=exact_environment(env)
    if preview:
        # A read-only preview must not CREATE TABLE or mutate any receipt.
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", ("public." + TABLE,))
            if cur.fetchone()[0] is None:
                return {"status": "V37_PREVIEW_LEDGER_NOT_READY",
                        "crm_writes": 0}
    else:
        conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
        locked=bool(cur.fetchone()[0])
    if not locked:
        return {"status":"V37_OTHER_DOSSIER_WORKER_ACTIVE","crm_writes":0}
    try:
        row=eligible(conn,target_ids=target_ids)
        if not row:
            return {"status":"V37_NO_DUE_SIGNED_CARD","crm_writes":0}
        card,source,notice,last_status,last_hash=row
        source_hash=_sha(source)
        if last_hash and last_hash!=source_hash:
            return {"status":"V37_SOURCE_CHANGED_NO_REPLAY","crm_writes":0}
        if (not isinstance(notice,dict)
                or notice.get("tender_id")!=source.split("-")[-1]
                or notice.get("source")!="B2B-Center"):
            return {"status":"V37_ORIGINAL_IDENTITY_CONFLICT","crm_writes":0}
        webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest=Rest(webhook,client)
        first=_get(rest,card)
        src_url,title=safe_notice(notice,card,source,first)
        need(src_url==notice["source_url"] and bool(title),
             "V37_SIGNED_IDENTITY_CONFLICT")
        if last_status=="UNCERTAIN" and not uncertain_no_effect_verified(
                conn,card,source_hash,first):
            return {"status":"V41_UNCERTAIN_RECONCILIATION_BLOCKED",
                    "crm_writes":0}
        before=original_nine(first)
        lines=verified_lines(conn,card,source_hash,before[REPORT])
        try:
            patch,meta=prepare(notice,first,lines)
        except ReconcileBlocked:
            if preview:
                return {"status":"V37_PREVIEW_CONFLICT_NO_WRITE",
                        "crm_writes":0, "card":card, "draft_ready":False}
            # A persistent legal/company mismatch must NOT starve every
            # subsequent signed tender for repeated five-minute cycles.
            # Record only a dedicated PG conflict receipt; never call CRM.
            if active_card_write(conn,card):
                return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,"CONFLICT")
            return {"status":"V37_PREPARE_CONFLICT_NO_WRITE",
                    "crm_writes":0}
        if preview:
            return {"status":"V37_READONLY_PREVIEW","crm_writes":0,
                    "card":card,"draft_ready":bool(patch),
                    "verified_lines":len(lines),
                    "structured_candidates":meta.get("structured_candidates",0),
                    "preview_result":meta.get("status","UNKNOWN")}
        if active_card_write(conn,card):
            return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
        if patch is None:
            reason=meta.get("status")
            result=("INSUFFICIENT_CAPACITY" if reason=="CRM_FIELD_CAPACITY_INSUFFICIENT"
                    else "NONSTANDARD_REPORT" if reason=="NONSTANDARD_REPORT_MANUAL_REVIEW"
                    else "NO_ATTESTED_NEW_FACTS")
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,result)
            return {"status":"V37_"+result,"crm_writes":0}
        try:
            verify_patch_schema(rest,patch)
        except (V37Blocked, KeyError, TypeError):
            # Treat missing or non-writable CRM fields as a conflict; do not
            # persist a WRITE_INTENT or endlessly select this first card.
            if active_card_write(conn,card):
                return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,"CONFLICT")
            return {"status":"V41_SCHEMA_BLOCKED_NO_WRITE","crm_writes":0}
        fresh=_get(rest,card)
        if (fingerprint(first)!=fingerprint(fresh)
                or active_card_write(conn,card)
                or any(fresh.get(k)!=first.get(k) for k in patch)):
            return {"status":"V37_PREWRITE_CHANGED_NO_WRITE","crm_writes":0}
        attempt=reserve(conn,card,source_hash,
                        {k:first.get(k) for k in patch})
        salt,proof=mark(conn,card,attempt,"WRITE_INTENT",patch)
        try:
            scoped_subset_update(rest,card,patch)
            after=_get(rest,card)
            need(all(after.get(k)==v for k,v in patch.items())
                 and _non_target_unchanged(fresh,after,patch)
                 and secrets.compare_digest(_digest(patch,salt),proof),
                 "V37_READBACK_FAILED")
            mark(conn,card,attempt,"VERIFIED")
            return {"status":"V37_CRM_RECONCILED_VERIFIED","crm_writes":1,
                    "fields_updated":len(patch),
                    "public_reviewed":meta.get("reviewed") is True}
        except Exception:
            try:
                mark(conn,card,attempt,"UNCERTAIN")
            except Exception:
                pass
            return {"status":"V37_UNCERTAIN_NO_REPLAY","crm_writes":"UNVERIFIED"}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))
