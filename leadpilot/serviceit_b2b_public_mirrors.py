"""Find independently readable public tender evidence in an approved mirror.

The official B2B page remains the primary source. This adapter searches only
public pages of explicitly reviewed mirrors; it never skips robots, opens
auth-required pages, accepts search snippets as proof, or sends vendor-email,
CRM identities or contacts to external models. The result is SOURCE evidence,
not automatic lead qualification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit
import re

import httpx

from .serviceit_public_evidence_research import (
    _host, _robots, _get, VisibleText, ResearchBlocked, _safe_model_excerpt,
)

# Additional public mirrors are discovery candidates only. Actual evidence
# requires a fresh HTTPS GET, robots permission, exact B2B number AND
# matching procurement-title terms. A blocked origin is skipped, never bypassed.
MIRRORS = frozenset(("tendem.ru", "zakupki.kontur.ru", "synapsenet.ru"))
MAX_CANDIDATES = 5
ID_RX = r"(?<!\d){id}(?!\d)"

# Email subjects often have service tags absent from the public tender title.
# Two meaningful word families can match "Предоставление IT-специалистов"
# with "Оказание услуг по предоставлению ИТ-специалистов", but ONLY after
# separately fetched evidence binds the exact ID to B2B-Center and the
# procurement label. IDs from OTHER trading platforms must not be mixed.
_TITLE_ROOTS = (
    "предоставл", "специалист", "разработч", "аналитик", "сервер",
    "аутсорс", "внедрен", "сопровожд", "оборудован", "интеграц",
    "закупк", "поставк", "обслуживан", "безопасност", "программ",
    "информацион", "поддержк", "тестирован", "процессор",
)


def _meaningful_title_roots(value: str) -> frozenset[str]:
    if type(value) is not str:
        return frozenset()
    normalized = value.casefold().replace("ё", "е")
    normalized = re.sub(r"(?i)(?<![a-zа-я])it(?![a-zа-я])", "ит", normalized)
    normalized = re.sub(r"(?i)(?<![a-zа-я0-9])1c(?![a-zа-я0-9])",
                        "1с", normalized)
    words = re.findall(r"[a-zа-я0-9]{2,}", normalized)
    roots = {root for word in words for root in _TITLE_ROOTS
             if word.startswith(root)}
    if "1с" in words:
        roots.add("1с")
    if "devops" in words:
        roots.add("devops")
    return frozenset(roots)




def matched_excerpt(text: str, tender_id: str, subject: str = "") -> str:
    """Extract text AROUND the exact tender ID, not the first 7k characters.

    A long public HTML page can list related notices in its footer. Do not
    identify a procurement by a matching number alone: require at least two
    independent distinctive subject words nearby, or leave unverified.
    """
    if (type(text) is not str or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240):
        return ""
    text = " ".join(text.split())
    tokens = re.findall(r"[^\W_]{3,}", subject.casefold())
    stops = {"закупка", "предоставление", "предоставлению",
             "предоставления", "оказание", "оказанию", "включая",
             "услуги", "услуг", "работы", "специалистов", "тендер",
             "поставка", "техническое", "требуется", "компании",
             "для", "или"}
    important = set(tokens) - stops
    # Generic titles ("Оказание услуг по предоставлению ИТ-специалистов")
    # can have too few distinctive terms after stopword removal. Accept a
    # FULL independently read public title instead, but only together with
    # the exact source-number label, platform, and procurement context.
    # A number in an unrelated footer link or a search snippet is insufficient.
    exact = list(re.finditer(ID_RX.format(id=tender_id), text))
    head = text[:4200]
    normal = lambda v: " ".join(v.casefold().replace("ё", "е").split())
    title_exact = 24 <= len(subject.strip()) <= 240 and normal(subject) in normal(head)
    title_overlap = len(_meaningful_title_roots(subject) &
                        _meaningful_title_roots(head)) >= 2
    platform = "b2b-center" in head.casefold()
    procurement = any(x in head.casefold() for x in
                      ("запрос котировок", "запрос цен", "запрос предложений",
                       "закупка", "тендер"))
    for number in exact[:12]:
        prefix = text[max(0,number.start()-90):number.start()]
        number_labeled = re.search(
            r"(?iu)(?:номер\s+у\s+источника|закупка\s*(?:№\s*)?|"
            r"запрос\s+(?:цен|предложений|котировок)\s*№\s*|"
            r"лот\s*№\s*)[:\s№-]*$", prefix) is not None
        # A category list can contain an unrelated purchase with this ID.
        # Relaxed title matching demands BOTH a labelled exact B2B number
        # and 2 procurement topic roots in an independently read page.
        after_id = text[number.end():number.end()+220].casefold()
        # A sidebar listing "B2B-Center" does NOT prove that this tender
        # originated there. Require an explicit publication attribution
        # directly after the labelled number in relaxed-title mode.
        b2b_publisher = bool(re.search(
            r"(?iu)(?:опубликован\w*|размещен\w*|размещён\w*|"
            r"площадка\s*[:—-]?)"
            r".{0,155}\bb2b-center\b", after_id))
        # Preserve the established exact-title mode for long public pages
        # where "Номер у источника" appears AFTER a large item table.
        # Only NEW tolerant matching is restricted to the first 4200 chars
        # and an explicit near-ID B2B source attribution.
        title_supported = (title_exact or
                           (title_overlap and b2b_publisher
                            and number.start() <= 4200))
        if platform and procurement and number_labeled and title_supported:
            nearby = text[max(0, number.start()-200):number.end()+450]
            return head[:3700] + " Источник и номер: " + nearby
    # Code/abbreviation words (1С, ИБ) may be short but meaningful.
    if re.search(r"(?i)\b1[сc]\b", subject):
        important.add("1с")
    if re.search(r"(?i)\bdevops\b", subject):
        important.add("devops")
    if len(important) < 2:
        return ""
    matches = list(re.finditer(ID_RX.format(id=tender_id), text))
    for m in matches[:12]:
        # Advertiser-related or navigation text elsewhere in page must not
        # satisfy the content match. Keep checks within same local vicinity.
        neighborhood = text[max(0, m.start()-950):min(len(text),m.end()+1700)]
        terms = set(re.findall(r"[^\W_]{3,}", neighborhood.casefold()))
        if re.search(r"(?i)\b1[сc]\b", neighborhood):
            terms.add("1с")
        if len(important & terms) < 2:
            continue
        # Retain the tender number; after it, likely description/requirements.
        # Capture enough context on both sides, not the site-wide navbar.
        excerpt = text[max(0, m.start()-550):min(len(text),m.end()+1750)]
        if len(text) > 7000 and len(text) > m.end()+250:
            intro = text[:1350]
            if "b2b-center" in text.casefold():
                return intro + " Фрагмент с номером: " + excerpt[:1900]
        return excerpt
    # Some marketplaces show the official B2B number in their SOURCE link
    # after a long lot table, while the procurement title/terms appear near
    # the top. This is a bounded document-level cross-check, NOT a mere ID
    # match: the top introduction must also share 2 distinctive terms, and
    # the page must identify the same B2B source platform.
    head = text[:3100]
    head_terms = set(re.findall(r"[^\W_]{3,}", head.casefold()))
    if re.search(r"(?i)\b1[сc]\b", head):
        head_terms.add("1с")
    if (matches and len(important & head_terms) >= 2
            and "b2b-center" in text.casefold()
            and ("закупк" in head.casefold() or "тендер" in head.casefold()
                 or "запрос" in head.casefold())):
        matched = text[max(0,matches[0].start()-250):
                       min(len(text),matches[0].end()+350)]
        return head[:2900] + " Источник и номер: " + matched
    return ""


def from_public_page(client: httpx.Client, url: str, tender_id: str,
                     subject: str, mirror_host="tendem.ru"):
    if mirror_host not in MIRRORS:
        raise ResearchBlocked("MIRROR_NOT_APPROVED")
    if _host(url, frozenset((mirror_host,))) not in (
            mirror_host, "www." + mirror_host):
        raise ResearchBlocked("MIRROR_URL_INVALID")
    # Reuse independent DNS/global-IP, HTTPS/port, robots and size bounds.
    _robots(client, url, frozenset((mirror_host,)))
    raw = _get(client, url, frozenset((mirror_host,)))
    parser = VisibleText()
    parser.feed(raw)
    visible = " ".join(parser.parts)
    # Full HTML visible text is scanned, including pages >7k where the exact
    # ID may be buried after banners; only the relevant 2.3k window is retained.
    excerpt = matched_excerpt(visible, tender_id, subject)
    if not excerpt:
        return None
    return {
        "url": url, "excerpt": excerpt,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "INDEPENDENT_PUBLIC_MIRROR",
    }


def _detail_score(excerpt: str) -> int:
    """Rank already fetched evidence richness; never infer a missing fact."""
    if not isinstance(excerpt, str):
        return 0
    s = excerpt.casefold()
    terms = (
        (r"(?iu)\bзаказчик(?:и)?\b.{0,90}\b(?:ооо|пао|ао|общество)", 9),
        (r"(?iu)\bинн(?:-кпп)?\b", 6),
        (r"(?iu)(?:окончание подачи заявок|подача заявок до|срок подачи)", 5),
        (r"(?iu)(?:условия оплаты|отсрочка платежа|постоплата)", 4),
        (r"(?iu)(?:обеспечение заявки|банковская гарантия|аванс)", 3),
        (r"(?iu)(?:документы участника|подгрузка документации|заявка)", 2),
        (r"(?iu)(?:объекты закупки|место поставки|количество)", 2),
    )
    return sum(weight for pattern, weight in terms if re.search(pattern, s))


def brave_mirror_search(client: httpx.Client, tender_id: str, subject: str,
                        key: str, *, allow_mirrors=MIRRORS):
    """Choose up to TWO richest independently fetched public source pages.

    One verified Tendum hint is no longer allowed to suppress a richer
    Kontur or Synapse source that actually contains the labelled buyer,
    deadline and participation rules. Search snippets NEVER become evidence.
    Every page still passes HTTPS/public DNS/robots/no redirects and exact
    tender ID plus procurement title checks. Up to 3 Brave searches and 8
    independently fetched candidates across all hosts (bounded cost).
    """
    if (type(key) is not str or not key.isascii()
            or not 12 <= len(key) <= 200 or "\n" in key or "\r" in key
            or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(allow_mirrors) is not frozenset
            or not allow_mirrors <= MIRRORS):
        return None
    from .serviceit_independent_company_research import SEARCH_URL
    candidates = []
    checked = set()
    known = {"4673595": "https://tendem.ru/tenders/16548471"}
    if "tendem.ru" in allow_mirrors and tender_id in known:
        url = known[tender_id]
        checked.add(url)
        try:
            row = from_public_page(client, url, tender_id, subject)
            if row:
                candidates.append(row)
        except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
            pass

    # Prefer the source with detailed verified buyer/INN/terms over an
    # earlier brief catalogue listing. Never accept the Brave text as proof.
    hosts = [x for x in (
        "zakupki.kontur.ru", "tendem.ru", "synapsenet.ru")
        if x in allow_mirrors]
    budget = 8
    for mirror in hosts:
        try:
            res = client.get(
                SEARCH_URL,
                params={"q": 'site:' + mirror + ' "' + tender_id + '"',
                        "count": MAX_CANDIDATES},
                headers={"X-Subscription-Token": key,
                         "Accept": "application/json",
                         "Cache-Control": "no-cache"},
                follow_redirects=False, timeout=12.0)
            if (res.status_code != 200
                    or not 0 < len(res.content) <= 160000
                    or "application/json" not in res.headers.get("content-type", "")):
                continue
            data = res.json().get("web", {}).get("results", [])
            if type(data) is not list or len(data) > 20:
                continue
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            continue
        for hit in data[:MAX_CANDIDATES]:
            if len(checked) >= budget:
                break
            if type(hit) is not dict or type(hit.get("url")) is not str:
                continue
            url = hit["url"]
            if url in checked:
                continue
            checked.add(url)
            try:
                result = from_public_page(
                    client, url, tender_id, subject, mirror_host=mirror)
                if result:
                    candidates.append(result)
            except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
                continue

    if not candidates:
        return None
    best = sorted(candidates, key=lambda p: (
        -_detail_score(p["excerpt"]), -len(p["excerpt"]), p["url"]))[:2]
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": x["url"], "excerpt": x["excerpt"][:4700]}
                  for x in best],
        "contacts": [],
        "model_excerpt": _safe_model_excerpt(best[0]["excerpt"]),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "company_site_candidate": "",
        "employer_named_in_source": "",
        "browser_rendered_unverified": False,
        "independent_company_research": None,
        "discovery": "BRAVE_PUBLIC_MIRRORS_TWO_INDEPENDENT_DETAIL_PAGES",
    }
