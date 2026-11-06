"""Permission-gated, read-only probe of Bidzaar's 2024 *aggregator* purchases API.

Distinct from the buyer Connector API and 2026 supplier-profile API.
The documented integrator ID must be independently issued by Bidzaar.
This probe deliberately does NOT persist tender bodies or qualify leads.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

ROOT = "https://bidzaar.com/api/aggregator_integrator/"
MAX_BODY_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 12
MAX_PAGE_SIZE = 100


class FeedBlocked(RuntimeError):
    """Error suitable for display: no URL, token or response body."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _identifier(raw: str) -> str:
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", raw):
        raise FeedBlocked("INVALID_INTEGRATOR_ID")
    try:
        parsed = UUID(raw)
    except ValueError:
        raise FeedBlocked("INVALID_INTEGRATOR_ID") from None
    if str(parsed).casefold() != raw.casefold():
        raise FeedBlocked("INVALID_INTEGRATOR_ID")
    return str(parsed)


def _since_utc(raw: str | None, now: datetime) -> str:
    if now.tzinfo is None:
        raise FeedBlocked("CLOCK_NEEDS_TIMEZONE")
    current = now.astimezone(timezone.utc)
    if raw is None:
        dt = current - timedelta(days=1)
    else:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise FeedBlocked("INVALID_FROM_DATE") from None
        if dt.tzinfo is None:
            raise FeedBlocked("FROM_DATE_NEEDS_TIMEZONE")
        dt = dt.astimezone(timezone.utc)
    if dt > current or dt < current - timedelta(days=13):
        raise FeedBlocked("FROM_DATE_OUT_OF_SAFE_WINDOW")
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_request(integrator_id: str, *, page_size: int = 25,
                  from_date: str | None = None,
                  now: datetime | None = None) -> Request:
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise FeedBlocked("INVALID_PAGE_SIZE")
    when = now or datetime.now(timezone.utc)
    uuid = _identifier(integrator_id)
    query = urlencode({"PageNumber": 0, "PageSize": page_size,
                       "FromDate": _since_utc(from_date, when)})
    url = f"{ROOT}{uuid}/procedures/purchases?{query}"
    return Request(url,
                   headers={"Accept": "application/json", "User-Agent": "ServiceITLeadGen-API-Check/1.0"},
                   method="GET")


def _read_publications(request: Request, *, opener=None) -> list[dict]:
    opener = opener or build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise FeedBlocked("NON_200_RESPONSE")
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise FeedBlocked("NON_JSON_RESPONSE")
            content = response.read(MAX_BODY_BYTES + 1)
    except HTTPError as exc:
        # Error URL may contain the integrator ID: never echo it.
        if exc.code in (301, 302, 303, 307, 308, 401, 403, 407, 429):
            raise FeedBlocked(f"ACCESS_OR_RATE_BLOCKED_HTTP_{exc.code}") from None
        raise FeedBlocked(f"HTTP_{exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise FeedBlocked("NETWORK_UNAVAILABLE") from None
    if len(content) > MAX_BODY_BYTES:
        raise FeedBlocked("RESPONSE_TOO_LARGE")
    try:
        result = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise FeedBlocked("BAD_JSON_RESPONSE") from None
    if not isinstance(result, list) or not all(isinstance(it, dict) for it in result):
        raise FeedBlocked("UNVERIFIED_RESPONSE_SCHEMA")
    return result


def probe(*, integrator_id: str | None, permission: str | None,
          from_date: str | None = None, page_size: int = 25,
          now: datetime | None = None, opener=None) -> dict:
    if permission != "YES":
        raise FeedBlocked("ENTITLEMENT_NOT_CONFIRMED")
    if not integrator_id:
        raise FeedBlocked("INTEGRATOR_ID_NOT_ISSUED")
    when = now or datetime.now(timezone.utc)
    req = build_request(integrator_id, page_size=page_size, from_date=from_date, now=when)
    rows = _read_publications(req, opener=opener)
    safe_fields = sorted(set.intersection(*(set(x.keys()) for x in rows))) if rows else []
    return {
        "source": "bidzaar_aggregator_integrator_2024",
        "verification": "LIVE_HTTP_RESPONSE_RECEIVED_NOT_QUALIFIED",
        "observed_at": when.astimezone(timezone.utc).isoformat(),
        "http_status": 200, "raw_records_received": len(rows),
        "common_top_level_fields": safe_fields[:80],
        "qualified_real_leads": 0, "crm_writes": "NO", "outreach": "NO",
        "notice": "Schema, entitlement and storage terms require independent review; tender body not stored.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gated read-only Bidzaar aggregator API probe")
    p.add_argument("--page-size", type=int, default=25)
    p.add_argument("--from-date", default=None, help="ISO8601 timestamp within last 13 days")
    args = p.parse_args(argv)
    try:
        summary = probe(integrator_id=os.environ.get("BIDZAAR_AGGREGATOR_ID"),
                        permission=os.environ.get("BIDZAAR_AGGREGATOR_ALLOWED"),
                        from_date=args.from_date, page_size=args.page_size)
    except FeedBlocked as err:
        print(json.dumps({"source": "bidzaar_aggregator_integrator_2024",
                          "live_fetch": "BLOCKED", "reason": str(err),
                          "qualified_real_leads": 0, "crm_writes": "NO"}))
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
