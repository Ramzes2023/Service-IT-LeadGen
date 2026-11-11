"""Read-only exact public Tendem page probe through installed free TinyFish Fetch.

Owner-only GitHub Actions passes this script via stdin to the EXISTING
coordinator's Python interpreter. No CRM, PG, RouterAI, private card data,
Docker restart, poll or live environment changes. One URL one Fetch attempt.
Never prints key, response text, buyer/INN/contacts or HTTP request details.
"""
from __future__ import annotations

import os
import httpx

from leadpilot.serviceit_b2b_free_public_fallback import read_free_public_mirror

TENDER_ID = "4673595"
TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
URL = "https://tendem.ru/tenders/16548471"
NOTICE = {
    "source": "B2B-Center",
    "tender_id": TENDER_ID,
    "subject": TITLE,
    "source_url": "https://www.b2b-center.ru/market/okazanie-uslug-po-predostavleniiu-it-spetsialistov/tender-4673595/",
}
INDEX = [{"evidence_level": "INDEX_ONLY_UNVERIFIED", "url": URL}]


def inspect() -> bool:
    env = dict(os.environ)
    # One-process verification only. These settings are NEVER written to
    # Docker/Compose or used to poll and never call a CRM endpoint.
    env.update({
        "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "YES",
        "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "YES",
        "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "NO",
    })
    try:
        with httpx.Client(trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(15, read=30)) as client:
            result = read_free_public_mirror(
                NOTICE, env, client, index_hints=INDEX)
    except (OSError, ValueError, httpx.HTTPError):
        result = None
    if not isinstance(result, dict):
        print("TINYFISH_TENDEM_PROBE=NO_VERIFIED_PAGE", flush=True)
        return False
    pages = result.get("pages") or []
    facts = result.get("structured_facts") or []
    valid = (
        result.get("status") == "PUBLIC_PAGES_INSPECTED"
        and len(pages) == len(facts) == 1
        and pages[0].get("url") == URL
        and TENDER_ID in pages[0].get("excerpt", "")
        and facts[0].get("tender_id", {}).get("value") == TENDER_ID
    )
    print("TINYFISH_TENDEM_PROBE=" +
          ("VERIFIED_EXACT" if valid else "NO_VERIFIED_PAGE"), flush=True)
    print("TINYFISH_TENDEM_REQUESTS=ONE_URL", flush=True)
    print("TINYFISH_TENDEM_CRM_WRITES=NO", flush=True)
    print("TINYFISH_TENDEM_LIVE_FLAGS_CHANGED=NO", flush=True)
    return valid


if __name__ == "__main__":
    raise SystemExit(0 if inspect() else 3)
