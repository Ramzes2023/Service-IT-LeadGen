"""Public-only Deep Research evidence pass for all signed CRM cards, no writes."""
import os
import re

import httpx

from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import _get
from leadpilot.serviceit_b2b_eis_mirrors import supplement_eis_mirrors
from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_public_evidence_research import _page
from leadpilot.serviceit_vps_b2b_deep_research import published_buyer_inn_evidence, safe_notice
from leadpilot.serviceit_vps_pg_queue import db, secret_file


def emit(name, value):
    print(f"MANAGER_{name}={value}", flush=True)


def main():
    env = dict(os.environ)
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False,follow_redirects=False,timeout=7) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest = Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        rows = conn.execute("""
            SELECT card_id,source_id,notice_json
            FROM serviceit_vps_b2b_inbox
            WHERE state='VERIFIED' AND card_id IS NOT NULL ORDER BY card_id
        """).fetchall()
        emit("FREE_CHECKED",len(rows))
        for card,source,notice in rows:
            try:
                item = _get(rest,card)
                url,_ = safe_notice(notice,card,source,item)
                page = _page(client,url,frozenset({"b2b-center.ru"}))
                body = " ".join(page["text"].split())
                tender = notice["tender_id"]
                if not re.search(r"(?<!\d)" + re.escape(tender) + r"(?!\d)",body):
                    emit(f"FREE_{card}_PUBLIC","ID_NOT_FOUND")
                    continue
                result = supplement_eis_mirrors({
                    "status":"PUBLIC_PAGES_INSPECTED",
                    "pages":[{"url":url,"excerpt":body}],
                },notice,client)
                report = source_only_investigation(result)
                emit(f"FREE_{card}_PUBLIC","YES")
                emit(f"FREE_{card}_QUOTES",len(report["facts"]))
                emit(f"FREE_{card}_INN","YES" if (result.get("eis_registry_buyer")
                    or published_buyer_inn_evidence(result)) else "NO")
            except Exception:
                emit(f"FREE_{card}_PUBLIC","UNAVAILABLE")


if __name__ == "__main__":
    main()
