"""Bounded public-web evidence research for NEW ServiceIT 1062/21 cards.

Source-neutral research; not a lead source collector or a contact campaign.
Only explicitly allowlisted HTTPS hosts, public pages, robots rules, and
company-site links labeled as such are followed. The source page is NOT proof
that the named company owns a linked site. No guessed email, phone or person.
No CRM writes; caller may place a cited manager draft in the nine AI fields.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

AGENT = "ServiceIT-PublicEvidenceResearch/1.0 (public pages; no outreach)"
MARKETPLACES = frozenset({
    "hh.ru", "b2b-center.ru", "zakupki.gov.ru", "trudvsem.ru",
    "superjob.ru", "avito.ru", "rabota.ru", "bidzaar.com",
})
CONTACT_WORDS = ("контакт", "contacts", "contact", "связаться", "about", "о компании",
                 "реквизит", "rekvizit", "рекрут", "career", "hr")
OFFICIAL_WORDS = ("сайт компании", "официальный сайт", "website", "сайт работодателя")
EMAIL = re.compile(r"[A-Za-z0-9_.+\-]{1,64}@[A-Za-z0-9.-]{3,150}\.[A-Za-z]{2,18}")
SAFE_HOST = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}\Z")
MAX_HTML = 160000
MAX_PAGES = 5


class ResearchBlocked(ValueError):
    pass


def _host(url, allowed):
    if type(url) is not str or not 12 <= len(url) <= 1000:
        raise ResearchBlocked("RESEARCH_URL_INVALID")
    try:
        p = urlsplit(url)
        hostname = (p.hostname or "").lower()
        port = p.port
    except ValueError:
        raise ResearchBlocked("RESEARCH_URL_INVALID") from None
    if (p.scheme != "https" or not SAFE_HOST.fullmatch(hostname)
            or p.username or p.password or p.fragment or port not in (None, 443)
            or not p.path.startswith(("/", "")) or p.path.startswith("//")
            or any(ord(ch) < 32 for ch in url)
            or not any(hostname == approved or hostname.endswith("." + approved)
                       for approved in allowed)
            or any(hostname == m or hostname.endswith("." + m) for m in
                   ("localhost", "local", "internal", "example.invalid"))):
        raise ResearchBlocked("RESEARCH_HOST_NOT_APPROVED")
    return hostname


def _public_dns(host):
    """Reject local/metadata targets; DNS change after validation is a residual risk."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or len(addresses) > 30:
            return False
        return all(ipaddress.ip_address(address[4][0]).is_global
                   for address in addresses)
    except (OSError, ValueError):
        return False


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.links = []
        self.anchor = None
        self.tel = []
        self.mail = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if self.skip:
            return
        if tag == "a":
            href = attrs.get("href", "")
            self.anchor = [href, []] if type(href) is str else None
            if isinstance(href, str) and href.startswith("tel:"):
                self.tel.append(href[4:])
            if isinstance(href, str) and href.startswith("mailto:"):
                self.mail.append(href[7:].split("?")[0])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
        if tag == "a" and self.anchor:
            href, parts = self.anchor
            self.links.append((href, " ".join(parts).strip().casefold()[:100]))
            self.anchor = None

    def handle_data(self, data):
        if self.skip:
            return
        s = " ".join(data.split())
        if s:
            self.parts.append(s)
            if self.anchor is not None:
                self.anchor[1].append(s)


def _get(client, url, allowed, *, html=True):
    host = _host(url, allowed)
    if not _public_dns(host):
        raise ResearchBlocked("RESEARCH_DNS_UNVERIFIED")
    response = client.get(url, follow_redirects=False, timeout=6.0,
                          headers={"User-Agent": AGENT, "Accept": "text/html,text/plain"})
    if (response.status_code != 200 or len(response.content) > MAX_HTML
            or (html and "text/html" not in response.headers.get("content-type", ""))):
        raise ResearchBlocked("RESEARCH_PAGE_UNAVAILABLE")
    return response.text


def _robots(client, url, allowed):
    p = urlsplit(url)
    robots_url = p.scheme + "://" + p.netloc + "/robots.txt"
    try:
        rules = _get(client, robots_url, allowed, html=False)
    except ResearchBlocked:
        # Missing/unreachable policy is not permission to bypass site controls.
        raise ResearchBlocked("RESEARCH_ROBOTS_UNKNOWN") from None
    rp = RobotFileParser()
    rp.parse(rules[:20000].splitlines())
    if not rp.can_fetch(AGENT, url):
        raise ResearchBlocked("RESEARCH_ROBOTS_DISALLOWED")


def _page(client, url, allowed):
    _robots(client, url, allowed)
    raw = _get(client, url, allowed)
    parser = VisibleText()
    parser.feed(raw)
    text = " ".join(parser.parts)
    return {"url": url, "text": text[:7000],
            "links": parser.links[:150], "emails": parser.mail[:30],
            "phones": parser.tel[:30]}


