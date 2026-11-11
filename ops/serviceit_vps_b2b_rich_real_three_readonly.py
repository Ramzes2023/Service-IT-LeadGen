#!/usr/bin/env python3
"""No-write live VPS check of three publicly verified tender #4673595 URLs.

External public sources, NOT private CRM or source-email content; no database,
no payment, no RouterAI, no Brave search, no login, no Bitrix, no downloads.
Source pages use the production robots/HTTPS/DNS/size restrictions. Print
ONLY fixed statuses by provider, never customer texts/phone/address/IDs/URLs.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import os
import re
import httpx


def emit(name,value):
    if not re.fullmatch(r"[A-Z0-9_]{3,65}",name):
        raise ValueError("BAD_FIELD")
    if value not in ("YES","NO"):
        raise ValueError("BAD_RESULT")
    print("RICH_PUBLIC_PROBE_"+name+"="+value,flush=True)


def check():
    from leadpilot.serviceit_b2b_public_mirrors import from_public_page
    from leadpilot.serviceit_vps_b2b_deep_research import (
        public_buyer_candidate,published_buyer_inn_evidence,
    )
    from leadpilot.serviceit_b2b_routerai_deep import (
        public_documents,source_only_investigation,
    )
    from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS,live_permitted

    module=Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    expected="b2841b8dafce749659b56868877e72ca6fb752943eb44b06f71e717705b04895"
    digest=hashlib.sha256(module.read_bytes()).hexdigest() if module.is_file() else "MISSING"
    emit("REVIEWED_SOURCE_SHA","YES" if digest==expected else "NO")
    env=dict(os.environ)
    live=(live_permitted(env) and INTERVAL_SECONDS==300
          and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
          and env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED")=="YES"
          and env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED")=="YES")
    emit("PRODUCTION_LIVE","YES" if live else "NO")
    if digest!=expected or not live:
        emit("SAFE_FINISHED","NO")
        return 2

    examples=(
      ("KONTUR","zakupki.kontur.ru",
       "https://zakupki.kontur.ru/4673595_4"),
      ("SYNAPSE","synapsenet.ru",
       "https://synapsenet.ru/zakupki/b2b/4673595--nizhegorodskaya-obl-okazanie-uslug-po-predostavleniyu-itspecialistov"),
      ("TENDEM","tendem.ru",
       "https://tendem.ru/tenders/16548471"),
    )
    tender_id="4673595"
    title="Предоставление IT-специалистов, включая 1С, DevOps и ИБ"
    with httpx.Client(trust_env=False,follow_redirects=False,
                      timeout=9.0) as client:
        for name,host,url in examples:
            try:
                row=from_public_page(client,url,tender_id,title,
                                     mirror_host=host)
                if not row:
                    emit(name+"_READABLE","NO")
                    continue
                excerpt=row["excerpt"]
                findings={"status":"PUBLIC_PAGES_INSPECTED","pages":[row]}
                buyer=public_buyer_candidate(findings)
                inn=published_buyer_inn_evidence(findings)
                docs=public_documents(findings)
                report=source_only_investigation(findings)
                cats={f.get("category") for f in report.get("facts",[])
                      if type(f) is dict}
                emit(name+"_READABLE","YES")
                emit(name+"_BUYER","YES" if buyer else "NO")
                emit(name+"_INN","YES" if inn else "NO")
                emit(name+"_DEADLINE","YES" if "23.11.2026" in excerpt else "NO")
                emit(name+"_WORK_GEO","YES" if
                     ("Москва" in excerpt and
                      ("удалённо" in excerpt or "удаленно" in excerpt)) else "NO")
                emit(name+"_MULTIPLE_ROLES","YES" if
                     (("DevOps" in excerpt or "devops" in excerpt)
                      and ("1С" in excerpt or "1C" in excerpt)) else "NO")
                emit(name+"_MODEL_DOCUMENT","YES" if docs else "NO")
                emit(name+"_CITABLE_FACTS","YES" if cats else "NO")
            except Exception:
                # Never log URL/response/exception: site may deny robots,
                # block unauthenticated users, or be temporarily unavailable.
                emit(name+"_READABLE","NO")
    emit("SAFE_FINISHED","YES")
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(check())
    except Exception:
        emit("SAFE_FINISHED","NO")
        raise SystemExit(2)
