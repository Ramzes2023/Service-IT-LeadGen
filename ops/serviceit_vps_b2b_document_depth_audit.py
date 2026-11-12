"""Read-only document availability on signed B2B cards and a public EIS example."""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from leadpilot.serviceit_b2b_tender_documents import inspect_public_documents
from leadpilot.serviceit_b2b_eis_mirrors import supplement_eis_mirrors
from leadpilot.serviceit_public_evidence_research import _page
from leadpilot.serviceit_vps_pg_queue import db

MODULES = {"EXTRACT":"serviceit_b2b_tender_documents.py",
           "WORKER":"serviceit_vps_b2b_document_worker.py",
           "V11":"serviceit_vps_b2b_dossier_v11_worker.py"}


def emit(k, v):
    print(f"DOCS_{k}={v}", flush=True)


def main():
    env = dict(os.environ)
    emit("ACTIVE", "YES" if env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED") == "YES" else "NO")
    for name, filename in MODULES.items():
        emit("SHA_"+name, hashlib.sha256((Path("/app/leadpilot")/filename).read_bytes()).hexdigest().upper())
    with db(env) as conn, conn.transaction(), httpx.Client(trust_env=False,follow_redirects=False,timeout=8) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT b.card_id,b.notice_json,COALESCE(r.status,'NONE')
            FROM serviceit_vps_b2b_inbox b
            LEFT JOIN serviceit_vps_b2b_tender_doc_receipts r ON b.card_id=r.card_id
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
            ORDER BY b.card_id DESC LIMIT 15
        """).fetchall()
        emit("CARD_COUNT", len(rows))
        for card,notice,state in rows:
            emit(f"CARD_{card}_STATE",state)
            try:
                url = notice["source_url"]
                page = _page(client,url,frozenset({"b2b-center.ru"}))
                findings = supplement_eis_mirrors({"status":"PUBLIC_PAGES_INSPECTED",
                    "pages":[{"url":url,"excerpt":page["text"]}]},notice,client)
                res = inspect_public_documents(client,findings,notice["tender_id"],max_downloads=2)
                emit(f"CARD_{card}_LINKS",res["discovered"])
                emit(f"CARD_{card}_FILES",len(res["documents"]))
                emit(f"CARD_{card}_EIS_FILES_LINKED",res.get("eis_links",0))
                for reason, count in res["rejections"].items():
                    if reason.startswith(("DOCUMENT_", "RESEARCH_")) and reason.replace("_","").isalnum():
                        emit(f"CARD_{card}_{reason}",count)
                for index, document in enumerate(res["documents"],1):
                    parsed = urlsplit(document["url"])
                    emit(f"CARD_{card}_FILE_{index}_FORMAT",document["kind"].lstrip(".").upper())
                    emit(f"CARD_{card}_FILE_{index}_BYTES",document["bytes"])
                    emit(f"CARD_{card}_FILE_{index}_ORIGIN",
                         (parsed.hostname or "").replace(".","_").replace("-","_").upper())
                    emit(f"CARD_{card}_FILE_{index}_HASH",document["sha256"].upper())
                if res["documents"]:
                    from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
                    group = {"status":"PUBLIC_PAGES_INSPECTED","pages":[
                        {"url":d["url"],"excerpt":d["text"]} for d in res["documents"]
                    ]}
                    emit(f"CARD_{card}_VERIFIED_FILE_QUOTES",len(source_only_investigation(group)["facts"]))
                emit(f"CARD_{card}_EIS_MIRRORS",findings.get("eis_mirror_count",0))
            except Exception:
                emit(f"CARD_{card}_FETCH","UNAVAILABLE")
        for tag, number in (("CONTROL","32616565221"),("CARD27","32616566539")):
            try:
                source = _page(client,f"https://sprosradar.ru/zakupki/223/{number}/",frozenset({"sprosradar.ru"}))
                official = [urlsplit(link) for link, _ in source["links"] if urlsplit(link).hostname == "zakupki.gov.ru"]
                emit(tag+"_OFFICIAL_LINKS",len(official))
                emit(tag+"_EIS_223_DOWNLOAD_LINKS",sum(
                    x.path == "/223/purchase/public/download/download.html" for x in official))
                emit(tag+"_EIS_FILESTORE_LINKS",sum("filestore" in x.path for x in official))
                from urllib.parse import parse_qsl
                for parsed in official:
                    if "filestore" in parsed.path:
                        path = parsed.path.strip("/").replace("/","_").replace(".","_").replace("-","_")
                        keys = "_".join(key for key,_ in parse_qsl(parsed.query))
                        if (path.replace("_","").isalnum() and keys.replace("_","").isalnum()
                                and len(path)<100 and len(keys)<40):
                            emit(tag+"_PATH_SHAPE",path.upper()+"_"+keys.upper())
                        break
                if tag == "CONTROL":
                    url = f"https://sprosradar.ru/zakupki/223/{number}/"
                    control = inspect_public_documents(client,{"status":"PUBLIC_PAGES_INSPECTED","pages":[
                        {"url":url,"excerpt":source["text"],"eis_notice_number":number}
                    ]},number,max_downloads=2)
                    emit("CONTROL_SAFE_LINKS",control.get("eis_links",0))
                    emit("CONTROL_READABLE",len(control["documents"]))
                    for reason,count in control["rejections"].items():
                        if reason.startswith(("DOCUMENT_", "RESEARCH_")) and reason.replace("_","").isalnum():
                            emit("CONTROL_"+reason,count)
            except Exception:
                emit(tag+"_MIRROR","UNAVAILABLE")


if __name__=="__main__":
    main()