def _contact_role(email):
    local = email.split("@", 1)[0].lower()
    if local in ("hr", "job", "jobs", "career", "recruitment"):
        return "отдел подбора персонала"
    if local in ("zakupki", "purchase", "procurement", "tender"):
        return "отдел закупок"
    if local in ("sales", "commercial", "partners"):
        return "коммерческий отдел"
    return "общая корпоративная почта"


def _phone(value):
    s = re.sub(r"[^\d+]", "", value)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return None


def _contacts(page, official_host, stamp):
    # Contacts on a recruitment/marketplace platform belong to the platform,
    # NOT automatically to the employer. Only approved corporate-site pages.
    got = []
    for value in page["emails"] + EMAIL.findall(page["text"]):
        candidate = value.strip().lower()
        if candidate.split("@")[-1] != official_host.removeprefix("www."):
            continue
        row = {"type": "email", "value": candidate,
               "role": _contact_role(candidate), "url": page["url"],
               "checked_at": stamp}
        if row not in got:
            got.append(row)
    # Capture public numbers printed on corporate contact pages, even when
    # the website omits a tel: link. A CRM phone is never used as input.
    text_numbers = []
    if ("contact" in urlsplit(page["url"]).path.casefold()
            or "контакт" in page["text"].casefold()
            or "телефон" in page["text"].casefold()):
        text_numbers = re.findall(
            r"(?<!\d)(?:\+7|8)[\s-]*\(?\d{3}\)?[\s-]*"
            r"\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)",
            page["text"])[:12]
    for value in page["phones"] + text_numbers:
        phone = _phone(value)
        if phone:
            row = {"type": "phone", "value": phone,
                   "role": "публичный телефон компании", "url": page["url"],
                   "checked_at": stamp}
            if row not in got:
                got.append(row)
    return got[:6]


def _safe_model_excerpt(text):
    # Public page excerpts can contain personal data, instructions and HTML.
    # Do not pass any such snippet to a model; keep its analysis title-only.
    clean = " ".join(text.split())[:450]
    if re.search(r"@|https?://|www\.|(?:\+7|8)\D{0,3}\d{3}|\d{10,}|"
                 r"ignore|system:|assistant:|инструкц|игнорир|<|>|\{|\}", clean, re.I):
        return ""
    return clean


def research_public(item, client, allow_hosts, *, now=None):
    """At most 3 public pages. Source provenance is never proof of customer identity.

    Caller must explicitly opt in; source host and prospective company host must
    be independently configured in the approved domain allowlist.
    """
    if (type(item) is not dict or not isinstance(client, httpx.Client)
            or not isinstance(allow_hosts, frozenset) or not 1 <= len(allow_hosts) <= 80
            or any(type(h) is not str or not SAFE_HOST.fullmatch(h) for h in allow_hosts)):
        raise ResearchBlocked("RESEARCH_CONFIGURATION_INVALID")
    kind = item.get(PREFIX + "SIGNAL_KIND")
    if kind not in ("tender-opportunity", "procurement", "tender",
                    "1c_vacancy", "it_vacancy", "vacancy-opportunity",
                    "direct-service", "service-request", "direct-request",
                    "crm_need", "new_store", "service-demand"):
        raise ResearchBlocked("RESEARCH_NOT_PUBLIC_SIGNAL")
    url = item.get(PREFIX + "SIGNAL_URL")
    source_host = _host(url, allow_hosts)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    try:
        first = _page(client, url, allow_hosts)
    except (ResearchBlocked, httpx.HTTPError, ValueError):
        return {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": [],
                "contacts": [], "model_excerpt": "", "checked_at": stamp}
    pages = [first]
    candidates = []
    # A marketplace posting can link the employer's official website. The
    # link is a CANDIDATE, not verified ownership. Never follow an unknown host.
    for href, label in first["links"]:
        if not any(marker in label for marker in OFFICIAL_WORDS):
            continue
        page_url = urljoin(url, href)
        try:
            h = _host(page_url, allow_hosts)
            if not any(h == m or h.endswith("." + m) for m in MARKETPLACES):
                candidates.append((page_url, h))
        except ResearchBlocked:
            continue
    corporate = None
    if source_host not in MARKETPLACES and not any(
            source_host.endswith("." + m) for m in MARKETPLACES):
        corporate = source_host
    elif candidates:
        corporate = candidates[0][1]
    if corporate:
        # If linked to a company domain, visit it; otherwise search same-site
        # contact/about pages. No link following outside the allowlist.
        if corporate != source_host and candidates:
            try:
                pages.append(_page(client, candidates[0][0], allow_hosts))
            except (ResearchBlocked, httpx.HTTPError, ValueError):
                pass
        anchor_page = pages[-1]
        for href, label in anchor_page["links"]:
            if len(pages) >= MAX_PAGES:
                break
            if not any(word in label for word in CONTACT_WORDS):
                continue
            target = urljoin(anchor_page["url"], href)
            try:
                if _host(target, allow_hosts) == corporate and all(
                        p["url"] != target for p in pages):
                    pages.append(_page(client, target, allow_hosts))
            except (ResearchBlocked, httpx.HTTPError, ValueError):
                continue
    contacts = []
    for page in pages:
        if corporate and urlsplit(page["url"]).hostname == corporate:
            contacts.extend(_contacts(page, corporate, stamp))
    # No model-generated contacts or inferred personal details ever enter CRM.
    dedup = {(c["type"], c["value"]): c for c in contacts}
    # A public page can mention a job title, but cannot establish a contract,
    # current deadline, decision maker or permission to contact.
    excerpt = _safe_model_excerpt(first["text"])
    return {"status": "PUBLIC_PAGES_INSPECTED", "pages": [
                {"url": p["url"], "excerpt": " ".join(p["text"].split())[:500]}
                for p in pages],
            "contacts": list(dedup.values())[:6],
            "model_excerpt": excerpt, "checked_at": stamp,
            "company_site_candidate": corporate or ""}


