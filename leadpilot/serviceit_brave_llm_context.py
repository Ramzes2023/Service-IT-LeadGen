"""Brave LLM Context discovery, one request within existing 3-search budget.

Public company name from the source notice only. Indexed snippets and titles
are CANDIDATES, never facts, model inputs or company ownership evidence.
Only the separately fetched, robots-compliant page may enter the AI report.
"""
from __future__ import annotations

import re

import httpx

CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
MAX_BODY_BYTES = 256_000
MAX_INDEXED_CONTEXT = 12


class ContextUnavailable(ValueError):
    pass


def _norm(s):
    if not isinstance(s, str):
        return ""
    return " ".join(re.findall(r"\w+", s.casefold().replace("ё", "е")))


def context_candidates(client: httpx.Client, *, query: str,
                       public_name: str, token: str) -> list[str]:
    """Make exactly ONE index call; never repeat after 429/timeout/5xx.

    Response chunks are only used to select candidate URLs to visit using the
    existing HTTPS/DNS/robots and explicit public-name checks.
    """
    if (not isinstance(client, httpx.Client)
            or not isinstance(query, str) or not 1 <= len(query) <= 380
            or len(query.split()) > 50
            or not isinstance(public_name, str) or not 5 <= len(public_name) <= 140
            or not isinstance(token, str) or not 12 <= len(token) <= 200
            or not token.isascii() or "\r" in token or "\n" in token):
        raise ContextUnavailable("CONTEXT_BAD_CONFIG")
    try:
        response = client.get(
            CONTEXT_URL,
            params={
                "q": query, "country": "RU", "search_lang": "ru",
                "safesearch": "strict", "count": 8,
                "maximum_number_of_urls": 8,
                "maximum_number_of_tokens": 4096,
                "maximum_number_of_tokens_per_url": 1024,
                "context_threshold_mode": "balanced",
            },
            headers={"X-Subscription-Token": token,
                     "Accept": "application/json",
                     "Cache-Control": "no-cache"},
            timeout=15.0, follow_redirects=False)
        if (response.status_code != 200
                or not 0 < len(response.content) <= MAX_BODY_BYTES
                or "application/json" not in response.headers.get("content-type", "")):
            raise ContextUnavailable("CONTEXT_ENDPOINT_UNAVAILABLE")
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise ContextUnavailable("CONTEXT_ENDPOINT_UNAVAILABLE") from None
    if not isinstance(payload, dict):
        raise ContextUnavailable("CONTEXT_SCHEMA_UNVERIFIED")
    grounding, sources = payload.get("grounding"), payload.get("sources")
    if (not isinstance(grounding, dict)
            or not isinstance(grounding.get("generic"), list)
            or len(grounding["generic"]) > 50
            or not isinstance(sources, dict) or len(sources) > 50):
        raise ContextUnavailable("CONTEXT_SCHEMA_UNVERIFIED")
    candidates = []
    name = _norm(public_name)
    if len(name.split()) < 2:
        return []
    for entry in grounding["generic"][:MAX_INDEXED_CONTEXT]:
        if not isinstance(entry, dict):
            continue
        url, title, chunks = (entry.get("url"), entry.get("title", ""),
                              entry.get("snippets", []))
        if (not isinstance(url, str) or not 12 <= len(url) <= 1000
                or not isinstance(title, str) or len(title) > 400
                or not isinstance(chunks, list) or len(chunks) > 20
                or url not in sources or not isinstance(sources[url], dict)):
            continue
        safe_chunks = [s for s in chunks[:8] if isinstance(s, str) and len(s) <= 4000]
        # A name hit in extracted content can discover an article whose headline
        # does not mention the employer; the fetched page is independently checked.
        if name not in _norm(title + " " + " ".join(safe_chunks)):
            continue
        candidates.append(url)
    return list(dict.fromkeys(candidates))[:8]
