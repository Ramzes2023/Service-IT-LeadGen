#!/usr/bin/env python3
"""Read-only first original B2B card #11 vs official CBR issuer registry via free TinyFish.

One exact GET via the free TinyFish Fetch HTTPS API; no paid Agent/Browser.
Only count proof/dossier capacity; do not print legal data, title, contacts,
key, source text, Bitrix fields, webhook or live private content. No CRM/PG writes.
"""
from __future__ import annotations
import json
import os
import re
import sys

import httpx

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_vps_worker import live_permitted,INTERVAL_SECONDS
from leadpilot.serviceit_b2b_v19_backfill import (
    select_one,prepare_addendum,REPORT,
)
from leadpilot.serviceit_b2b_dossier_v11 import original_nine
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.bitrix_metadata_audit import validate_webhook

URL="https://www.cbr.ru/registries/rcb/ecb/orginfo/?id=11123764"
FETCH="https://api.fetch.tinyfish.ai"
TENDER="4673971"
INN="7702077840"
OGRN="1027739387411"
FOLD=lambda s:" ".join(s.casefold().replace("ё","е").split())


def say(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{2,70}",k)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,3}|[A-Z0-9_]{4,90})",str(v))
    print("CBR_FIRST_"+k+"="+str(v),flush=True)


def main():
    env=dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    assert env.get("SERVICEIT_TINYFISH_API_KEY_FILE")=="/run/secrets/serviceit_tinyfish_key"
    assert env.get("SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED")=="NO"
    say("LIVE_GATES","YES")
    with db(env) as conn:
      with conn.transaction():
        with conn.cursor() as cur:
          cur.execute("SET TRANSACTION READ ONLY")
          orig=select_one(conn,preview=True)
          assert len(orig)==4
          card,source,notice=orig[0]
          assert card==11 and source=="B2B-CENTER-"+TENDER
          assert notice["source"]=="B2B-Center"
          assert notice["tender_id"]==TENDER
          assert "сервер" in FOLD(notice["subject"])
          say("SIGNED_TENDER_ID","YES")
          with httpx.Client(trust_env=False,follow_redirects=False,timeout=40) as client:
            webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
            validate_webhook(webhook)
            rest=Rest(webhook,client)
            item=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
            validate_identity(item,card)
            url,title=safe_notice(notice,card,source,item)
            assert url==notice["source_url"] and bool(title)
            before=original_nine(item)
            # Verify that the previously signed and/or CRM buyer metadata
            # actually identifies the official issuer. Never infer buyer
            # solely from unrelated source index or same numeric tender ID.
            notice_text=FOLD(json.dumps(notice,ensure_ascii=False))
            source_buyer=("московская биржа" in notice_text or
                          "московская биржа" in FOLD(before[REPORT]))
            say("BUYER_NAME_MATCHED_SIGNED_OR_VERIFIED_REPORT",
                "YES" if source_buyer else "NO")
            key=secret_file(env,"SERVICEIT_TINYFISH_API_KEY",optional=True)
            assert key and 16<=len(key)<=256
            response=client.post(
                FETCH,
                json={"urls":[URL],"format":"markdown","links":False,
                      "image_links":False,"ttl":0,
                      "per_url_timeout_ms":17000},
                headers={"X-API-Key":key,"Accept":"application/json"},
                timeout=39,follow_redirects=False)
            if response.status_code!=200 or len(response.content)>300000:
                say("CBR_PUBLIC_FETCH_200","NO")
                return 0
            payload=response.json()
            rows=payload.get("results") if isinstance(payload,dict) else None
            if not isinstance(rows,list) or len(rows)!=1:
                say("CBR_PUBLIC_FETCH_200","NO")
                return 0
            row=rows[0]
            text=row.get("text")
            valid=(row.get("url")==URL and row.get("final_url")==URL
                   and isinstance(text,str) and len(text)>400
                   and INN in text and OGRN in text
                   and "Московская Биржа" in text)
            say("CBR_PUBLIC_FETCH_200","YES")
            say("CBR_REGISTRY_IDENTITY_VERIFIED","YES" if valid else "NO")
            if not valid or not source_buyer:
                say("ADDENDUM_READY","NO")
            else:
                line=("ОФИЦИАЛЬНЫЙ РЕЕСТР БАНКА РОССИИ / ПАО МОСКОВСКАЯ БИРЖА: "
                      "ИНН "+INN+", ОГРН "+OGRN+
                      "; юридическое лицо подтверждено реестром эмитентов. ["+URL+"]")
                draft=prepare_addendum(before,[line],TENDER)
                say("NEW_LEGAL_FACTS", "YES" if line not in before[REPORT] else "NO")
                say("ADDENDUM_READY", "YES" if draft else "NO")
    say("CRM_WRITES","NO")
    say("PG_WRITES","NO")
    say("PAID_MODEL_CALLS","NO")
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        say("FAIL_CLOSED","YES")
        raise SystemExit(2)