def enrich_manager_fields(fields, findings, *, signal_kind):
    """Place evidence, contact origin and manager action in existing 9 AI fields."""
    if not isinstance(fields, dict) or set(fields) != AI_FIELDS:
        raise ResearchBlocked("NINE_AI_FIELDS_REQUIRED")
    if not isinstance(findings, dict) or findings.get("status") != "PUBLIC_PAGES_INSPECTED":
        return fields
    pages = findings.get("pages") or []
    if not pages:
        return fields
    lines = ["ДОПОЛНИТЕЛЬНЫЙ ПОИСК ПО ОТКРЫТЫМ СТРАНИЦАМ",
             "Проверено: " + str(findings.get("checked_at", "дата неизвестна")),
             "Найденные сведения требуют сверки с организацией."]
    for page in pages[:MAX_PAGES]:
        lines.append("Страница: " + page["url"])
        lines.append("Извлечённый текст (НЕ доказательство намерения купить): "
                     + page["excerpt"][:350])
    contacts = findings.get("contacts") or []
    if contacts:
        lines.append("ПУБЛИЧНО ОПУБЛИКОВАННЫЕ КОНТАКТЫ (не подтверждено право рассылки):")
        for c in contacts[:6]:
            lines.append(c["type"] + ": " + c["value"] + " | " + c["role"]
                         + " | найдено внешним поиском: " + c["url"])
    else:
        lines.append("Контакты компании: на проверенных страницах не найдены.")
        lines.append("Не придумывать телефон или email, проверить сайт компании вручную.")
    if signal_kind in ("1c_vacancy", "it_vacancy", "vacancy-opportunity"):
        audience = "HR / ИТ-руководитель; сначала выяснить, допустим ли внешний подрядчик"
        approach = ("Черновик менеджеру ПОСЛЕ разрешения контакта: "
                    "«Увидели опубликованную ИТ-вакансию. Рассматриваете ли "
                    "сопровождение части задач по договору с подрядчиком?» "
                    "Если указан только штатный найм или запрет предложений — не отправлять.")
    elif signal_kind in ("tender-opportunity", "procurement", "tender"):
        audience = "отдел закупок; проверить дедлайн, статус и правила участия"
        approach = ("Черновик менеджеру ПОСЛЕ разрешения контакта: "
                    "«Просим уточнить требования к участию и технический объём "
                    "закупки по опубликованному извещению». Не выдавать уведомление "
                    "за прямой запрос услуг.")
    else:
        audience = "ИТ-руководитель / закупки; подтвердить прямую потребность в услуге"
        approach = ("Черновик менеджеру ПОСЛЕ разрешения контакта: "
                    "«Уточните, пожалуйста, актуальны ли обозначенные ИТ-задачи, "
                    "какой объём работ и срок рассмотрения предложения?»")
    lines.append("Кому предложить рассмотрение: " + audience + ".")
    lines.append(approach)
    lines.append("Сначала проверить корпоративный STOP, CRM-дубли, законность обращения.")
    appendix = "\n".join(lines)
    updated = dict(fields)
    for suffix, extra in (
        ("AI_FULL_REPORT", "\n\n" + appendix),
        ("AI_MANAGER_BRIEF", "\n" + "\n".join(lines[-min(len(lines), 10):])),
        ("AI_NEXT_ACTION", " Проверить опубликованные контакты и роль: " + audience + "."),
    ):
        key = PREFIX + suffix
        updated[key] = (updated[key] + extra)[:12000]
    if contacts:
        key = PREFIX + "AI_RECOMMENDATION"
        updated[key] = (updated[key] +
                        " Найдены дополнительные публичные контакты с URL в полном отчёте.")[:12000]
    return updated
