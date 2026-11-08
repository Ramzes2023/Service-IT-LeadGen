"""Optional isolated Chromium page renderer for owner-approved public websites.

This is NOT a lead collector, CRM worker, outbound-contact agent or evidence
certifier. It never receives Bitrix, DB, RouterAI or GitHub credentials.
BROWSER PROFILE OFF by default. Run only with approved public hostnames and
a separately network-isolated, nonroot browser container after operator review.

Only readable public text from one rendered page is returned in memory.
Nothing is ever sent to an LLM automatically. Javascript pages, robots.txt,
DNS, redirected requests and resource URLs must pass explicit boundaries.
Container-level egress restrictions are additionally required for production
because a checked DNS answer is NOT a pinned socket destination.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_public_evidence_research import (
    _public_dns, _robots, SAFE_HOST, ResearchBlocked,
)

MAX_HOSTS = 24
MAX_URL = 1000
MAX_REQUESTS = 35
MAX_OUTPUT = 10000
MAX_HTML = 160000


class BrowserBlocked(ValueError):
    pass


def approved_hosts(raw: str) -> frozenset[str]:
    if not isinstance(raw, str) or len(raw) > 3500:
        raise BrowserBlocked("BROWSER_ALLOWLIST_INVALID")
    hosts = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if not 1 <= len(hosts) <= MAX_HOSTS or any(
        not SAFE_HOST.fullmatch(h)
        or h.endswith((".test", ".invalid", ".local", ".localhost"))
        for h in hosts
    ):
        raise BrowserBlocked("BROWSER_ALLOWLIST_MISSING_OR_INVALID")
    return frozenset(hosts)


def verify_url(url: str, allowed: frozenset[str], *, dns_check=_public_dns) -> str:
    if type(url) is not str or not 12 <= len(url) <= MAX_URL:
        raise BrowserBlocked("BROWSER_URL_INVALID")
    try:
        uri = urlsplit(url)
        host = (uri.hostname or "").lower()
        port = uri.port
    except ValueError:
        raise BrowserBlocked("BROWSER_URL_INVALID") from None
    if (
        uri.scheme != "https" or not SAFE_HOST.fullmatch(host)
        or uri.username or uri.password or uri.fragment or port not in (None, 443)
        or any(ord(c) < 32 for c in url)
        or not any(host == h or host.endswith("." + h) for h in allowed)
        or host.endswith((".test", ".invalid", ".local", ".localhost"))
    ):
        raise BrowserBlocked("BROWSER_PUBLIC_HOST_NOT_APPROVED")
    if not dns_check(host):
        raise BrowserBlocked("BROWSER_DNS_NOT_PUBLIC")
    return host


def render_public(url: str, allowed: frozenset[str]) -> dict:
    verify_url(url, allowed)
    # Container has NO direct public network. The only egress goes through the
    # HTTPS-only proxy that resolves once, validates and pins a PUBLIC IP.
    proxy = os.environ.get("SERVICEIT_VPS_BROWSER_PROXY_URL", "")
    if proxy != "http://egress:3128":
        raise BrowserBlocked("BROWSER_PINNED_EGRESS_PROXY_REQUIRED")
    # Explicit robots policy must be accessible and permissive; never
    # substitute a browser for the ordinary verified HTTP source fetch.
    with httpx.Client(trust_env=False, follow_redirects=False, proxy=proxy) as client:
        try:
            _robots(client, url, allowed)
        except (ResearchBlocked, httpx.HTTPError):
            raise BrowserBlocked("BROWSER_ROBOTS_NOT_APPROVED") from None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserBlocked("BROWSER_PACKAGE_NOT_INSTALLED") from None

    count = 0
    try:
        with sync_playwright() as playwright:
            # Chromium sandbox MUST be enabled. If this fails on a container,
            # do NOT silently restart with --no-sandbox.
            browser = playwright.chromium.launch(
                headless=True,
                chromium_sandbox=True,
                proxy={"server": proxy},
                args=["--disable-background-networking", "--disable-sync"],
                timeout=12000,
            )
            try:
                context = browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    permissions=[],
                    java_script_enabled=True,
                    ignore_https_errors=False,
                    locale="ru-RU",
                )
                def route_guard(route):
                    nonlocal count
                    count += 1
                    if count > MAX_REQUESTS:
                        route.abort()
                        return
                    try:
                        verify_url(route.request.url, allowed)
                        route.continue_()
                    except BrowserBlocked:
                        route.abort()

                context.route("**/*", route_guard)
                page = context.new_page()
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=15000,
                )
                if response is None or response.status != 200:
                    raise BrowserBlocked("BROWSER_PAGE_NOT_200")
                final_host = verify_url(page.url, allowed)
                if response.headers.get("content-type", "").split(";",1)[0] not in (
                    "text/html", "application/xhtml+xml"
                ):
                    raise BrowserBlocked("BROWSER_RESPONSE_NOT_HTML")
                # Browser never clicks, logs in, submits forms, downloads,
                # scrapes private pages or follows automation instructions.
                text = page.locator("body").inner_text(timeout=4000)
                if not text:
                    raise BrowserBlocked("BROWSER_EMPTY_PUBLIC_TEXT")
                result = {
                    "status": "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED",
                    "public_page_host": final_host,
                    "text": " ".join(text.split())[:MAX_OUTPUT],
                    "requests_checked": min(count, MAX_REQUESTS),
                    "crm_writes": 0, "model_calls": 0,
                    "external_contacts": 0,
                    "company_identity_verified": False,
                    "contacts_authorized": False,
                }
                context.close()
                return result
            finally:
                browser.close()
    except BrowserBlocked:
        raise
    except Exception:
        raise BrowserBlocked("BROWSER_RENDER_UNVERIFIED") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--selfcheck", action="store_true")
    modes.add_argument("--url")
    args = parser.parse_args(argv)
    try:
        if args.selfcheck:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                executable = Path(p.chromium.executable_path)
                if not executable.is_file():
                    raise BrowserBlocked("BROWSER_EXECUTABLE_NOT_FOUND")
            result = {
                "status": "CHROMIUM_BINARY_AVAILABLE_NOT_NETWORK_VALIDATED",
                "crm_writes": 0, "model_calls": 0, "external_contacts": 0,
                "requests": 0,
            }
        else:
            allowed = approved_hosts(
                os.environ.get("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS", ""))
            result = render_public(args.url, allowed)
            # Never log scraped page body or contacts to GitHub, shell, or CI.
            result = {
                "status": result["status"],
                "public_text_chars": len(result["text"]),
                "public_text_sha256": hashlib.sha256(
                    result["text"].encode("utf8")).hexdigest(),
                "requests_checked": result["requests_checked"],
                "crm_writes": 0, "model_calls": 0, "external_contacts": 0,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BrowserBlocked, ImportError):
        print(json.dumps({
            "status": "CHROMIUM_BROWSER_BLOCKED_OR_UNVERIFIED",
            "crm_writes": 0, "model_calls": 0, "external_contacts": 0,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
