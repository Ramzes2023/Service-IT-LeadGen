"""V44: optional Tavily basic URL discovery for SIGNED B2B notices.

Zero additional CRM or AI writers. Tavily snippets and generated answers NEVER
become facts; only subsequent normal HTTPS/robots/identity-verified public page
fetches enter existing dossier analysis. One search per signed source FOREVER,
independently of how often the five-minute coordinator runs.

Reserve one search credit before HTTP using a durable PostgreSQL receipt.
All failures and crashes remain spent (no blind retry). Hard monthly allowance
of at most 300 Tavily basic searches, leaving 700 of the provider's advertised
1000 free monthly credits for other uses. No automatic paid plan/upgrade.

Search input is ONLY a published numeric B2B tender ID; never send vendor
email body, CRM fields, buyer contacts, personal information, or RouterAI data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_vps_pg_queue import secret_file
from .serviceit_public_evidence_research import _safe_model_excerpt

ENDPOINT = "https://api.tavily.com/search"
TABLE = "serviceit_vps_b2b_tavily_v44_search_receipts"
MAX_MONTHLY_REQUESTS = 300
MAX_RESULTS = 6
SEARCH_LOCK = 693712410
MIRRORS = frozenset(("zakupki.kontur.ru", "tendem.ru", "synapsenet.ru"))
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_tavily_v44_search_receipts (
 source_hash CHAR(64) PRIMARY KEY,
 tender_id VARCHAR(12) NOT NULL CHECK (tender_id ~ '^[0-9]{5,12}$'),
 status VARCHAR(24) NOT NULL CHECK (status IN (
   'RESERVED', 'CANDIDATES', 'NO_RESULTS', 'PROVIDER_FAILED', 'PROVIDER_LIMITED')),
 candidate_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
 attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (jsonb_typeof(candidate_urls) = 'array')
);
CREATE INDEX IF NOT EXISTS serviceit_vps_b2b_tavily_v44_month_idx
 ON serviceit_vps_b2b_tavily_v44_search_receipts(attempted_at);
"""

class TavilyUnavailable(ValueError):
    pass


def permitted(env):
    return (
        env.get("SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED") == "YES"
        and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"
        and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
        and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21"
    )


def _identity(notice):
    if (not isinstance(notice, dict)
            or notice.get("source") != "B2B-Center"
            or not isinstance(notice.get("tender_id"), str)
            or not re.fullmatch(r"[0-9]{5,12}", notice["tender_id"])):
        raise TavilyUnavailable("TAVILY_ORIGINAL_SIGNED_NOTICE_REQUIRED")
    tender_id = notice["tender_id"]
    # The source ID is derived from the authenticated/verified original
    # B2B notification, never from Tavily's response.
    source_hash = hashlib.sha256(("B2B-CENTER-"+tender_id).encode("ascii")).hexdigest()
    return tender_id, source_hash


def safe_candidate(url):
    if not isinstance(url, str) or not 20 <= len(url) <= 800:
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or p.hostname not in MIRRORS
                or p.port not in (None, 443) or p.username or p.password
                or p.fragment or p.query or not p.path.startswith("/")):
            return None
        if re.search(r"(?i)(login|auth|account|user|signin|redirect)", p.path):
            return None
    except ValueError:
        return None
    return url


def _search_once(client, api_key, tender_id):
    if (not isinstance(api_key, str)
            or not re.fullmatch(r"[!-~]{20,220}", api_key)):
        raise TavilyUnavailable("TAVILY_SECRET_INVALID")
    response = client.post(
        ENDPOINT,
        headers={"Authorization": "Bearer " + api_key,
                 "Accept": "application/json",
                 "Content-Type": "application/json"},
        json={
            "query": '"' + tender_id + '" B2B-Center тендер закупка',
            "search_depth": "basic",
            "topic": "general",
            "max_results": MAX_RESULTS,
            "include_domains": sorted(MIRRORS),
            "include_domains_mode": "restrict",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
        },
        follow_redirects=False,
        timeout=18.0,
    )
    if response.status_code in (429, 432, 433):
        return "PROVIDER_LIMITED", []
    if (response.status_code != 200
            or len(response.content) > 200000
            or "application/json" not in response.headers.get("content-type", "")):
        return "PROVIDER_FAILED", []
    payload = response.json()
    if not isinstance(payload,dict) or not isinstance(payload.get("results"),list):
        return "PROVIDER_FAILED", []
    rows = payload["results"]
    if len(rows) > 30:
        return "PROVIDER_FAILED", []
    urls = []
    for row in rows[:MAX_RESULTS]:
        if not isinstance(row,dict):
            continue
        url = safe_candidate(row.get("url"))
        if url and url not in urls:
            urls.append(url)
    return ("CANDIDATES" if urls else "NO_RESULTS"), urls[:MAX_RESULTS]


