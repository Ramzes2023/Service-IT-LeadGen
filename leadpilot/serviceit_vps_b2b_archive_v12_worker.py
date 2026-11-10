"""V12 archive-continuity repair for source-loss regressions in four B2B cards.

One-use PostgreSQL receipt with prior nine-field snapshot, a durable
WRITE_INTENT before every CRM update, exact independent readback and no
automatic uncertain replay. No RouterAI/Brave/browser calls or external mail.
Cards with richer V11 evidence are always left completely untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets

import httpx

from .serviceit_b2b_archive_continuity_v12 import repair, ArchiveBlocked
from .serviceit_b2b_hold_ai_fields import AI_FIELDS
from .serviceit_enrichment_worker import Rest, normalized, validate_identity, fingerprint
from .serviceit_vps_b2b_dossier_v11_worker import (
    compute_digest, original_nine, non_ai_unchanged,
)
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_vps_pg_queue import db, secret_file
from .serviceit_vps_worker import live_permitted
from .bitrix_metadata_audit import validate_webhook

LOCK = 6754819001  # same PostgreSQL advisory lock as current V11 worker
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_dossier_v12_continuity_receipts(
 card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
 status TEXT NOT NULL CHECK(status IN
 ('RESERVED','WRITE_INTENT','VERIFIED','NO_CHANGE','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL, after_fields JSONB,
 salt BYTEA, digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""


class V12Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise V12Blocked(code)


def next_source(conn):
    with conn.cursor() as cur:
        cur.execute("""
          SELECT r.card_id,r.before_fields,r.after_fields
          FROM serviceit_vps_b2b_dossier_v11_receipts r
          WHERE r.status='VERIFIED'
            AND NOT EXISTS (
              SELECT 1 FROM serviceit_vps_b2b_dossier_v12_continuity_receipts v
              WHERE v.card_id=r.card_id)
          ORDER BY r.card_id LIMIT 1
        """)
        return cur.fetchone()


def receipt(conn, card, current):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              INSERT INTO serviceit_vps_b2b_dossier_v12_continuity_receipts
                 (card_id,status,before_fields)
              VALUES(%s,'RESERVED',%s::jsonb)
              ON CONFLICT(card_id) DO NOTHING
              RETURNING card_id
            """, (card,json.dumps(current,sort_keys=True,ensure_ascii=False)))
            return cur.fetchone() is not None


def update_receipt(conn, card, previous, new):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_dossier_v12_continuity_receipts
              SET status=%s,updated_at=now()
              WHERE card_id=%s AND status=%s RETURNING card_id
            """, (new,card,previous))
            require(cur.fetchone() is not None,"RECEIPT_STATUS_CONFLICT")


def before_write(conn, card, fields):
    salt = secrets.token_bytes(16)
    digest = compute_digest(fields, salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_dossier_v12_continuity_receipts
              SET status='WRITE_INTENT',after_fields=%s::jsonb,
                  salt=%s,digest=%s,updated_at=now()
              WHERE card_id=%s AND status='RESERVED'
              RETURNING card_id
            """, (json.dumps(fields,sort_keys=True,ensure_ascii=False),
                  salt,digest,card))
            require(cur.fetchone() is not None,"WRITE_INTENT_NOT_DURABLE")
    return salt,digest


