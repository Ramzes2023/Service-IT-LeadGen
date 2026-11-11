"""Opt-in read-only public B2B source fallback; no access-control bypass or CRM writes.

Only separately approved public Tendum / official B2B details. Kontur is
intentionally excluded: its denied VPS origin needs an authorized API/export.
"""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re
import httpx
from .serviceit_b2b_public_mirrors import detailed_public_excerpt
from .serviceit_public_evidence_research import _safe_model_excerpt
from .serviceit_b2b_public_fact_extractor import extract_tender_facts

API = "https://api.fetch.tinyfish.ai"
HOSTS = frozenset(("tendem.ru", "www.tendem.ru",
                   "b2b-center.ru", "www.b2b-center.ru"))


def approved_url(url: str) -> bool:
    if type(url) is not str or not 20 <= len(url) <= 800:
        return False
    try:
        p = urlsplit(url)
        host, port = (p.hostname or "").lower(), p.port
    except ValueError:
        return False
    if (p.scheme != "https" or host not in HOSTS or p.username or p.password
            or p.fragment or p.query or port not in (None, 443)
            or any(ord(c) < 32 for c in url)
            or not p.path.startswith("/") or p.path.startswith("//")
            or ".." in p.path.split("/")):
        return False
    if host.endswith("tendem.ru"):
        return re.fullmatch(r"/tenders/\d{5,12}/?", p.path) is not None
    return re.fullmatch(
        r"/(?:app/market-next|market)/[a-z0-9-]+/tender-\d{5,12}/?",
        p.path) is not None


def _normalize(s: str) -> str:
    return " ".join(re.findall(r"[a-zа-я0-9]+",
                               s.casefold().replace("ё", "е")))


def verified_excerpt(url: str, text: str, tender_id: str,
                     subject: str) -> str:
    if (not approved_url(url) or type(text) is not str
            or not 50 <= len(text) <= 80000
            or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240):
        return ""
    text = re.sub(r"[*#]+", " ", text).replace("\u00a0", " ")
    path = urlsplit(url).path
    if "tendem.ru" in urlsplit(url).hostname:
        # Reuse the repository's exact-ID/B2B/title proof and long-page
        # evidence extractor rather than trusting an index snippet.
        return detailed_public_excerpt(
            [s.strip() for s in text.splitlines() if s.strip()][:2800],
            tender_id, subject)[:4700]
    if (not re.search(r"/tender-" + tender_id + r"/?$", path)
            or not re.search(r"(?<!\d)№\s*" + tender_id + r"(?!\d)",
                             text[:2500])
            or _normalize(subject) not in _normalize(text[:2500])):
        return ""
    return " ".join(text.split())[:4700]


def fetch_remote_public_evidence(
        client: httpx.Client, *, urls: list[str],
        tender_id: str, subject: str, api_key: str) -> dict | None:
    """Pass verified public source text to the existing evidence pipeline.

    Caller must explicitly opt in, authorize use of the extraction provider,
    save only evidence after validation, and use the existing CRM single-writer.
    """
    if (type(urls) is not list or not 1 <= len(urls) <= 2
            or len(set(urls)) != len(urls)
            or not all(approved_url(u) for u in urls)
            or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240
            or type(api_key) is not str or not 16 <= len(api_key) <= 256
            or not api_key.isascii()
            or "\n" in api_key or "\r" in api_key):
        return None
    try:
        response = client.post(
            API, json={"urls": urls, "format": "markdown", "links": False,
                       "image_links": False, "ttl": 0,
                       "per_url_timeout_ms": 15000},
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            follow_redirects=False, timeout=38)
        if (response.status_code != 200 or len(response.content) > 350000
                or "application/json" not in
                response.headers.get("content-type", "")):
            return None
        result = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if (type(result) is not dict
            or type(result.get("results")) is not list):
        return None
    pages = []
    structured_facts = []
    for row in result["results"][:2]:
        if type(row) is not dict:
            continue
        url = row.get("url")
        if (url not in urls or row.get("final_url") != url
                or any(item["url"] == url for item in pages)):
            continue
        excerpt = verified_excerpt(url, row.get("text"), tender_id, subject)
        if excerpt:
            pages.append({"url": url, "excerpt": excerpt})
            structured_facts.append(extract_tender_facts(
                tender_id=tender_id, title=subject, tender_text=row["text"],
                source_url=url, verified_source=True))
    if not pages:
        return None
    return {
        "status": "REMOTE_PUBLIC_EVIDENCE_VALIDATED",
        "pages": pages, "structured_facts": structured_facts, "contacts": [],
        "model_excerpt": _safe_model_excerpt(pages[0]["excerpt"]),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "company_site_candidate": "", "employer_named_in_source": "",
        "browser_rendered_unverified": False,
        "independent_company_research": None,
        "discovery": "OPT_IN_CLOUD_PUBLIC_PAGE_EXTRACT",
    }
