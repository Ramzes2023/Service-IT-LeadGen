#!/usr/bin/env python3
"""Inspect the V37 ambiguous write receipt vs current signed CRM card READONLY.

No customer text, URLs, names, INNs, report body or secret material printed.
Does not alter any PostgreSQL or CRM content.
"""
from __future__ import annotations
import hashlib,json,os,re,sys
import httpx

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.bitrix_metadata_audit import validate_webhook

CARDS=(11,13,15,17)
TABLE="serviceit_vps_b2b_dossier_consistency_v37_receipts"
AI_REPORT="UF_CRM_13_SVCIT_AI_FULL_REPORT"
INN="UF_CRM_13_SVCIT_COMPANY_INN"
REGION="UF_CRM_13_SVCIT_REGION"
VALID=frozenset(("RESERVED","WRITE_INTENT","VERIFIED",
                 "NO_ATTESTED_NEW_FACTS","INSUFFICIENT_CAPACITY",
                 "NONSTANDARD_REPORT","CONFLICT","UNCERTAIN","NO_RECEIPT"))


def out(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,70}",k)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,5}|[A-Z0-9_]{3,70})",str(v))
    print("V37_UNCERTAIN_AUDIT_"+k+"="+str(v),flush=True)


def read_one(conn,rest,card):
    row=conn.execute(f"""
      SELECT status,before_fields,after_fields,salt,digest
      FROM {TABLE} WHERE card_id=%s
      ORDER BY attempt_no DESC LIMIT 1
    """,(card,)).fetchone()
    if not row:
        out("CARD_"+str(card)+"_STATUS","NO_RECEIPT")
        return
    status,old,expected,salt,proof=row
    out("CARD_"+str(card)+"_STATUS",status if status in VALID else "INVALID")
    if card!=11 or status!="UNCERTAIN":
        return
    if not isinstance(old,dict) or not isinstance(expected,dict):
        out("CARD_11_PATCH_PERSISTED","NO")
        return
    out("CARD_11_PATCH_PERSISTED","YES")
    item=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,
        "useOriginalUfNames":"Y"})["item"])
    validate_identity(item,card)
    equal_after=sum(item.get(k)==v for k,v in expected.items())
    equal_before=sum(item.get(k)==v for k,v in old.items())
    out("CARD_11_TOTAL_PATCH_FIELDS",len(expected))
    out("CARD_11_MATCH_EXPECTED_FIELDS",equal_after)
    out("CARD_11_MATCH_PRIOR_FIELDS",equal_before)
    out("CARD_11_ALL_EXPECTED_MATCH","YES" if equal_after==len(expected) else "NO")
    out("CARD_11_ALL_PRIOR_MATCH","YES" if equal_before==len(old) else "NO")
    if isinstance(salt,(bytes,memoryview)) and isinstance(proof,(bytes,memoryview)):
        raw=json.dumps(expected,ensure_ascii=False,sort_keys=True,
                       separators=(",",":")).encode("utf-8")
        digest=hashlib.sha256(bytes(salt)+raw).digest()
        out("CARD_11_DURABLE_DIGEST_MATCH","YES" if digest==bytes(proof) else "NO")
    else:
        out("CARD_11_DURABLE_DIGEST_MATCH","NO")
    for k,name in ((AI_REPORT,"REPORT"),(INN,"INN"),(REGION,"REGION")):
        if k not in expected:
            continue
        out("CARD_11_"+name+"_IS_EXPECTED",
            "YES" if item.get(k)==expected[k] else "NO")
        out("CARD_11_"+name+"_IS_OLD",
            "YES" if item.get(k)==old.get(k) else "NO")


def main():
    if len(sys.argv)!=1:
        return 2
    env=dict(os.environ)
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with db(env) as conn,httpx.Client(
            trust_env=False,follow_redirects=False,timeout=20) as client:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            exists=conn.execute("SELECT to_regclass(%s)",("public."+TABLE,)).fetchone()[0]
            if exists is None:
                out("LEDGER_EXISTS","NO")
                return 2
            out("LEDGER_EXISTS","YES")
            rest=Rest(webhook,client)
            for card in CARDS:
                read_one(conn,rest,card)
    out("CRM_WRITES",0)
    out("POSTGRES_WRITES",0)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("SAFE_FAILURE","YES")
        raise SystemExit(2)
