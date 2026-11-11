#!/usr/bin/env python3
"""No-write, no-paid-source V21 audit: exact four B2B original notice URLs.

Reads signed PG inbox and fetches robots.txt only on reviewed public hosts.
If and ONLY if robots permits the URL, reads the public detail HTML and
counts same-host, publicly linked candidate attachments. Does not download
documents or invoke RouterAI, Brave, browser, mail or Bitrix update.
All printed output is fixed enum/count status: no tender IDs, URLs, secrets.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from leadpilot.serviceit_vps_worker import live_permitted, INTERVAL_SECONDS
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_b2b_v19_backfill import select_one
from leadpilot.serviceit_public_evidence_research import (
    AGENT, VisibleText, _host, _public_dns, _get, ResearchBlocked,
)
from leadpilot.serviceit_b2b_tender_documents import extension

HOSTS = frozenset(("b2b-center.ru",))
PROOF_ID = re.compile(r"(?<!\d){}(?!\d)")
LABELS = ("скачать", "документ", "тз", "файл", "download")
SAFE_REASONS = frozenset((
    "ROBOTS_200_ALLOW", "ROBOTS_200_DISALLOW", "ROBOTS_HTTP_401",
    "ROBOTS_HTTP_403", "ROBOTS_HTTP_429", "ROBOTS_HTTP_OTHER",
    "ROBOTS_NETWORK", "ROBOTS_INVALID", "DETAIL_READABLE",
    "DETAIL_DENIED", "DETAIL_NO_EXACT_ID", "SOURCE_INVALID",
))
def say(name, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,75}", name)
    assert re.fullmatch(r"(?:YES|NO|[A-Z0-9_]{4,75}|[0-9]{1,3})", str(value))
    print("V21_SOURCE_" + name + "=" + str(value), flush=True)


def robots_probe(client, url):
    """No source GET unless user-agent robots is fetched and explicitly allows."""
    try:
        host = _host(url, HOSTS)
        if not _public_dns(host):
            return "ROBOTS_INVALID", False
        u = urlsplit(url)
        robots = u.scheme + "://" + u.netloc + "/robots.txt"
        response = client.get(
            robots, timeout=9, follow_redirects=False,
            headers={"User-Agent": AGENT, "Accept": "text/plain"},
        )
    except (ResearchBlocked, ValueError, httpx.HTTPError):
        return "ROBOTS_NETWORK", False
    if response.status_code != 200:
        category = {401:"ROBOTS_HTTP_401",403:"ROBOTS_HTTP_403",
                    429:"ROBOTS_HTTP_429"}.get(
                        response.status_code,"ROBOTS_HTTP_OTHER")
        return category, False
    if not 0 < len(response.content) <= 80000:
        return "ROBOTS_INVALID", False
    try:
        parser = RobotFileParser()
        parser.parse(response.text[:20000].splitlines())
        approved = parser.can_fetch(AGENT, url)
    except (TypeError, ValueError):
        return "ROBOTS_INVALID", False
    return ("ROBOTS_200_ALLOW" if approved else "ROBOTS_200_DISALLOW",
            approved)


def same_host_public_links(raw, url):
    parser = VisibleText()
    parser.feed(raw)
    text = " ".join(parser.parts)
    source_host = _host(url, HOSTS)
    links = []
    for href, label in parser.links[:150]:
        if type(href) is not str or len(href) > 900:
            continue
        candidate = urljoin(url, href)
        try:
            if _host(candidate, HOSTS) != source_host:
                continue
            ext = extension(urlsplit(candidate).path)
            dynamic = (bool(re.search(r"(?i)(?:download|attachment|file)", candidate))
                       and any(w in label for w in LABELS))
            if (ext or dynamic) and candidate != url:
                links.append(candidate)
        except ResearchBlocked:
            continue
    return text, len(set(links))


def inspect(client, url, tender_id):
    label, permitted = robots_probe(client, url)
    if not permitted:
        return label, "NO", 0
    try:
        page = _get(client, url, HOSTS)
        text, count = same_host_public_links(page, url)
    except (ResearchBlocked, ValueError, httpx.HTTPError, TypeError):
        return label, "NO", 0
    match = re.search(r"(?<!\d)" + re.escape(tender_id) + r"(?!\d)", text)
    return label, "YES" if match else "NO", min(99,count) if match else 0


def main():
    env = dict(os.environ)
    assert live_permitted(env) and INTERVAL_SECONDS==300
    with db(env) as connection:
      with connection.transaction():
       with connection.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cards = select_one(connection, preview=True)
        assert len(cards)==4
        say("SIGNED_FOUR","YES")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            for i, (card,source,notice) in enumerate(cards,1):
                valid = (isinstance(notice,dict) and
                         isinstance(source,str) and
                         re.fullmatch(r"B2B-CENTER-[0-9]{5,12}",source) and
                         notice.get("tender_id")==source.removeprefix("B2B-CENTER-")
                         and notice.get("source")=="B2B-Center" and
                         isinstance(notice.get("source_url"),str))
                if valid:
                    try:
                        _host(notice["source_url"],HOSTS)
                    except ResearchBlocked:
                        valid=False
                if not valid:
                    say(f"CARD_{i}_ROBOTS","SOURCE_INVALID")
                    say(f"CARD_{i}_SOURCE_EXACT_ID","NO")
                    say(f"CARD_{i}_PUBLIC_DOC_LINKS",0)
                    continue
                reason, match, count = inspect(
                    client, notice["source_url"], notice["tender_id"])
                assert reason in SAFE_REASONS
                say(f"CARD_{i}_ROBOTS",reason)
                say(f"CARD_{i}_SOURCE_EXACT_ID",match)
                say(f"CARD_{i}_PUBLIC_DOC_LINKS",count)
    say("CRM_WRITES","NO")
    say("PAID_AI_CALLS","NO")
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        say("AUDIT_BLOCKED","YES")
        raise SystemExit(2)
