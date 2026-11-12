"""Read-only official tender self-buyer and legal identity proof for real card 29.

Never calls paid search, model or CRM write. No raw response/secret logging.
"""
from __future__ import annotations

import os
import re
import httpx
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_b2b_deep_research import (
    verified_original_b2b_details, public_buyer_candidate
)
from leadpilot.serviceit_public_evidence_research import (
    _robots, _get, VisibleText, ResearchBlocked
)

SOURCE="B2B-CENTER-4677923"
CARD=29
SITES={
    "TBANK":"https://www.tbank.ru/business/contractor/legal/1027700058506/",
    "TOCHKA":"https://check.tochka.com/company/1027700058506/",
    "EGRUL":"https://egrul.org/7727218041",
    "SABY":"https://saby.ru/profile/7727218041-773001001",
    "XFIRM":"https://xfirm.ru/company/7727218041",
    "SLEDI":"https://sledi.ru/azot-vzryv-7727218041/",
    "B2BHOUSE":"https://b2b.house/company/AO-AZOT-VZRYV_43158d33-2b0a-4786-aaab-6716d134c4fa/",
}
ORGANIZER="АЗОТ-ВЗРЫВ"
EXPECTED_INN="7727218041"
EXPECTED_OGRN="1027700058506"


def emit(name,value):
    assert re.fullmatch(r"[A-Z0-9_]{2,50}",name)
    value=str(value).upper()
    if re.fullmatch(r"[A-Z0-9_]{1,85}",value):
        print("ORG29_"+name+"="+value,flush=True)


def main():
    with db(dict(os.environ)) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        row=conn.execute("""
          SELECT state,card_id,notice_json FROM serviceit_vps_b2b_inbox
          WHERE source_id=%s
        """,(SOURCE,)).fetchone()
    if not row or row[0]!="VERIFIED" or row[1]!=CARD:
        emit("SIGNAL","NOT_VERIFIED")
        return
    emit("SIGNAL","VERIFIED")
    notice=row[2]
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=12) as client:
        found=verified_original_b2b_details({"status":"PUBLIC_SOURCE_NOT_READABLE","pages":[]},
                                            notice,client)
        ok=found.get("original_b2b_detail_independently_read") is True
        emit("ORIGINAL_SOURCE", "VERIFIED" if ok else "NOT_VERIFIED")
        if not ok:
            return
        public=" ".join(found["pages"][0]["excerpt"].split())
        buyer=public_buyer_candidate(found)
        emit("SELF_BUYER_CANDIDATE","YES" if buyer and ORGANIZER in buyer.upper() else "NO")
        emit("SOURCE_FOR_OWN_NEEDS","YES" if
             re.search(r"(?iu)процедура\s+проводится\s+для\s+собственных\s+нужд",public)
             else "NO")
        emit("SOURCE_ORGANIZER","YES" if
             re.search(r"(?iu)\bорганизатор\s+АО\s+[«\"]АЗОТ-ВЗРЫВ[»\"]",public)
             else "NO")

        for code,url in SITES.items():
            host=httpx.URL(url).host
            try:
                _robots(client,url,frozenset({host}))
                raw=_get(client,url,frozenset({host}))
                parser=VisibleText()
                parser.feed(raw)
                text=" ".join(parser.parts)
                emit(code+"_READ","YES")
                emit(code+"_TEXT", min(len(text),100000))
                emit(code+"_LEGAL_NAME","YES" if re.search(
                     r"(?iu)(?:АО|АКЦИОНЕРНОЕ\s+ОБЩЕСТВО)\s+[«\"]?АЗОТ[-–]ВЗРЫВ",
                     text) else "NO")
                emit(code+"_INN","YES" if
                     re.search(r"(?<!\d)"+EXPECTED_INN+r"(?!\d)",text) else "NO")
                emit(code+"_OGRN","YES" if
                     re.search(r"(?<!\d)"+EXPECTED_OGRN+r"(?!\d)",text) else "NO")
                emit(code+"_ADDRESS","YES" if
                     "можайский вал" in text.lower() else "NO")
            except (ResearchBlocked,ValueError,httpx.HTTPError,TypeError) as exc:
                name=str(exc) if isinstance(exc,ResearchBlocked) else type(exc).__name__.upper()
                value=name if re.fullmatch(r"[A-Z0-9_]{4,80}",name) else "READ_UNAVAILABLE"
                emit(code+"_READ",value)


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        emit("FAILURE",type(exc).__name__.upper())
        raise SystemExit(2) from None