def discover_urls(conn, client, env, notice):
    """Return cached discovery URLs, reserving at most ONE credit per source.

    This is the only paid API call path. The monthly cap includes FAILED,
    rate-limited, RESERVED/crashed calls, so no optimistic billing assumptions.
    """
    if not permitted(env):
        return []
    try:
        api_key = secret_file(env, "SERVICEIT_TAVILY_SEARCH_API_KEY", optional=True)
    except (ValueError, OSError):
        return []
    if not api_key:
        return []
    tender_id, source_hash = _identity(notice)
    # V45: guarantee that historical/manual B2B cards never consume Tavily
    # credits, even when V19/V20 reprocess them hours or days later. The
    # original verified inbox receipt and its activation timestamp are the
    # source of truth, not the AI-generated notice or public search result.
    from .serviceit_vps_worker import SEARCH_POLICY_START
    row = conn.execute("""
        SELECT b.received_at FROM serviceit_vps_b2b_inbox b
        WHERE b.source_id=%s AND b.state='VERIFIED'
          AND b.notice_json->>'tender_id'=%s
        LIMIT 1
    """, ("B2B-CENTER-" + tender_id, tender_id)).fetchone()
    if (not row or not isinstance(row[0], datetime)
            or row[0].tzinfo is None or row[0] < SEARCH_POLICY_START):
        return []
    with conn.transaction():
        conn.execute(DDL)
    with conn.transaction():
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (SEARCH_LOCK,)
        ).fetchone()
        if not locked or not locked[0]:
            return []
        row = conn.execute(
            f"SELECT status,candidate_urls FROM {TABLE} "
            "WHERE source_hash=%s", (source_hash,)).fetchone()
        if row is not None:
            # Cached candidate hints; only independent public GET can verify.
            state, urls = row
            return [u for u in urls[:MAX_RESULTS] if safe_candidate(u)] if (
                state == "CANDIDATES" and isinstance(urls,list)) else []
        count = conn.execute(
            f"SELECT count(*) FROM {TABLE} "
            "WHERE attempted_at >= date_trunc('month',now())"
        ).fetchone()
        if int(count[0]) >= MAX_MONTHLY_REQUESTS:
            return []
        created = conn.execute(f"""
            INSERT INTO {TABLE}(source_hash,tender_id,status)
            VALUES (%s,%s,'RESERVED')
            ON CONFLICT(source_hash) DO NOTHING
            RETURNING source_hash
        """, (source_hash,tender_id)).fetchone()
        if not created:
            return []
    state, urls = "PROVIDER_FAILED", []
    try:
        state, urls = _search_once(client,api_key,tender_id)
    except (httpx.HTTPError, ValueError, TypeError):
        # No provider payloads or API keys may enter log/PG.
        pass
    with conn.transaction():
        conn.execute(
            f"UPDATE {TABLE} SET status=%s,candidate_urls=%s::jsonb "
            "WHERE source_hash=%s AND status='RESERVED'",
            (state,json.dumps(urls),source_hash))
    return urls


def verified_mirror_search(conn, client, env, notice, browser=None):
    """Search only hints and independently read public proof before use."""
    urls = discover_urls(conn,client,env,notice)
    if not urls:
        return None
    from .serviceit_b2b_public_mirrors import (
        _from_page_or_public_browser, _detail_score,
    )
    tender_id, subject = notice["tender_id"], notice["subject"]
    if not isinstance(subject,str) or not 10 <= len(subject) <= 240:
        return None
    checked = []
    for url in urls[:MAX_RESULTS]:
        host = urlsplit(url).hostname
        try:
            row = _from_page_or_public_browser(
                client, url, tender_id, subject, host, browser)
            if row:
                checked.append(row)
        except Exception:
            # robots, HTTP 403, paywalls, 401, identity mismatches all block.
            continue
    if not checked:
        return None
    results = sorted(checked,key=lambda r: (
        -_detail_score(r["excerpt"]), -len(r["excerpt"]), r["url"]))[:2]
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": r["url"], "excerpt": r["excerpt"][:4700]}
                  for r in results],
        "contacts": [],
        "model_excerpt": _safe_model_excerpt(results[0]["excerpt"]),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "company_site_candidate": "",
        "employer_named_in_source": "",
        "browser_rendered_unverified": False,
        "independent_company_research": None,
        "discovery": "TAVILY_URLS_WITH_INDEPENDENT_PUBLIC_READ",
    }
