"""Resolve public 223-FZ records from the verified B2B publication."""
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_public_evidence_research import ResearchBlocked, _host, _page, _safe_model_excerpt
from .serviceit_vps_b2b_deep_research import MAX_PAGES


EIS_NOTICE_NUMBER = re.compile(
    r"(?iu)\bномер\s+извещения\s+(?:на|в)\s+ЕИС\s*[:№]?\s*(3\d{10})(?!\d)"
)
EIS_MIRRORS = (
    ("tendik.ru", "/tender/eis223/{number}"),
    ("sprosradar.ru", "/zakupki/223/{number}/"),
)


def _source_eis_number(findings, notice, client):
    url, tid = notice["source_url"], notice["tender_id"]
    hosts = frozenset({"b2b-center.ru"})
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(?:app/market-next|market)/[a-z0-9-]+/tender-(\d{5,12})/?",
                         parsed.path)
    if (_host(url, hosts) not in {"www.b2b-center.ru", "b2b-center.ru"}
            or not match or match[1] != tid or parsed.query):
        return None
    texts = [p["excerpt"] for p in findings.get("pages", [])
             if isinstance(p, dict) and p.get("url") == url and isinstance(p.get("excerpt"), str)]
    for text in texts:
        numbers = set(EIS_NOTICE_NUMBER.findall(text))
        if len(numbers) == 1 and re.search(r"№\s*" + re.escape(tid) + r"(?!\d)", text):
            return next(iter(numbers))
    # Search extracts are often truncated before the EIS label. Read the
    # canonical signed page once with the shared public/robots policy.
    page = _page(client, url, hosts)
    text = " ".join(page["text"].split())
    numbers = set(EIS_NOTICE_NUMBER.findall(text))
    if len(numbers) == 1 and re.search(r"№\s*" + re.escape(tid) + r"(?!\d)", text):
        return next(iter(numbers))
    return None


def supplement_eis_mirrors(findings, notice, client):
    """Cross-check exact registry-linked public pages, with no provider credits."""
    try:
        number = _source_eis_number(findings, notice, client)
    except (ResearchBlocked, httpx.HTTPError, KeyError, TypeError):
        return findings
    if not number:
        return findings
    tid = notice["tender_id"]
    tender_number = re.compile(r"(?<!\d)" + re.escape(tid) + r"(?!\d)")
    eis_number = re.compile(r"(?<!\d)" + re.escape(number) + r"(?!\d)")
    words = re.findall(r"[^\W_]{4,}", notice["subject"].casefold())
    words = set(words) - {"закупка", "тендер", "поставка", "приобретение"}
    prior = list(findings.get("pages") or [])
    known = {p["url"] for p in prior if isinstance(p, dict) and "url" in p}
    added = []
    for host, path in EIS_MIRRORS:
        url = "https://" + host + path.format(number=number)
        if url in known:
            continue
        try:
            text = " ".join(_page(client, url, frozenset({host}))["text"].split())
        except (ResearchBlocked, httpx.HTTPError):
            continue
        match = tender_number.search(text)
        if not match or not eis_number.search(text):
            continue
        window = text[max(0, match.start()-300):match.end()+300].casefold()
        if len(words & set(re.findall(r"[^\W_]{4,}", window))) < 2:
            continue
        added.append({"url": url, "excerpt": text[:6800]})
    if not added:
        return findings
    return {**findings, "status": "PUBLIC_PAGES_INSPECTED",
            "pages": (prior[:1] + added + prior[1:])[:MAX_PAGES],
            "model_excerpt": findings.get("model_excerpt") or _safe_model_excerpt(added[0]["excerpt"]),
            "eis_mirror_count": len(added)}


