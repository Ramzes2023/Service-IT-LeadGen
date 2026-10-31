"""Public 1CLancer task-only RSS -> research queue, not qualified B2B leads.

Only the publicly advertised task RSS is fetched. No user accounts, contact
scraping, private messages, task page traversal, Bitrix, emails or outreach.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
import httpx

RSS_URL = "https://1clancer.ru/i/pics/rss/main.xml"
SOURCE = "1clancer-public-tasks-rss"
MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 500
RECENCY_DAYS = 21
PUBLIC_TASK_DOMAINS = {"1clancer.ru", "1lancer.ru"}
CATEGORIES = {
    "1C_SUPPORT": ("сопровожд", "поддержк", "обновлен", "обновлени", "обслуживан"),
    "1C_DEVELOPMENT": ("доработ", "разработ", "обработк", "отчет", "отчёт", "печатн", "расширен"),
    "1C_INTEGRATIONS": ("интеграц", "обмен", "api", "xml", "эдо", "маркиров", "сайт", "маркетплейс"),
    "1C_SETUP": ("настройк", "внедрен", "установк", "перенос", "миграц", "конфигурац"),
}
CONTACT_EMAIL = re.compile(r"(?i)\b[\w.+'-]+@[\w.-]+\.[a-z]{2,}\b")
CONTACT_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s()\-]*\d[\d\s()\-]{8,15}\d(?!\d)")


def _tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _field(node, name: str) -> str:
    for child in node:
        if _tag(child.tag) == name.lower():
            return "".join(child.itertext()).strip() or child.attrib.get("href", "")
    return ""


def _text(raw: str, limit: int = 280) -> str:
    value = unescape(unescape(raw or ""))
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = " ".join(value.split())
    value = CONTACT_EMAIL.sub("[EMAIL_REDACTED]", value)
    value = CONTACT_PHONE.sub("[PHONE_REDACTED]", value)
    return value[:limit]


def _task_url(raw: str) -> tuple[str, str]:
    if not isinstance(raw, str) or re.search(r"[\s\\\x00-\x1f]", raw):
        raise ValueError("CLANCER_TASK_URL_INVALID")
    if raw.startswith("/") and not raw.startswith("//"):
        raw = "https://1clancer.ru" + raw
    try:
        u = urlsplit(raw)
        if (u.scheme not in ("http", "https") or u.hostname not in PUBLIC_TASK_DOMAINS
                or u.username or u.password or u.port not in (None, 443)
                or u.query or u.fragment):
            raise ValueError("CLANCER_TASK_URL_NOT_PUBLIC")
        m = re.fullmatch(r"/task/([1-9]\d{2,10})/?", u.path)
        if not m:
            raise ValueError("CLANCER_NOT_A_TASK")
        # Published RSS may contain legacy http:// task links. We do NOT fetch\n        # them over HTTP: canonicalize only an exact allowed host/task ID to\n        # HTTPS for human opening, while network RSS GET stays HTTPS-only.
        identifier = m.group(1)
        return "https://1clancer.ru/task/" + identifier, identifier
    except ValueError as err:
        if str(err).startswith("CLANCER_"):
            raise
        raise ValueError("CLANCER_TASK_URL_INVALID") from None


def _publication(raw: str) -> date | None:
    if not raw:
        return None
    try:
        published = parsedate_to_datetime(raw)
        if published.tzinfo is not None:
            return published.date()
    except (ValueError, TypeError, OverflowError, IndexError):
        pass
    try:
        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if published.tzinfo is not None:
            return published.date()
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _category(title: str, excerpt: str) -> list[str]:
    haystack = (title + " " + excerpt).casefold().replace("ё", "е")
    return sorted(k for k, words in CATEGORIES.items() if any(w in haystack for w in words))


def parse(xml: bytes, *, today: date | None = None) -> dict:
    if not isinstance(xml, bytes) or not 20 <= len(xml) <= MAX_BYTES:
        raise ValueError("CLANCER_RSS_SIZE_INVALID")
    try:
        root = ElementTree.fromstring(xml)
    except (DefusedXmlException, ElementTree.ParseError):
        raise ValueError("CLANCER_RSS_XML_INVALID") from None
    kind = _tag(root.tag)
    if kind == "rss":
        channel = next((x for x in root if _tag(x.tag) == "channel"), None)
        if channel is None:
            raise ValueError("CLANCER_RSS_CHANNEL_MISSING")
        entries = [x for x in channel if _tag(x.tag) == "item"]
    elif kind == "feed":
        entries = [x for x in root if _tag(x.tag) == "entry"]
    else:
        raise ValueError("CLANCER_RSS_ROOT_INVALID")
    if len(entries) > MAX_ITEMS:
        raise ValueError("CLANCER_RSS_TOO_MANY_ITEMS")
    now = today or date.today()
    counts = Counter(items_seen=len(entries))
    task_ids = set()
    items = []
    for entry in entries:
        title = _text(_field(entry, "title"), 280)
        raw_link = _field(entry, "link") or _field(entry, "guid")
        try:
            link, task_id = _task_url(raw_link)
        except ValueError:
            counts["non_task_or_untrusted_entry"] += 1
            continue
        if task_id in task_ids:
            counts["duplicate_task_entries"] += 1
            continue
        task_ids.add(task_id)
        published = _publication(_field(entry, "pubDate") or _field(entry, "published")
                                 or _field(entry, "updated") or _field(entry, "date"))
        if published is None:
            counts["publication_date_missing"] += 1
            continue
        days = (now - published).days
        if days < 0 or days > RECENCY_DAYS:
            counts["not_recent"] += 1
            continue
        if len(title) < 4:
            counts["title_missing"] += 1
            continue
        excerpt = _text(_field(entry, "description") or _field(entry, "summary"), 280)
        categories = _category(title, excerpt)
        items.append({
            "source": SOURCE, "task_id": task_id, "source_url": link,
            "stable_signal_id": hashlib.sha256(f"{SOURCE}|{task_id}".encode()).hexdigest()[:24],
            "title": title, "description_excerpt_public_redacted": excerpt,
            "published_on": published.isoformat(), "discovered_on": now.isoformat(),
            "service_hypotheses": categories,
            "task_status": "UNVERIFIED_OPEN_OR_CLOSED",
            "buyer_legal_name": None, "buyer_inn": None,
            "buyer_fns_size": "UNVERIFIED", "buyer_region": "UNVERIFIED",
            "buyer_company_contact": None,
            "evidence_status": "PUBLIC_TASK_RESEARCH_ONLY",
            "verification_holds": ["OPEN_TASK_STATUS", "LEGAL_BUYER_IDENTITY", "BUYER_INN",
                                   "FNS_MICRO_SMALL", "MOSCOW_MO_REGION", "COMPANY_CONTACT_RIGHTS",
                                   "DUPLICATES_STOPLIST", "MANAGER_REVIEW"],
            "qualified_lead": False, "crm_ready": False,
            "outbound_authorized": False,
        })
    counts["research_task_signals"] = len(items)
    counts["qualified_b2b_leads"] = 0
    counts["crm_ready"] = 0
    return {
        "mode": "OFFICIAL_1CLANCER_RSS_TASK_RESEARCH_ONLY",
        "source": SOURCE, "source_url": RSS_URL, "checked_on": now.isoformat(),
        "counts": dict(counts), "items": items,
        "warning": "Public task and individual handle do not prove registered company, region, FNS size, or permission to contact.",
        "crm_writes": "NO", "outbound_messages": "NO",
    }


def fetch(*, client: httpx.Client | None = None) -> dict:
    """One HTTPS GET of the publisher-advertised public RSS. Fail closed."""
    owns = client is None
    if owns:
        client = httpx.Client(timeout=18.0, follow_redirects=False, trust_env=False)
    try:
        try:
            with client.stream("GET", RSS_URL, headers={
                    "Accept": "application/rss+xml, application/xml, text/xml",
                    "User-Agent": "ServiceITLeadResearch/0.1"}) as resp:
                if resp.status_code != 200:
                    raise RuntimeError("CLANCER_RSS_HTTP_" + str(resp.status_code))
                content_type = resp.headers.get("content-type", "").lower()
                if not any(x in content_type for x in ("xml", "rss")):
                    raise RuntimeError("CLANCER_RSS_NOT_XML")
                body = bytearray()
                for part in resp.iter_bytes():
                    body.extend(part)
                    if len(body) > MAX_BYTES:
                        raise RuntimeError("CLANCER_RSS_TOO_LARGE")
        except httpx.TimeoutException:
            raise RuntimeError("CLANCER_RSS_TIMEOUT") from None
        except httpx.HTTPError:
            raise RuntimeError("CLANCER_RSS_NETWORK_ERROR") from None
        return parse(bytes(body))
    finally:
        if owns:
            client.close()


def _csv_safe(value: object) -> str:
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def save(report: dict, json_path: str | Path, csv_path: str | Path) -> tuple[Path, Path]:
    root = Path("exports").resolve()
    paths = Path(json_path).resolve(), Path(csv_path).resolve()
    if (paths[0] == paths[1] or paths[0].suffix.lower() != ".json"
            or paths[1].suffix.lower() != ".csv"
            or any(not p.is_relative_to(root) or p.exists() for p in paths)):
        raise ValueError("CLANCER_OUTPUT_MUST_BE_NEW_EXPORTS")
    root.mkdir(parents=True, exist_ok=True)
    with paths[0].open("x", encoding="utf-8") as dst:
        json.dump(report, dst, ensure_ascii=False, indent=2)
    columns = ("task_id", "published_on", "title", "source_url",
               "description_excerpt_public_redacted", "service_hypotheses",
               "task_status", "buyer_inn", "buyer_fns_size", "buyer_region",
               "evidence_status", "qualified_lead", "crm_ready")
    with paths[1].open("x", encoding="utf-8-sig", newline="") as dst:
        writer = csv.DictWriter(dst, columns)
        writer.writeheader()
        for item in report["items"]:
            row = {key: (";".join(item[key]) if isinstance(item[key], list) else item[key])
                   for key in columns}
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description="Official 1CLancer task RSS, research only")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--rss-file", help="Previously saved official XML for offline acceptance")
    ap.add_argument("--json", required=True)
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()
    try:
        if args.rss_file:
            path = Path(args.rss_file)
            if not path.is_file() or not 20 <= path.stat().st_size <= MAX_BYTES:
                raise ValueError("CLANCER_LOCAL_XML_INVALID")
            result = parse(path.read_bytes())
        else:
            result = fetch()
        files = save(result, args.json, args.csv)
    except (RuntimeError, ValueError, OSError, UnicodeError) as e:
        print("CLANCER_RSS=BLOCKED; REASON=" + str(e).splitlines()[0][:80])
        raise SystemExit(2)
    print("CLANCER_RSS=OFFICIAL_TASK_FEED_PARSED; RESEARCH_ONLY=YES; "
          + "COUNTS=" + json.dumps(result["counts"], ensure_ascii=True, sort_keys=True))
    print("CLANCER_JSON=" + str(files[0]))
    print("CLANCER_CSV=" + str(files[1]))
    print("CLANCER_B2B_LEADS=0; CRM_WRITES=NO; OUTBOUND=NO")


if __name__ == "__main__":
    main()
