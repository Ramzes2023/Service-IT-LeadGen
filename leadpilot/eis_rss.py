"""Read-only EIS procurement RSS research adapter for the Service IT 1C pilot.

No company identity or SME category is inferred from a procurement notice. No CRM,
AI, contacts, outbound, or automatic lead qualification is performed.

The official search RSS URL must be copied from the EIS user interface; the
adapter deliberately does not fabricate undocumented portal query parameters.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import ssl
from urllib.parse import parse_qs, urlsplit

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
import httpx

EIS_HOST = "zakupki.gov.ru"
FEED_PATHS = {"/epz/order/extendedsearch/rss", "/epz/order/extendedsearch/rss.html"}
SOURCE_NAME = "eis-zakupki-gov-ru-official-rss"
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 500
MAX_AGE_DAYS = 30
SERVICE_WORDS = ("сопровожд", "поддержк", "доработ", "настройк", "внедрен", "обслуживан", "модификац", "консультационн")


def _url(raw: object, *, feed: bool) -> str:
    """Exact official host, no redirects or user-controlled network destinations."""
    if not isinstance(raw, str) or not 15 <= len(raw) <= 4000 or re.search(r"[\s\\\x00-\x1f]", raw):
        raise ValueError("EIS_URL_INVALID")
    try:
        # RSS may use official site-root notice links: never protocol-relative.
        if not feed and raw.startswith("/") and not raw.startswith("//"):
            raw = "https://" + EIS_HOST + raw
        u = urlsplit(raw)
        if (u.scheme != "https" or u.hostname != EIS_HOST or u.username or u.password
                or u.port not in (None, 443) or u.fragment):
            raise ValueError("EIS_URL_NOT_OFFICIAL_HTTPS")
        if feed:
            if u.path not in FEED_PATHS or not u.query:
                raise ValueError("EIS_RSS_SEARCH_URL_REQUIRED")
            return raw
        valid_44 = u.path.startswith("/epz/order/notice/") and u.path.endswith(".html")
        valid_223 = (u.path.startswith("/223/purchase/public/purchase/info/")
                     and u.path.endswith(".html"))
        if not (valid_44 or valid_223):
            raise ValueError("EIS_NOTICE_URL_REQUIRED")
        params = parse_qs(u.query, keep_blank_values=False)
        valid_number = any(
            len(params.get(key, [])) == 1 and bool(re.fullmatch(r"\d{6,30}", params[key][0]))
            for key in ("regNumber", "noticeInfoId")
        )
        if not valid_number:
            raise ValueError("EIS_NOTICE_ID_REQUIRED")
        return raw
    except ValueError as exc:
        if str(exc).startswith("EIS_"):
            raise
        raise ValueError("EIS_URL_INVALID") from None


def _plain(raw: str, max_chars: int = 1200) -> str:
    # EIS escapes its HTML fragments inside RSS XML, sometimes twice.
    text = unescape(unescape(raw or ""))
    return " ".join(re.sub(r"<[^>]*>", " ", text).split())[:max_chars]


_SUBJECT_LABEL = (
    r"(?:Наименование объекта закупки|Наименование предмета договора|"
    r"Наименование закупки|Предмет закупки|Предмет договора)"
)


def _official_subject(raw_description: str) -> str | None:
    """Read ONLY the labelled procurement subject, never the search query.

    Real EIS RSS titles are often 'Электронный аукцион №...' while the
    procurement subject is an HTML-escaped labelled field in description.
    None = absent; empty = explicitly null/unspecified.
    """
    fragment = unescape(unescape(raw_description or ""))[:25000]
    found = re.search(
        _SUBJECT_LABEL + r"\s*:\s*(?:</(?:strong|b|span)>\s*)*(.*?)(?=<br\s*/?>|</p>|$)",
        fragment, flags=re.IGNORECASE | re.DOTALL,
    )
    if not found:
        return None
    subject = _plain(found.group(1), 700)
    return "" if subject.casefold() in {"null", "none", "не указано", "-"} else subject


def _text(node, tag: str) -> str:
    for child in node:
        if child.tag.rsplit("}", 1)[-1].lower() == tag.lower():
            return "".join(child.itertext()).strip()
    return ""


def _parse_publication(raw: str) -> str | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return None
        return dt.date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.date().isoformat() if dt.tzinfo else None
        except (TypeError, ValueError):
            return None


def _is_1c_external_service(title: str, subject: str | None) -> bool:
    # A labelled object of procurement overrides a generic auction RSS title.
    # Forbid matching the irrelevant EIS 'Искомое слово' field.
    source = subject if subject is not None else title
    if subject is None and re.match(
            r"^(?:Электронный аукцион|Запрос котировок|Закупка, осуществляемая|"
            r"Открытый конкурс|Запрос предложений)\\b", title, flags=re.I):
        return False
    t = _plain(source, 700).casefold().replace("ё", "е")
    if not t or "битрикс" in t:
        return False
    if re.search(r"\\b(?:без|не\\s+предусмотрен[а-я]*)\\s+(?:оказания\\s+)?услуг\\s+(?:по\\s+)?(?:сопровожд|поддержк|внедрен|настройк)", t):
        return False
    return (bool(re.search(r"(?<![a-zа-я0-9])1[сc](?![a-zа-я0-9])", t))
            and any(word in t for word in SERVICE_WORDS))


def parse_feed(xml: bytes, *, feed_url: str, today: date | None = None) -> dict:
    """Parse public EIS RSS/Atom to a research-only, deduplicated audit snapshot."""
    _url(feed_url, feed=True)
    if not isinstance(xml, bytes) or not (20 <= len(xml) <= MAX_FEED_BYTES):
        raise ValueError("EIS_FEED_SIZE_INVALID")
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException, ValueError):
        raise ValueError("EIS_FEED_XML_INVALID") from None
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name == "rss":
        channel = next((c for c in root if c.tag.rsplit("}", 1)[-1] == "channel"), None)
        if channel is None:
            raise ValueError("EIS_RSS_CHANNEL_MISSING")
        entries = [c for c in channel if c.tag.rsplit("}", 1)[-1] == "item"]
    elif root_name == "feed":
        entries = [c for c in root if c.tag.rsplit("}", 1)[-1] == "entry"]
    else:
        raise ValueError("EIS_FEED_FORMAT_UNKNOWN")
    if len(entries) > MAX_ITEMS:
        raise ValueError("EIS_FEED_TOO_MANY_ITEMS")
    today = today or date.today()
    counts = Counter()
    unique = {}
    for item in entries:
        counts["items_seen"] += 1
        title = _plain(_text(item, "title"), 700)
        raw_description = _text(item, "description") or _text(item, "summary")
        subject = _official_subject(raw_description)
        if not _is_1c_external_service(title, subject):
            counts["not_explicit_external_1c_service"] += 1
            continue
        link = _text(item, "link")
        if not link:
            link = next((c.attrib.get("href", "") for c in item
                         if c.tag.rsplit("}", 1)[-1] == "link"), "")
        try:
            link = _url(link, feed=False)
        except ValueError:
            counts["unverified_notice_link"] += 1
            continue
        published = _parse_publication(_text(item, "pubDate") or _text(item, "published") or _text(item, "updated"))
        if published is None:
            counts["publication_date_unknown"] += 1
            status = "DATE_UNKNOWN_HOLD"
        else:
            days = (today - date.fromisoformat(published)).days
            if days < 0:
                counts["future_date_rejected"] += 1
                continue
            if days > MAX_AGE_DAYS:
                counts["stale_rejected"] += 1
                continue
            status = "EIS_1C_EXTERNAL_SERVICE_RESEARCH_ONLY"
        uid = hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]
        if uid in unique:
            counts["duplicate_notices"] += 1
            continue
        unique[uid] = {
            "source_name": SOURCE_NAME,
            "source_url": link,
            "signal_id": uid,
            "title": subject if subject else title,
            "rss_title": title,
            "procurement_subject_from_rss": subject,
            "buyer_name_rss_unverified": _plain(_text(item, "author"), 250) or None,
            "evidence_excerpt": (subject if subject else title)[:650],
            "published_on": published,
            "discovered_on": today.isoformat(),
            "research_status": status,
            "service_hypothesis": "1C maintenance / implementation",
            "buyer_inn": None,
            "buyer_region": "UNVERIFIED",
            "buyer_fns_size": "UNVERIFIED",
            "procurement_status": "UNVERIFIED",
            "deadline_on": None,
            "official_notice_checked": False,
            "manager_approved": False,
            "crm_ready": False,
            "outbound_authorized": False,
            "lead_score": None,
        }
    counts["candidates_for_identity_review"] = len(unique)
    counts["qualified_small_business_leads"] = 0
    counts["crm_ready"] = 0
    return {
        "mode": "EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        "source": SOURCE_NAME,
        "search_rss_url": feed_url,
        "checked_on": today.isoformat(),
        "counts": dict(counts),
        "items": list(unique.values()),
        "gate": "OFFICIAL_NOTICE_BUYER_INN_FNS_REGION_DEADLINE_SCOPE_CONTACT_CRM_DUPLICATES",
        "warning": "EIS procurement buyers are NOT presumed small businesses; 44-FZ/223-FZ supplier SME status is not buyer size. A tender is not permission to contact.",
        "crm_writes": "NO", "outbound_messages": "NO", "valyqon_changes": "NONE",
    }


def fetch(*, url: str, client: httpx.Client | None = None, today: date | None = None, ca_bundle: str | None = None) -> dict:
    """Exactly one authorized official GET. No SSL bypass or redirect following."""
    _url(url, feed=True)
    own = client is None
    if own:
        verify = True
        if ca_bundle is not None:
            p = Path(ca_bundle)
            if (not p.is_file() or p.suffix.lower() not in (".pem", ".crt")
                    or not 1 <= p.stat().st_size <= 512 * 1024):
                raise ValueError("EIS_CA_BUNDLE_INVALID")
            try:
                verify = ssl.create_default_context(cafile=str(p))
            except (OSError, ssl.SSLError):
                raise ValueError("EIS_CA_BUNDLE_INVALID") from None
        client = httpx.Client(timeout=20.0, follow_redirects=False,
                              trust_env=False, verify=verify)
    try:
        try:
            with client.stream("GET", url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml",
                                                    "User-Agent": "ServiceITLeadGen-EISResearch/0.1"}) as response:
                if response.status_code != 200:
                    raise RuntimeError("EIS_SOURCE_UNAVAILABLE_OR_REDIRECT")
                media = response.headers.get("content-type", "").lower()
                if not any(x in media for x in ("xml", "rss", "atom")):
                    raise RuntimeError("EIS_RESPONSE_NOT_XML")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_FEED_BYTES:
                        raise RuntimeError("EIS_FEED_TOO_LARGE")
        except httpx.HTTPError:
            raise RuntimeError("EIS_TRANSPORT_OR_TLS_ERROR") from None
        return parse_feed(bytes(body), feed_url=url, today=today)
    finally:
        if own:
            client.close()


def save(report: dict, output: str | Path) -> Path:
    out = Path(output).resolve()
    root = Path("exports").resolve()
    if not out.is_relative_to(root) or out.suffix.lower() != ".json" or out.exists():
        raise ValueError("EIS_OUTPUT_MUST_BE_NEW_JSON_IN_EXPORTS")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Official EIS RSS research only; never CRM ready")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--url", help="Exact official RSS link copied from EIS search interface")
    modes.add_argument("--rss-file", help="Previously saved original official EIS RSS XML")
    parser.add_argument("--feed-url", help="Original EIS RSS URL, mandatory with --rss-file")
    parser.add_argument("--ca-bundle", help="Optional trusted PEM CA bundle from official source; TLS checks remain enabled")
    parser.add_argument("--output", default="exports/eis_rss_research.json")
    args = parser.parse_args()
    try:
        if args.url:
            if args.feed_url:
                raise ValueError("EIS_EXTRA_FEED_URL_FORBIDDEN")
            report = fetch(url=args.url, ca_bundle=args.ca_bundle)
        else:
            if args.ca_bundle:
                raise ValueError("EIS_CA_BUNDLE_NOT_FOR_OFFLINE_XML")
            if not args.feed_url:
                raise ValueError("EIS_ORIGINAL_FEED_URL_REQUIRED")
            source = Path(args.rss_file)
            if not source.is_file() or not 20 <= source.stat().st_size <= MAX_FEED_BYTES:
                raise ValueError("EIS_LOCAL_XML_MISSING_OR_TOO_LARGE")
            report = parse_feed(source.read_bytes(), feed_url=args.feed_url)
        output = save(report, args.output)
    except (ValueError, RuntimeError) as exc:
        print("EIS_STATUS=NOT_CONNECTED; ERROR_CODE=" + str(exc).split("\n")[0][:90])
        raise SystemExit(2)
    print("EIS_STATUS=OFFICIAL_FEED_PARSED; RESEARCH_ONLY=YES; CANDIDATES="
          + str(report["counts"]["candidates_for_identity_review"])
          + "; QUALIFIED_LEADS=0; CRM_WRITES=NO; OUTBOUND=NO")
    print("OUTPUT=" + str(output))


if __name__ == "__main__":
    main()
