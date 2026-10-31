"""Bitrix24 connection diagnostics that NEVER writes to CRM.
Use only an authorized test-portal webhook; redact URL and response errors.
"""
import os
from urllib.parse import urlparse
import httpx


def _base_url(value):
    parsed = urlparse(value.rstrip("/"))
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.query or parsed.fragment:
        raise ValueError("HTTPS webhook URL required")
    if not any(hostname.endswith(x) for x in (".bitrix24.ru", ".bitrix24.com", ".bitrix24.kz", ".bitrix24.by")):
        raise ValueError("Only *.bitrix24 official test portal supported in this preflight")
    if "/rest/" not in parsed.path:
        raise ValueError("Expected Bitrix24 REST webhook URL")
    return value.rstrip("/")


def readonly_preflight(webhook=None, client=None):
    """Only API reads, no user data returned to logs."""
    base = _base_url(webhook or os.getenv("BITRIX24_TEST_WEBHOOK", ""))
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    methods = ["crm.settings.mode.get", "crm.item.fields"]
    results = {}
    try:
        for method in methods:
            try:
                response = client.post(base + "/" + method,
                            json={"entityTypeId": 1} if method == "crm.item.fields" else {})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or "error" in data or "result" not in data:
                    results[method] = "API_ERROR_OR_PERMISSION_MISSING"
                else:
                    results[method] = "READ_OK"
            except (httpx.HTTPError, ValueError):
                results[method] = "HTTP_OR_RESPONSE_ERROR"
        return {"mode": "READ_ONLY", "crm_writes": False, "methods": results}
    finally:
        if own:
            client.close()
