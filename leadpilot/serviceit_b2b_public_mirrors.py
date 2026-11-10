"""Find independently readable public tender evidence in an approved mirror.

The official B2B page remains the primary source. This adapter searches only
public pages of explicitly reviewed mirrors; it never skips robots, opens
auth-required pages, accepts search snippets as proof, or sends vendor-email,
CRM identities or contacts to external models. The result is SOURCE evidence,
not automatic lead qualification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit
import re

import httpx

from .serviceit_public_evidence_research import (
    _host, _robots, _get, VisibleText, ResearchBlocked, _safe_model_excerpt,
)

MIRRORS = frozenset(("tendem.ru",))
MAX_CANDIDATES = 5
ID_RX = r"(?<!\d){id}(?!\d)"


def matched_excerpt(text: str, tender_id: str, subject: str = "") -> str:
    """Extract text AROUND the exact tender ID, not the first 7k characters.

    A long public HTML page can list related notices in its footer. Do not
    identify a procurement by a matching number alone: require at least two
    independent distinctive subject words nearby, or leave unverified.
    """
    if (type(text) is not str or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240):
        return ""
    text = " ".join(text.split())
    tokens = re.findall(r"[^\W_]{3,}", subject.casefold())
    stops = {"закупка", "предоставление", "оказание", "включая",
             "услуги", "работы", "специалистов", "тендер", "поставка",
             "техническое", "требуется", "компании", "для", "или"}
    important = set(tokens) - stops
    # Code/abbreviation words (1С, ИБ) may be short but meaningful.
    if re.search(r"(?i)\b1[сc]\b", subject):
        important.add("1с")
    if re.search(r"(?i)\bdevops\b", subject):
        important.add("devops")
    if len(important) < 2:
        return ""
    matches = list(re.finditer(ID_RX.format(id=tender_id), text))
    for m in matches[:12]:
        # Advertiser-related or navigation text elsewhere in page must not
        # satisfy the content match. Keep checks within same local vicinity.
        neighborhood = text[max(0, m.start()-950):min(len(text),m.end()+1700)]
        terms = set(re.findall(r"[^\W_]{3,}", neighborhood.casefold()))
        if re.search(r"(?i)\b1[сc]\b", neighborhood):
            terms.add("1с")
        if len(important & terms) < 2:
            continue
        # Retain the tender number; after it, likely description/requirements.
        # Capture enough context on both sides, not the site-wide navbar.
        excerpt = text[max(0, m.start()-550):min(len(text),m.end()+1750)]
        return excerpt
    # Some marketplaces show the official B2B number in their SOURCE link
    # after a long lot table, while the procurement title/terms appear near
    # the top. This is a bounded document-level cross-check, NOT a mere ID
    # match: the top introduction must also share 2 distinctive terms, and
    # the page must identify the same B2B source platform.
    head = text[:3100]
    head_terms = set(re.findall(r"[^\W_]{3,}", head.casefold()))
    if re.search(r"(?i)\b1[сc]\b", head):
        head_terms.add("1с")
    if (matches and len(important & head_terms) >= 2
            and "b2b-center" in text.casefold()
            and ("закупк" in head.casefold() or "тендер" in head.casefold()
                 or "запрос" in head.casefold())):
        matched = text[max(0,matches[0].start()-250):
                       min(len(text),matches[0].end()+350)]
        return head[:2900] + " Источник и номер: " + matched
    return ""


def from_public_page(client: httpx.Client, url: str, tender_id: str,
                     subject: str, mirror_host="tendem.ru"):
    if mirror_host not in MIRRORS:
        raise ResearchBlocked("MIRROR_NOT_APPROVED")
    if _host(url, frozenset((mirror_host,))) not in (
            mirror_host, "www." + mirror_host):
        raise ResearchBlocked("MIRROR_URL_INVALID")
    # Reuse independent DNS/global-IP, HTTPS/port, robots and size bounds.
    _robots(client, url, frozenset((mirror_host,)))
    raw = _get(client, url, frozenset((mirror_host,)))
    parser = VisibleText()
    parser.feed(raw)
    visible = " ".join(parser.parts)
    # Full HTML visible text is scanned, including pages >7k where the exact
    # ID may be buried after banners; only the relevant 2.3k window is retained.
    excerpt = matched_excerpt(visible, tender_id, subject)
    if not excerpt:
        return None
    return {
        "url": url, "excerpt": excerpt,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "INDEPENDENT_PUBLIC_MIRROR",
    }


def brave_mirror_search(client: httpx.Client, tender_id: str, subject: str,
                        key: str, *, allow_mirrors=MIRRORS):
    """One numeric query per mirror; max five candidate independent GETs.

    Does not trust Brave snippets, images or auto answers as procurement facts.
    """
    if (type(key) is not str or not key.isascii()
            or not 12 <= len(key) <= 200 or "\n" in key or "\r" in key
            or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(allow_mirrors) is not frozenset
            or not allow_mirrors <= MIRRORS):
        return None
    from .serviceit_independent_company_research import SEARCH_URL
    # One already independently verified public mirror URL is a hint, NOT
    # trusted content: the same DNS, HTTPS, robots and exact-ID/title checks
    # always run again. All other procurement IDs use Brave discovery.
    known = {"4673595": "https://tendem.ru/tenders/16548471"}
    if "tendem.ru" in allow_mirrors and tender_id in known:
        try:
            page = from_public_page(
                client, known[tender_id], tender_id, subject)
            if page:
                return {
                    "status": "PUBLIC_PAGES_INSPECTED",
                    "pages": [{"url": page["url"],
                               "excerpt": page["excerpt"][:3500]}],
                    "contacts": [], "model_excerpt": _safe_model_excerpt(
                        page["excerpt"]),
                    "checked_at": page["checked_at"],
                    "company_site_candidate": "",
                    "employer_named_in_source": "",
                    "browser_rendered_unverified": False,
                    "independent_company_research": None,
                    "discovery": "INDEPENDENT_PUBLIC_MIRROR_REVERIFIED",
                }
        except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
            pass
    for mirror in sorted(allow_mirrors):
        try:
            res = client.get(
                SEARCH_URL, params={"q": 'site:' + mirror + ' "' +
                                    tender_id + '"', "count": MAX_CANDIDATES},
                headers={"X-Subscription-Token": key,
                         "Accept": "application/json",
                         "Cache-Control": "no-cache"},
                follow_redirects=False, timeout=12.0)
            if (res.status_code != 200
                    or not 0 < len(res.content) <= 160000
                    or "application/json" not in res.headers.get("content-type", "")):
                continue
            body = res.json()
            candidates = body.get("web", {}).get("results", [])
            if not isinstance(candidates, list) or len(candidates) > 20:
                continue
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            continue
        for hit in candidates[:MAX_CANDIDATES]:
            if type(hit) is not dict:
                continue
            url = hit.get("url")
            try:
                # An allowlisted URL alone is not evidence. Confirm the exact
                # number and at least two distinctive technical/title terms.
                item = from_public_page(
                    client, url, tender_id, subject, mirror_host=mirror)
                if item is None:
                    continue
                return {
                    "status": "PUBLIC_PAGES_INSPECTED",
                    "pages": [{"url": item["url"],
                               "excerpt": item["excerpt"][:3500]}],
                    "contacts": [], "model_excerpt": _safe_model_excerpt(
                        item["excerpt"]),
                    "checked_at": item["checked_at"],
                    "company_site_candidate": "",
                    "employer_named_in_source": "",
                    "browser_rendered_unverified": False,
                    "independent_company_research": None,
                    "discovery": "BRAVE_INDEPENDENT_MIRROR_EXACT_ID_AND_TERMS",
                }
            except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
                continue
    return None
