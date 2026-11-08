"""Owner-only disposable Brave 422 differential: full vs minimal Web Search.

No CRM/AI/DB writes, no PII, no contact discovery and no retries.
One normal Web call; if and only if HTTP 422, one minimum-parameter Web call.
Never prints or logs token, raw API response, request URL, search results,
headers, exception details or HTTP response text.
"""
from __future__ import annotations

import json
import os

import httpx

from leadpilot.serviceit_vps_pg_queue import VpsBlocked, secret_file

URL = "https://api.search.brave.com/res/v1/web/search"
# Match the exact public search phrase that returned HTTP 200 on Windows.
# Comparing identical queries isolates host/network/API-key differences.
QUERY = "brave search"
FULL = {
    "q": QUERY,
    "count": 2,
    "country": "RU",
    "search_lang": "ru",
    "safesearch": "strict",
}
MINIMAL = {"q": QUERY}
ALLOWED_ERROR_CODES = frozenset((
    "VALIDATION", "QUOTA_LIMITED", "RATE_LIMITED", "UNAUTHORIZED",
    "INVALID_API_KEY", "SUBSCRIPTION_NOT_FOUND", "INVALID_SUBSCRIPTION",
    "FORBIDDEN", "BAD_REQUEST", "INVALID_REQUEST", "NOT_FOUND",
))
ALLOWED_LOCATIONS = frozenset((
    ("query", "q"), ("query", "country"), ("query", "search_lang"),
    ("query", "safesearch"), ("query", "count"),
    ("header", "cache-control"), ("header", "x-subscription-token"),
    ("header", "accept"), ("header", "api-version"),
))


def request(client: httpx.Client, key: str, params: dict) -> dict:
    try:
        response = client.get(
            URL,
            params=params,
            headers={
                "X-Subscription-Token": key,
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                # Match the explicit Windows request header.
                "Accept-Encoding": "gzip",
            },
            follow_redirects=False,
            timeout=15.0,
        )
        status = response.status_code
        if status == 200:
            return {"status": "HTTP_200", "code": "NONE", "field": "NONE"}
        if status != 422:
            known = {400, 401, 402, 403, 404, 408, 429, 500, 502, 503, 504}
            return {
                "status": "HTTP_" + str(status) if status in known else "HTTP_OTHER",
                "code": "UNKNOWN", "field": "UNKNOWN",
            }
        output = {"status": "HTTP_422", "code": "UNKNOWN", "field": "UNKNOWN"}
        if not 0 < len(response.content) <= 10000:
            return output
        if "application/json" not in response.headers.get("content-type", ""):
            return output
        payload = response.json()
        if type(payload) is not dict or type(payload.get("error")) is not dict:
            return output
        error = payload["error"]
        if type(error.get("code")) is str and error["code"] in ALLOWED_ERROR_CODES:
            output["code"] = error["code"]
        meta = error.get("meta")
        if type(meta) is dict and type(meta.get("errors")) is list:
            errors = meta["errors"]
            if len(errors) <= 10:
                for entry in errors:
                    if type(entry) is not dict or type(entry.get("loc")) is not list:
                        continue
                    loc = entry["loc"]
                    if (len(loc) == 2 and all(type(x) is str for x in loc)
                            and tuple(loc) in ALLOWED_LOCATIONS):
                        output["field"] = loc[0].upper() + "_" + loc[1].upper().replace("-", "_")
                        break
        return output
    except (httpx.HTTPError, ValueError, TypeError):
        return {"status": "TRANSPORT_OR_RESPONSE_ERROR", "code": "UNKNOWN", "field": "UNKNOWN"}


def inspect(env: dict, client: httpx.Client) -> dict:
    try:
        key = secret_file(env, "SERVICEIT_BRAVE_SEARCH_API_KEY")
    except (VpsBlocked, OSError, ValueError):
        return {"status": "SECRET_NOT_ACCESSIBLE", "full": {"status": "NOT_CALLED"}, "minimal": {"status": "NOT_CALLED"}, "requests_max": 0, "crm_writes": 0, "model_calls": 0}
    if not 12 <= len(key) <= 200 or not key.isascii():
        return {"status": "KEY_FORMAT_INVALID", "full": {"status": "NOT_CALLED"}, "minimal": {"status": "NOT_CALLED"}, "requests_max": 0, "crm_writes": 0, "model_calls": 0}
    original = request(client, key, FULL)
    if original["status"] == "HTTP_422":
        minimal = request(client, key, MINIMAL)
        count = 2
    else:
        minimal = {"status": "NOT_CALLED", "code": "NONE", "field": "NONE"}
        count = 1
    return {
        "status": "DIAG_COMPLETE_NO_LIVE_CHANGE",
        "full": original, "minimal": minimal, "requests_max": count,
        "crm_writes": 0, "model_calls": 0,
    }


def main() -> int:
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            info = inspect(dict(os.environ), client)
    except Exception:
        info = {
            "status": "DIAG_INTERNAL_ERROR",
            "full": {"status": "NOT_CALLED", "code": "UNKNOWN", "field": "UNKNOWN"},
            "minimal": {"status": "NOT_CALLED", "code": "UNKNOWN", "field": "UNKNOWN"},
            "requests_max": 2, "crm_writes": 0, "model_calls": 0,
        }
    print(json.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
