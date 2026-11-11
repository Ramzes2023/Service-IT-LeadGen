"""V18: bounded Brave LLM Context retrieval for exact B2B tender evidence.

Unlike search engine HTML scraping, this uses an official Brave API already
configured for the VPS. Indexed excerpts are useful RESEARCH LEADS, never
equivalent to independently fetched primary pages or official FNS data.
No cookies, CAPTCHA bypass, proxy rotation, contact outreach or CRM writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_brave_llm_context import CONTEXT_URL
from .serviceit_public_evidence_research import _host, ResearchBlocked

MARKET_HOSTS = frozenset((
    "zakupki.kontur.ru", "synapsenet.ru", "tendem.ru", "b2b-center.ru"
))
MAX_RESPONSE = 256000
MAX_INDEX_URLS = 5
MAX_QUOTE = 250

# Only clearly labelled procurement fields may be surfaced as index claims.
# Never infer a hidden price, legal identity, seller contact or requirements.
FIELDS = (
    ("BUYER", re.compile(
        r"(?iu)(?:заказчик(?:и)?|закупающ(?:ая|ей)\s+организация)\s*[:：—-]\s*"
        r"([^\n.]{7,130})")),
    ("INN", re.compile(
        r"(?iu)\bИНН(?:\s*[-–]\s*КПП)?\s*[:：—-]?\s*"
        r"(\d{10}|\d{12})(?!\d)")),
    ("DEADLINE", re.compile(
        r"(?iu)(?:окончани[ея]\s+подачи\s+заявок|подача\s+заявок\s+до|"
        r"срок\s+подачи\s+заявок)\s*[:：—-]?\s*"
        r"(.{0,55}\d{1,2}[./]\d{1,2}[./]\d{4}.{0,25})")),
    ("LOCATION", re.compile(
        r"(?iu)(?:место\s+поставки|адрес\s+поставки|регион\s+поставки)"
        r"\s*[:：—-]\s*([^\n.]{4,145})")),
    ("BUDGET", re.compile(
        r"(?iu)(?:начальная\s+цена|максимальная\s+цена|бюджет\s+закупки)"
        r"\s*[:：—-]\s*([^\n.]{3,70})")),
    # Additional categories are only indexed RESEARCH LEADS, never evidence
    # of an actual contractual duty or approved participation.
    ("PROCEDURE", re.compile(
        r"(?iu)(?:тип закупки|способ закупки|вид процедуры|форма закупки)"
        r"\s*[:：—-]\s*([^\n.;]{8,140})")),
    ("TECHNOLOGY", re.compile(
        r"(?iu)(?:технические требования|техническое задание|спецификация)"
        r"\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("ELIGIBILITY", re.compile(
        r"(?iu)(?:требования к участник(?:ам|у)|квалификационные требования|"
        r"условия участия)\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("EVALUATION", re.compile(
        r"(?iu)(?:критерии оценки|критерии отбора|порядок оценки заявок)"
        r"\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("SUBMISSION", re.compile(
        r"(?iu)(?:порядок подачи заявок|способ подачи заявки)"
        r"\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("DOCUMENTS", re.compile(
        r"(?iu)(?:перечень документов|документы для участия|"
        r"состав заявки)\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("PAYMENT", re.compile(
        r"(?iu)(?:условия оплаты|порядок расчетов|порядок расчётов)"
        r"\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("GUARANTEE", re.compile(
        r"(?iu)(?:обеспечение заявки|обеспечение договора|"
        r"банковская гарантия)\s*[:：—-]\s*([^\n.;]{10,155})")),
    ("DELIVERY", re.compile(
        r"(?iu)(?:срок поставки|срок исполнения|срок оказания услуг)"
        r"\s*[:：—-]\s*([^\n.;]{10,155})")),
)


class IndexUnavailable(ValueError):
    pass


def _title_terms(s):
    """Small distinct Russian/English title roots, not generic tender words."""
    if type(s) is not str:
        return set()
    stop = {"закупка", "запрос", "цен", "лот", "услуг", "услуги", "оказание",
            "приобретение", "для", "нужд", "поставка", "оборудования",
            "предоставление", "компании", "тендер", "предварительный",
            "квалификационный", "отбор", "коммерческий", "договор"}
    words = {w[:6] for w in re.findall(r"(?iu)[\w]{4,}", s.casefold())
             if w not in stop}
    # Three-letter buyer acronyms (МКБ, РЖД, ВТБ) are often distinctive.
    acronyms = re.findall(r"(?u)(?<![\w])[А-ЯЁA-Z]{3,6}(?![\w])", s)
    words.update(w.casefold()[:6] for w in acronyms
                 if w.casefold() not in stop)
    return words


def _safe_index_candidate(url, title, text, tender_id, subject):
    """Exact number plus subject and marketplace attribution; never a mere ID."""
    if type(url) is not str or type(title) is not str or type(text) is not str:
        return None
    if not 12 <= len(url) <= 950 or len(title) > 400 or len(text) > 24000:
        return None
    try:
        host = _host(url, MARKET_HOSTS)
    except ResearchBlocked:
        return None
    if host not in MARKET_HOSTS and not any(
            host.endswith("." + x) for x in MARKET_HOSTS):
        return None
    exact = re.compile(r"(?<!\d)" + re.escape(tender_id) + r"(?!\d)")
    if not (exact.search(text) or exact.search(title)):
        return None
    overlap = _title_terms(subject) & _title_terms(title + " " + text[:3200])
    # Only a full title match may waive the two-distinctive-terms requirement.
    flat = lambda x: " ".join(x.casefold().replace("ё", "е").split())
    if len(overlap) < 2 and flat(subject) not in flat(title+" "+text[:3200]):
        return None
    # A third-party directory might display the exact tender ID yet describe
    # a different procurement platform. B2B origin must appear in the indexed
    # subject/detail; Kontur URL alone is not sufficient.
    platform = ("b2b-center" in (title+" "+text[:4400]).casefold()
                or host == "b2b-center.ru" or host.endswith(".b2b-center.ru"))
    # An aggregator's exact-ID URL and matching subject may remain a useful
    # lead when the indexed snippet omits B2B origin. Keep provenance
    # explicitly unconfirmed; do not promote it to verified buyer/price/FNS.
    if not platform and not exact.search(url):
        return None
    # Search context might contain prompt-injection. Never execute or
    # forward its instructions as model facts. Only bounded labelled quotes.
    # A search index can aggregate many tenders on one page. Extract labels
    # only close to the exact tender number and matching topic. An exact
    # detail URL + title may supply a text block without the ID repeated.
    anchored = []
    for id_match in list(exact.finditer(text[:6000]))[:6]:
        local = text[max(0, id_match.start()-250):id_match.end()+1700]
        if (_title_terms(subject) & _title_terms(local)):
            anchored.append(local)
    if not anchored and exact.search(title) and exact.search(url):
        anchored.append(text[:2500])
    details = "\n".join(anchored)[:6000]
    quotes = []
    # Clearly stated PКО is NOT a contract award. Capture its actual wording
    # as an indexed warning; never synthesize a value from the site type.
    kind_rx = re.compile(
        r"(?iu)(?:предварительный\s+квалификационный\s+отбор|"
        r"запрос\s+предложений|запрос\s+цен|аукцион)"
        r"\s*№\s*" + re.escape(tender_id) + r"(?!\d)")
    kind = kind_rx.search(title + "\n" + details)
    if kind:
        quotes.append({"category":"PROCEDURE",
                       "quote":" ".join(kind.group(0).split())[:MAX_QUOTE]})
    for label, rx in FIELDS:
        match = rx.search(details)
        if not match:
            continue
        clean = " ".join(match.group(0).split())[:MAX_QUOTE]
        if re.search(r"[<>\x00-\x1f]|ignore\s+previous|system\s*:", clean, re.I):
            continue
        # Conditional labels must not accidentally copy a private email/phone
        # or external instruction from an untrusted search snippet.
        if label not in ("BUYER","INN","DEADLINE","LOCATION","BUDGET"):
            if re.search(r"@|https?://|(?:\+7|8)\D{0,3}\d{3}", clean, re.I):
                continue
        if (label, clean) not in {(x["category"],x["quote"]) for x in quotes}:
            quotes.append({"category": label, "quote": clean})
    return {"url": url, "host": host, "claims": quotes[:14],
            "evidence_level": "INDEX_ONLY_UNVERIFIED",
            "title_match": ("EXACT_ID_AND_TOPIC_AND_PLATFORM" if platform else
                            "EXACT_ID_TOPIC_PLATFORM_NOT_CONFIRMED")}


def tender_index_context(client, tender_id, subject, token):
    """One Brave API request max per tender; no retry after quota or timeout.

    The index can provide the original page's extracted snippets even if
    this VPS cannot directly read the publisher. Such claims stay labelled
    INDEX_ONLY_UNVERIFIED, never passed to the legal registry/lead score.
    """
    checked = datetime.now(timezone.utc).isoformat()
    baseline = {"status": "INDEX_UNAVAILABLE", "candidates": [],
                "checked_at": checked, "search_requests": 0}
    if (not isinstance(client, httpx.Client)
            or type(tender_id) is not str
            or not re.fullmatch(r"\d{5,12}", tender_id)
            or type(subject) is not str or not 10 <= len(subject) <= 240
            or type(token) is not str or not 12 <= len(token) <= 200
            or not token.isascii() or "\n" in token or "\r" in token):
        return baseline
    try:
        response = client.get(
            CONTEXT_URL,
            params={"q": '"' + tender_id + '" B2B-Center ' + subject[:110],
                    "country": "RU", "search_lang": "ru",
                    "count": 12, "maximum_number_of_urls": 8,
                    "maximum_number_of_tokens": 4096,
                    "maximum_number_of_tokens_per_url": 1536,
                    "context_threshold_mode": "balanced",
                    "enable_source_metadata": "true"},
            headers={"X-Subscription-Token": token,
                     "Accept": "application/json",
                     "Cache-Control": "no-cache"},
            timeout=20, follow_redirects=False)
        baseline["search_requests"] = 1
        if response.status_code != 200:
            baseline["status"] = (
                "INDEX_QUOTA_OR_AUTH" if response.status_code in (401,403,429)
                else "INDEX_HTTP_UNAVAILABLE")
            return baseline
        if not 0 < len(response.content) <= MAX_RESPONSE:
            baseline["status"] = "INDEX_SIZE_UNSUPPORTED"
            return baseline
        if "application/json" not in response.headers.get("content-type",""):
            baseline["status"] = "INDEX_CONTENT_TYPE_UNSUPPORTED"
            return baseline
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        baseline["status"] = "INDEX_TRANSPORT_UNAVAILABLE"
        return baseline
    if type(payload) is not dict or type(payload.get("grounding")) is not dict:
        baseline["status"] = "INDEX_SCHEMA_UNSUPPORTED"
        return baseline
    rows = payload["grounding"].get("generic")
    sources = payload.get("sources")
    if (type(rows) is not list or type(sources) is not dict
            or len(rows) > 60 or len(sources) > 80):
        baseline["status"] = "INDEX_SCHEMA_UNSUPPORTED"
        return baseline
    chosen, seen = [], set()
    for entry in rows[:24]:
        if type(entry) is not dict:
            continue
        url,title,snips=entry.get("url"),entry.get("title"),entry.get("snippets")
        if (type(url) is not str or url not in sources or
                type(sources[url]) is not dict or
                type(title) is not str or type(snips) is not list
                or len(snips) > 60 or url in seen):
            continue
        text = "\n".join(s for s in snips[:16]
                         if type(s) is str and len(s) <= 5000)
        row = _safe_index_candidate(url,title,text,tender_id,subject)
        if row is None:
            continue
        seen.add(url)
        chosen.append(row)
        if len(chosen) >= MAX_INDEX_URLS:
            break
    baseline["status"] = "INDEX_CANDIDATES" if chosen else "INDEX_NO_EXACT_MATCH"
    baseline["candidates"] = chosen
    return baseline
