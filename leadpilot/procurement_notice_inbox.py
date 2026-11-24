"""Read official vendor-generated email notices, not vendor websites.

This is a local, inbound-only research intake for B2B-Center, SUPPMAN and
ONLINECONTRACT. An .eml From header alone is not authenticated evidence:
all records remain HOLD. Never creates leads or sends messages.
"""
from __future__ import annotations

import argparse
import hashlib
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

MAX_EMAIL_BYTES = 256_000
MAX_EMAILS = 100
MAX_TEXT = 50_000
DOMAINS = {
    "b2b-center": ("b2b-center.ru",),
    "suppman": ("suppman.ru", "suppman.team"),
    "onlinecontract": ("onlinecontract.ru", "onlc.ru"),
}
GROUPS = {
    "1C": ("1с", "1c"),
    "IT_SUPPORT": ("ит-аутсорс", "it-аутсорс", "техподдержк", "поддержк пользователей", "обслуживани компьют"),
    "KKT_MARKING": ("ккт", "онлайн-касс", "маркировк", "честный знак", "офд"),
    "RETAIL_EQUIPMENT": ("торгового оборудован", "торговое оборудование", "сканер штрих", "терминал сбора данных"),
    "EDO_SIGNATURE": ("эдо", "криптопро", "электронн подпис", "диадок"),
    "CRM": ("битрикс24", "битрикс 24", "crm", "срм"),
    "SERVERS_NETWORKS": ("сервер", "локальн сет", "сетевой инфраструктур", "администрировани сет"),
    "INTEGRATION": ("интеграц", "обмен данными", "api", "автоматизац"),
    "BOTS_AI": ("чат-бот", "чатбот", "ии-агент", "искусственн интеллект", "ai-агент"),
}
URL_RE = re.compile(r'https://[^\s<>"\x27]{1,1500}', re.I)


class NoticeError(ValueError):
    pass


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links = [], []

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for name, value in attrs
                              if name == "href" and isinstance(value, str))


def _source(sender):
    address = parseaddr(str(sender))[1].lower()
    if address.count("@") != 1:
        return None
    domain = address.rsplit("@", 1)[-1]
    for source, allowed in DOMAINS.items():
        if any(domain == host or domain.endswith("." + host) for host in allowed):
            return source
    return None


def _notice_url(value, source):
    if not isinstance(value, str) or len(value) > 1500 or re.search(r"[\x00-\x20\\]", value):
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443) or url.fragment:
            return None
        if source == "b2b-center" and host in ("www.b2b-center.ru", "b2b-center.ru"):
            if re.fullmatch(r"/search/number/l\d+(?:-\d+){1,8}/?", url.path) and not url.query:
                return value
            if re.fullmatch(r"/app/market-next/[a-z0-9-]+/tender-[0-9]+/", url.path) and not url.query:
                return value
            if url.path == "/market/view.html" and set(parse_qs(url.query)) == {"id"}:
                ids = parse_qs(url.query).get("id", [])
                if len(ids) == 1 and re.fullmatch(r"\d{5,12}", ids[0]):
                    return value
        if source == "suppman" and host in ("suppman.ru", "www.suppman.ru"):
            if re.fullmatch(r"/lot/\d{1,12}/?", url.path) and not url.query:
                return value
        if source == "onlinecontract" and host in ("onlinecontract.ru", "www.onlinecontract.ru", "store.onlinecontract.ru"):
            # Link shape is an unverified candidate; NOT proof of current procurement.
            if url.path.startswith("/tenders/") and len(url.path.strip("/").split("/")) > 1 and not url.query:
                return value
    except ValueError:
        return None
    return None


def _text(msg):
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            continue
        if isinstance(content, str):
            (plain if part.get_content_type() == "text/plain" else html).append(content)
    # Both MIME alternatives matter: a link can exist only in an HTML href.
    body = "\n".join(plain)[:MAX_TEXT]
    if html:
        p = _HTML()
        p.feed("\n".join(html)[:MAX_TEXT])
        body = (body + "\n" + " ".join(p.text))[:MAX_TEXT]
        return body, p.links[:30]
    return body, []


def read_email(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_EMAIL_BYTES:
        raise NoticeError("EMAIL_MISSING_OR_TOO_LARGE")
    raw_bytes = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    fingerprint = hashlib.sha256(raw_bytes).hexdigest()
    source = _source(msg.get("From", ""))
    if not source:
        raise NoticeError("UNRECOGNIZED_SENDER_DOMAIN")
    subject = str(msg.get("Subject", ""))[:350]
    body, html_links = _text(msg)
    alltext = (subject + " " + body).casefold().replace("ё", "е")
    groups = sorted(group for group, terms in GROUPS.items() if any(term in alltext for term in terms))
    urls = []
    for link in [*html_links, *URL_RE.findall(body)]:
        url = _notice_url(link.rstrip(".,;)]}»>\"'"), source)
        if url and url not in urls:
            urls.append(url)
        if len(urls) == 30:
            break
    timestamp = None
    try:
        dt = parsedate_to_datetime(str(msg.get("Date")))
        if dt.tzinfo:
            timestamp = dt.isoformat()
    except (ValueError, TypeError, IndexError):
        pass
    return {
        "source": source,
        "email_sha256": fingerprint,
        "subject": subject,
        "message_date": timestamp,
        "original_notice_urls": urls,
        "service_categories": groups,
        "status": "HOLD_UNVERIFIED_EMAIL_NOTICE",
        "hold_reasons": ["SENDER_NOT_CRYPTOGRAPHICALLY_VERIFIED", "BUYER_INN_NOT_VERIFIED",
                         "DEADLINE_NOT_VERIFIED", "FNS_AND_STOP_LIST_PENDING",
                         "ORIGINAL_NOTICE_NOT_LIVE_VERIFIED"],
        "qualified_real_leads": 0,
    }


def audit(paths):
    if not 1 <= len(paths) <= MAX_EMAILS:
        raise NoticeError("EMAIL_COUNT_INVALID")
    seen, notices = set(), []
    for path in paths:
        item = read_email(path)
        key = item["email_sha256"]
        if key not in seen:
            seen.add(key)
            notices.append(item)
    return {
        "mode": "VENDOR_EMAIL_NOTIFICATION_INTAKE_V1",
        "transport": "LOCAL_EMAIL_EXPORT_NOT_AUTOMATIC_GMAIL_OR_VENDOR_API",
        "input_emails": len(paths),
        "unique_notifications": len(notices),
        "linked_notices": sum(bool(x["original_notice_urls"]) for x in notices),
        "matching_notifications": sum(bool(x["service_categories"]) for x in notices),
        "qualified_real_leads": 0,
        "crm_writes": "NO",
        "outreach": "NO",
        "notices": notices,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research vendor procurement .eml, no crawling")
    parser.add_argument("eml", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    target = Path(args.output).resolve()
    if not target.is_relative_to(Path("exports").resolve()) or target.suffix != ".json" or target.exists():
        raise NoticeError("OUTPUT_MUST_BE_NEW_JSON_UNDER_EXPORTS")
    report = audit(args.eml)
    target.parent.mkdir(exist_ok=True, parents=True)
    with target.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "notices"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
