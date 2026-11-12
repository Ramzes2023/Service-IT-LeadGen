"""Read-only source identification for the one requested public tender."""
import os
import re
import sys
from urllib.parse import urlsplit

import httpx

from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.serviceit_vps_pg_queue import db, secret_file

CARD = 27


def main():
    env = dict(os.environ)
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=16) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute("""
            SELECT source_id, notice_json FROM serviceit_vps_b2b_inbox
            WHERE card_id=%s AND state='VERIFIED'
        """, (CARD,)).fetchone()
        if row is None:
            sys.exit(2)
        source, notice = row
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        card = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": CARD, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(card, CARD)
        url, _ = safe_notice(notice, CARD, source, card)
        tender = notice["tender_id"]
        assert re.fullmatch(r"[0-9]{5,12}", tender)
        assert urlsplit(url).hostname in {"www.b2b-center.ru", "b2b-center.ru"}
        print("CARD27_WEB_TENDER=" + tender, flush=True)
        print("CARD27_WEB_INN_IN_CRM=" + ("YES" if card.get(PREFIX+"COMPANY_INN") else "NO"), flush=True)
        for name in ("TAVILY_SEARCH", "TAVILY_RESEARCH_MINI"):
            enabled = env.get("SERVICEIT_VPS_B2B_" + name + "_ENABLED") == "YES"
            print("CARD27_WEB_" + name + "_ENABLED=" + ("YES" if enabled else "NO"), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("CARD27_WEB_FAILURE_CLASS=" + type(error).__name__.upper(), flush=True)
        sys.exit(2)