def worker_once(env, client, conn, preview=False):
    require(live_permitted(env), "V12_LIVE_APPROVAL_REQUIRED")
    conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK,))
        locked = cur.fetchone()[0]
    if not locked:
        return {"status":"V12_ANOTHER_WORKER_ACTIVE","crm_writes":0}
    try:
        row=next_source(conn)
        if row is None:
            return {"status":"V12_NO_ELIGIBLE_CARDS","crm_writes":0}
        card,old_v10,verified_v11=row
        PostgresNativeFence.validate_card(card)
        require(type(old_v10) is dict and type(verified_v11) is dict,
                "V11_ARCHIVE_VALUES_INVALID")
        webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest=Rest(webhook,client)
        first=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
        })["item"])
        validate_identity(first,card)
        current=original_nine(first)
        require(current==verified_v11,"V11_VERIFIED_CRM_DRIFT")
        target,info=repair(current,old_v10)
        if preview:
            return {"status":"V12_PREVIEW","crm_writes":0,
                    "restored_quotes":info["restored_quotes"]}
        if not receipt(conn,card,current):
            return {"status":"V12_ALREADY_RESERVED_NO_RETRY","crm_writes":0}
        phase="RESERVED"
        try:
            if target==current:
                update_receipt(conn,card,"RESERVED","NO_CHANGE")
                return {"status":"V12_NO_CHANGE","crm_writes":0}
            # Re-read before update: preserve concurrent CRM edits from other
            # services instead of overwriting them with stale AI text.
            fresh=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
            })["item"])
            validate_identity(fresh,card)
            require(fingerprint(first)==fingerprint(fresh)
                    and all(fresh.get(k)==current[k] for k in AI_FIELDS),
                    "CRM_STALE_PREWRITE_CONFLICT")
            salt,proof=before_write(conn,card,target)
            phase="WRITE_INTENT"
            rest("crm.item.update",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                "fields":target,
            })
            result=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
            })["item"])
            validate_identity(result,card)
            observed={key:result.get(key) for key in AI_FIELDS}
            require(observed==target
                    and secrets.compare_digest(
                        compute_digest(observed,salt),proof)
                    and non_ai_unchanged(fresh,result),
                    "V12_EXACT_NINE_READBACK_NOT_VERIFIED")
            update_receipt(conn,card,"WRITE_INTENT","VERIFIED")
            return {"status":"V12_RESTORED_AND_VERIFIED","crm_writes":1,
                    "restored_quotes":info["restored_quotes"]}
        except Exception:
            try:
                update_receipt(conn,card,phase,
                    "UNCERTAIN" if phase=="WRITE_INTENT" else "CONFLICT")
            except Exception:
                pass
            return {"status":"V12_BLOCKED_NO_BLIND_REPLAY",
                    "crm_writes":"UNVERIFIED" if phase=="WRITE_INTENT" else 0}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK,))


def verify_all(env,client,conn):
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    rest=Rest(webhook,client)
    with conn.cursor() as cur:
        cur.execute("""
          SELECT card_id,after_fields,salt,digest
          FROM serviceit_vps_b2b_dossier_v12_continuity_receipts
          WHERE status='VERIFIED'
          ORDER BY card_id
        """)
        rows=cur.fetchall()
    checked=0
    for card,expected,salt,proof in rows:
        observed=normalized(rest("crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"
        })["item"])
        validate_identity(observed,card)
        fields={name:observed.get(name) for name in AI_FIELDS}
        require(fields==expected
                and secrets.compare_digest(
                    compute_digest(fields,bytes(salt)),bytes(proof)),
                "V12_READBACK_MISMATCH")
        checked+=1
    return {"status":"V12_ARCHIVE_EXACT_READBACK",
            "verified_repaired_cards":checked,"crm_writes":0}


def main(argv=None):
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--batch-four",action="store_true")
    g.add_argument("--preview",action="store_true")
    g.add_argument("--verify",action="store_true")
    args=p.parse_args(argv)
    env=dict(os.environ)
    try:
        with db(env) as conn:
            with httpx.Client(trust_env=False,follow_redirects=False,
                              timeout=24) as client:
                if args.verify:
                    data=verify_all(env,client,conn)
                elif args.preview:
                    data=worker_once(env,client,conn,preview=True)
                else:
                    rows=[worker_once(env,client,conn) for _ in range(4)]
                    data={"status":"V12_BATCH",
                          "outcomes":rows,
                          "crm_writes":sum(x.get("crm_writes")==1 for x in rows)}
        print(json.dumps(data,sort_keys=True,ensure_ascii=False))
        if data.get("status")=="V12_BLOCKED_NO_BLIND_REPLAY":
            return 2
        return 2 if any(x.get("status")=="V12_BLOCKED_NO_BLIND_REPLAY"
                        for x in data.get("outcomes",[])) else 0
    except Exception:
        print(json.dumps({"status":"V12_GLOBAL_BLOCKED",
                          "no_blind_retry":True}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
