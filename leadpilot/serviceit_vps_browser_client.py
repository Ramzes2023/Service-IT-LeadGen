"""Bounded coordinator-side client for local isolated public browser bridge.

Never passes Bitrix item IDs, webhook, customer data, model prompts or secrets.
Only a preapproved public HTTPS page URL may be sent over host loopback.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from .serviceit_public_evidence_research import _host, _public_dns, ResearchBlocked

BRIDGE_URL = "http://127.0.0.1:18785/render"
MAX_BODY_BYTES = 15000
MAX_TEXT_CHARS = 6000


def render_approved_url(url: str, allowed: frozenset[str], *,
                        client: httpx.Client) -> dict | None:
    """Optional evidence; never implies verified ownership or sale permission."""
    if not isinstance(allowed, frozenset) or not allowed:
        return None
    try:
        hostname = _host(url, allowed)
        if not _public_dns(hostname):
            return None
        # URL has passed the ordinary source allowlist and public DNS check.
        response = client.post(
            BRIDGE_URL,
            json={"url": url},
            timeout=httpx.Timeout(28, connect=2),
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        if (response.status_code != 200 or len(response.content) > MAX_BODY_BYTES
                or response.headers.get("content-type", "").split(";")[0]
                != "application/json"):
            return None
        result = response.json()
        if (type(result) is not dict
                or result.get("status") != "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED"
                or result.get("public_page_host") != hostname
                or result.get("crm_writes") != 0
                or result.get("model_calls") != 0
                or type(result.get("text")) is not str):
            return None
        page_text = " ".join(result["text"].split())[:MAX_TEXT_CHARS]
        if not page_text:
            return None
        return {
            "url": url,
            "text": page_text,
            "links": [],
            "emails": [],
            "tel": [],
            "employer": {},
            "browser_unverified": True,
        }
    except (httpx.HTTPError, ResearchBlocked, ValueError, TypeError):
        return None
