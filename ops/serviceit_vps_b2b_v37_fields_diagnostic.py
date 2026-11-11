#!/usr/bin/env python3
"""V37 four-card current custom field and due-date availability READONLY.

Only enum statuses and existing field codes enter logs, never names, tender
text, user data, INNs, CRM URLs or credentials.
"""
from __future__ import annotations
import os,re,sys

import httpx

# Kept separate from V37 modules so this probe runs BEFORE deployment.
REVIEWED = {
    "4673971":{"buyer":"ПАО «Московская Биржа»",
               "inn":"7702077840","region":"Москва"},
    "4672802":{"buyer":"ПАО «Агрегат»",
               "inn":"7401000191","region":"Челябинская область"},
    "4674861":{"buyer":"ООО «Ева»",
               "inn":"7708335695","region":"Москва"},
    "4675475":{"buyer":"ПАО «Московский Кредитный Банк»",
               "inn":"7734202860","region":"Москва"},
}
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_enrichment_worker import Rest,normalized,validate_identity
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_b2b_v19_backfill import select_one
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice


def emit(label,value):
    assert re.fullmatch(r"[A-Z0-9_]{2,70}",label)
    assert re.fullmatch(r"[A-Z0-9_]{2,100}|[0-9]{1,4}",str(value))
    print("V37_FIELD_DIAG_"+label+"="+str(value),flush=True)


def status(current, expected):
    if current in (None,"","-","Не установлен","не установлен"):
        return "EMPTY"
    if not isinstance(current,str):
        return "OTHER_TYPE"
    import re
    simplify=lambda s:re.sub(r"[^а-яa-z0-9]+","",s.casefold())
    return "MATCH" if simplify(current)==simplify(expected) else "CONFLICT"


def main():
    if len(sys.argv)!=1:
        return 2
    with db(dict(os.environ)) as conn,httpx.Client(
            trust_env=False,follow_redirects=False,timeout=25) as client:
        webhook=secret_file(dict(os.environ),"SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest=Rest(webhook,client)
        meta=normalized(rest("crm.item.fields",{
            "entityTypeId":1062,"useOriginalUfNames":"Y"}))
        fields=meta.get("fields",{}) if isinstance(meta,dict) else {}
        emit("METADATA_READ","YES" if isinstance(fields,dict) else "NO")
        date_codes=[c for c in fields
                    if re.fullmatch(r"[A-Za-z0-9_]{5,80}",c)
                    and any(w in c.upper() for w in
                            ("DEADLINE","DUE","END_DATE","DATE_END","TENDER_DATE"))]
        emit("DATE_FIELD_CANDIDATES",len(date_codes))
        for i,code in enumerate(date_codes[:5],1):
            emit("DATE_FIELD_"+str(i),code.upper())
        rows=select_one(conn,preview=True)
        assert len(rows)==4
        for card,source,notice in rows:
            tid=notice.get("tender_id")
            assert card in (11,13,15,17) and tid in REVIEWED
            item=normalized(rest("crm.item.get",{
                "entityTypeId":1062,"id":card,
                "useOriginalUfNames":"Y"})["item"])
            validate_identity(item,card)
            url,title=safe_notice(notice,card,source,item)
            assert url==notice["source_url"] and title
            expected=REVIEWED[tid]
            for key,reference in (
                ("INN",expected["inn"]),("COMPANY",expected["buyer"]),
                ("REGION",expected["region"])):
                code=PREFIX+{"INN":"COMPANY_INN",
                             "COMPANY":"COMPANY_NAME",
                             "REGION":"REGION"}[key]
                emit("CARD_"+str(card)+"_"+key,status(item.get(code),reference))
            report=item.get(PREFIX+"AI_FULL_REPORT") or ""
            emit("CARD_"+str(card)+"_V11",
                 "YES" if "01  ПАСПОРТ ЗАКУПКИ" in report else "NO")
            emit("CARD_"+str(card)+"_EXTERNAL_ADDENDUM",
                 "YES" if "РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ" in report
                 else "NO")
            emit("CARD_"+str(card)+"_NAMED_MANAGER",
                 "YES" if any(s in report.casefold()
                              for s in ("для михаила","действие михаила"))
                 else "NO")
    emit("CRM_WRITES",0)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        emit("SAFE_FAILURE","YES")
        raise SystemExit(2)
