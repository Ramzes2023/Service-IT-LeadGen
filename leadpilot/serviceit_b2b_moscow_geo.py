"""B2B-Center pre-CRM Moscow/Moscow Oblast place-of-performance gate.

Only explicit PROCUREMENT location in a verified, independently fetched page
may approve a new card. The organizer's office, buyer registration address,
Gmail search profile, model output, or a page's global footer never prove the
place of contract performance. UNKNOWN / OUTSIDE / MIXED never write CRM.

This module performs at most two GETs (robots + the original public URL).
No Brave, Tavily, model, login, redirect, proxy or paid research.
"""
from __future__ import annotations

import re

from .serviceit_b2b_public_mirrors import matched_excerpt
from .serviceit_public_evidence_research import (
    ResearchBlocked, VisibleText, _get, _robots,
)

HOSTS = frozenset(("b2b-center.ru",))
LOCATION_LABEL = re.compile(
    r"(?iu)^\s*(?:"
    r"место\s+(?:поставки|оказания\s+услуг|выполнения\s+работ|"
    r"исполнения\s+(?:договора|контракта|обязательств))|"
    r"адрес\s+(?:поставки|оказания\s+услуг|выполнения\s+работ)|"
    r"регион\s+(?:поставки|оказания\s+услуг|исполнения|выполнения\s+работ)|"
    r"территория\s+(?:оказания\s+услуг|исполнения\s+договора)"
    r")\s*[:：\-–—]?\s*(.*)$"
)
MOSCOW = re.compile(
    r"(?iu)(?<![а-яё])москв(?:а|е|у|ы|ой|ою)(?![а-яё])|"
    r"московск(?:ая|ой|ую)\s+(?:обл(?:асть|\.)?|области)|"
    r"подмосков(?:ье|ья|ью|ьи)"
)
OTHER_PLACES = re.compile(
    r"(?iu)(?:санкт[\s-]*петербург|ленинградск(?:ая|ой)\s+обл|"
    r"республик[аиуе]\s+[а-яё-]+|"
    r"новосибирск|екатеринбург|казань|уфа|самара|краснодар|"
    r"нижний\s+новгород|тюмень|пермь|ростов[\s-]*на[\s-]*дону|"
    r"владивосток|хабаровск|красноярск|омск|челябинск|иркутск)"
)
ALL_RUSSIA = re.compile(
    r"(?iu)по\s+всей\s+росси[ии]|все\s+регионы|по\s+всей\s+рф|"
    r"по\s+территории\s+рф|на\s+территории\s+всей\s+рф|"
    r"люб(?:ой|ая)\s+регион|вся\s+россия"
)
TEXT_LIMIT = 512000


def classify_location_lines(lines: list[str]) -> str:
    """MOSCOW / OUTSIDE / MIXED / UNKNOWN, strict and side-effect-free."""
    if type(lines) is not list:
        return "UNKNOWN"
    values = []
    for i, line in enumerate(lines[:5000]):
        if type(line) is not str:
            continue
        match = LOCATION_LABEL.match(" ".join(line.split())[:280])
        if match is None:
            continue
        value = match.group(1).strip()
        # HTML often places the actual location in the next text node.
        if not value and i + 1 < len(lines):
            candidate = lines[i + 1]
            if (type(candidate) is str and len(candidate) <= 200
                    and not LOCATION_LABEL.match(candidate)):
                value = candidate.strip()
        if value and len(value) <= 240:
            values.append(value)
    if not values:
        return "UNKNOWN"
    in_moscow = any(MOSCOW.search(v) for v in values)
    elsewhere = any(OTHER_PLACES.search(v) or ALL_RUSSIA.search(v)
                    for v in values)
    # A positive match alone is NOT enough if the tender explicitly covers
    # another named region as well.
    if in_moscow and elsewhere:
        return "MIXED"
    if in_moscow:
        return "MOSCOW"
    if elsewhere:
        return "OUTSIDE"
    # 'г. Московский' and 'Московский район' are intentionally NOT city proof.
    return "UNKNOWN"


def check_public_procurement_location(notice: dict, client) -> str:
    """Never trust bare mail fields; first verify the exact public tender page."""
    if type(notice) is not dict or notice.get("source") != "B2B-Center":
        return "UNKNOWN"
    tender_id = notice.get("tender_id")
    subject = notice.get("subject")
    url = notice.get("source_url")
    if (type(tender_id) is not str or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240
            or type(url) is not str):
        return "UNKNOWN"
    # Only the signed original B2B tender; _robots/_get enforce https,
    # allowlisted host, globally routable DNS, public robots and no redirects.
    if not re.fullmatch(
        r"https://www\.b2b-center\.ru/(?:market|app/market-next)/"
        r"[a-z0-9-]{1,160}/tender-" + re.escape(tender_id) + r"/", url, re.I
    ):
        return "UNKNOWN"
    try:
        _robots(client, url, HOSTS)
        html = _get(client, url, HOSTS)
        if not 100 <= len(html) <= TEXT_LIMIT:
            return "UNKNOWN"
        parser = VisibleText()
        parser.feed(html)
        lines = parser.parts
        flat = " ".join(lines)
        # Number AND tender title in fetched full text, not an index snippet.
        if not matched_excerpt(flat, tender_id, subject):
            return "UNKNOWN"
        return classify_location_lines(lines)
    except (ResearchBlocked, ValueError, TypeError, UnicodeError,
            OSError, AttributeError):
        return "UNKNOWN"
