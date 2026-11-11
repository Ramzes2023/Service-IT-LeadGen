"""OFF-by-default free-only public mirror input for the existing V19/V20 worker.

It is NOT a second CRM writer or a new polling loop. It only returns source
evidence after exact full-page validation, or None. Index results are not facts.
Marketplace HTML extraction requires source-specific operator approval; denied
401/403/robots/auth/CAPTCHA never become a browser/proxy-bypass workflow.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .serviceit_b2b_remote_public_fetch import (
    discover_free_public_urls, fetch_remote_public_evidence, approved_url,
)
from .serviceit_vps_pg_queue import secret_file, VpsBlocked

ENABLE = "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED"
T_EN = "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED"
B_EN = "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED"
SECRET = "SERVICEIT_TINYFISH_API_KEY"
MAX_URLS_PER_RESEARCH = 1


def _permitted(url: str, env: dict) -> bool:
    if not approved_url(url):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host in {"tendem.ru", "www.tendem.ru"}:
        return env.get(T_EN) == "YES"
    if host in {"b2b-center.ru", "www.b2b-center.ru"}:
        return env.get(B_EN) == "YES"
    return False


def read_free_public_mirror(notice: dict, env: dict, client) -> dict | None:
    """At most 1 Search + 1 Fetch(URL) per bounded native research attempt.

    Existing PG receipt/lock controls retries and prevents duplicate Bitrix
    writes. This function neither reserves a receipt nor writes a CRM field.
    """
    if not isinstance(env, dict) or env.get(ENABLE) != "YES":
        return None
    if (env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") != "YES"
            or env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") != "YES_1062_21"):
        return None
    if not isinstance(notice, dict) or notice.get("source") != "B2B-Center":
        return None
    tender_id, subject = notice.get("tender_id"), notice.get("subject")
    if (type(tender_id) is not str or re.fullmatch(r"\d{5,12}", tender_id) is None
            or type(subject) is not str or not 10 <= len(subject) <= 240):
        return None
    if env.get(T_EN) != "YES" and env.get(B_EN) != "YES":
        return None
    try:
        api_key = secret_file(env, SECRET, optional=True)
    except (VpsBlocked, OSError, UnicodeError):
        return None
    if not api_key:
        return None

    # Source notice is a B2B website URL. Never send that host to cloud Fetch
    # unless explicitly licensed. If inaccessible, discover an alternative
    # independently published Tendum detail using Search URL hints only.
    source_url = notice.get("source_url")
    if _permitted(source_url, env) and urlsplit(source_url).hostname in {
            "b2b-center.ru", "www.b2b-center.ru"}:
        urls = [source_url]
    else:
        candidates = discover_free_public_urls(
            client, tender_id=tender_id, subject=subject, api_key=api_key)
        urls = [url for url in candidates if _permitted(url, env)]
        urls = urls[:MAX_URLS_PER_RESEARCH]
    if not urls:
        return None
    fetched = fetch_remote_public_evidence(
        client, urls=urls, tender_id=tender_id,
        subject=subject, api_key=api_key)
    if not isinstance(fetched, dict) or not fetched.get("pages"):
        return None
    # Adapt EXACT verified full-page evidence to the current source_findings,
    # public_documents, RouterAI and V11/V19/V20 functions. No new writer.
    return {
        **fetched,
        "status": "PUBLIC_PAGES_INSPECTED",
        "discovery": "FREE_FETCH_VERIFIED_PUBLIC_DETAIL_ONLY",
        "remote_status": "REMOTE_PUBLIC_EVIDENCE_VALIDATED",
        "free_source_requests_max": {"search": 1, "fetch_urls": 1},
        "contacts": [], "company_site_candidate": "",
        "independent_company_research": None,
    }
