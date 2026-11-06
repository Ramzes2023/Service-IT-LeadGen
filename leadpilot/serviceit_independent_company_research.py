"""Independent corporate public-web intelligence for future ServiceIT cards.

An OPTIONAL owner-configured Brave Search API discovers websites other than
the original signal. This is post-card research, not a new lead source. Search
queries contain only company names independently PUBLISHED in the source.
Indexed snippets are never accepted as evidence. Every usable finding must be
read from a separately reachable public page, with its own URL and timestamp.

Commercial opportunities are hypotheses, not confirmed orders. Discovered
corporate-site candidates are not certified company identities or contact
consent. No CRM write, contact sending, global STOP or Lead Score change.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_public_evidence_research import (
    _page, _host, _contacts, _safe_model_excerpt, MARKETPLACES,
    ResearchBlocked, PREFIX, AI_FIELDS,
)

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_SEARCH_CALLS = 3
MAX_EXTERNAL_PAGES = 6
MAX_EXTERNAL_FETCHES = 9
MAX_EXTERNAL_HOSTS = 5
MAX_INDEX_RESULTS = 24

# The site must publish the organization name; a search-result snippet alone
# never establishes a match. An exact *public* name match is still not proof of
# legal identity, group affiliation, active demand or contact authorization.
SIGNALS = (
    ("OPENING_OR_EXPANSION",
     ("открытие магазина", "открывает магазин", "новый магазин",
      "открытие филиала", "открыли филиал", "новый филиал",
      "расширение сети", "новый склад", "открытие офиса",
      "новая торговая точка", "запуск магазина"),
     "Возможен запуск/расширение площадок: проверить потребность в ККТ, "
     "маркировке, сетях, торговом оборудовании и интеграции с 1С."),
    ("IT_DIGITALIZATION",
     ("внедрение 1с", "автоматизация", "переход на 1с", "внедрение crm",
      "цифровизация", "модернизация it", "интеграция с 1с",
      "электронный документооборот", "модернизация сервер"),
     "Возможно изменение цифровой инфраструктуры: выяснить действующие "
     "системы, интеграции, сопровождение и сроки."),
    ("INFRASTRUCTURE",
     ("серверное оборудование", "it-инфраструктура", "ит-инфраструктура",
      "локальная сеть", "компьютерные сети", "рабочие места",
      "сетевого оборудования", "сеть магазинов"),
     "Проверить возможную потребность в сетях, оборудовании, "
     "технической поддержке и администрировании."),
    ("RECRUITMENT",
     ("вакансия системного администратора", "вакансия программиста 1с",
      "ищет системного администратора", "специалист 1с в штат"),
     "Кадровый сигнал: установить, допустимо ли внешнее сопровождение; "
     "вакансия сама по себе не доказывает закупку услуг."),
    ("PROCUREMENT",
     ("тендер на", "закупка услуг", "запрос предложений",
      "конкурс на", "закупка оборудования"),
     "Проверить дату, актуальность, условия участия и заказчика "
     "в оригинальном закупочном документе."),
)

PUBLIC_NAME = re.compile(r"[\w \-«»\"'().,№&]{5,140}\Z", re.UNICODE)
ROLE_MAILBOX = frozenset({
    "info", "office", "contact", "hello", "mail", "support",
    "hr", "job", "jobs", "career", "recruitment",
    "zakupki", "tender", "procurement", "purchase",
    "sales", "commercial", "partners",
})
BLOCKED_DISCOVERY = (
    "bing.com", "google.com", "yandex.ru", "yandex.com",
    "duckduckgo.com", "brave.com", "facebook.com", "instagram.com",
    "vk.com", "t.me", "linkedin.com", "youtube.com",
)


class DeepResearchBlocked(ValueError):
    pass


def _norm(value):
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"\w+", value.casefold().replace("ё", "е")))


def _same_published_name(name, text):
    candidate, body = _norm(name), _norm(text)
    return len(candidate) >= 5 and len(candidate.split()) >= 2 and candidate in body


def published_company_name(item, source):
    """Do not send arbitrary private CRM company identifiers to search API."""
    employer = source.get("employer", {}) if isinstance(source, dict) else {}
    name = employer.get("name", "") if isinstance(employer, dict) else ""
    if (isinstance(name, str) and PUBLIC_NAME.fullmatch(name.strip())
            and not any(k in name.casefold() for k in ("не указан", "неизвест", "заказчик"))):
        return name.strip(), "PUBLIC_JOBPOSTING_EMPLOYER_CLAIM"
    text = source.get("text", "") if isinstance(source, dict) else ""
    candidate = item.get(PREFIX + "COMPANY_NAME") if type(item) is dict else ""
    if (isinstance(candidate, str) and PUBLIC_NAME.fullmatch(candidate.strip())
            and _same_published_name(candidate.strip(), text)
            and not any(k in candidate.casefold() for k in
                        ("не указан", "неизвест", "заказчик", "demo"))):
        return candidate.strip(), "PUBLIC_SOURCE_TEXT_MATCHES_CRM_NAME"
    return "", "NO_COMPANY_PUBLISHED_IN_SOURCE"


def _queries(name, kind):
    base = '"' + name.replace('"', "") + '" '
    second = ("филиал магазины сеть открытие расширение"
              if kind in ("new_store", "service-request", "direct-service",
                          "direct-request", "service-demand", "crm_need")
              else "ИТ инфраструктура 1С внедрение")
    return (
        base + "официальный сайт контакты",
        base + second,
        base + "проекты закупки вакансии новости",
    )


def _index_search(client, name, kind, token):
    if not (isinstance(client, httpx.Client) and isinstance(token, str)
            and 12 <= len(token) <= 200 and token.isascii()
            and "\n" not in token and "\r" not in token):
        raise DeepResearchBlocked("INDEX_AUTH_NOT_CONFIGURED")
    results = []
    for q in _queries(name, kind)[:MAX_SEARCH_CALLS]:
        try:
            response = client.get(
                SEARCH_URL, params={
                    "q": q, "count": 8, "country": "RU",
                    "search_lang": "ru", "safesearch": "strict"},
                headers={"X-Subscription-Token": token,
                         "Accept": "application/json"},
                follow_redirects=False, timeout=9.0)
            if (response.status_code != 200 or not 0 < len(response.content) <= 200000
                    or "application/json" not in response.headers.get("content-type", "")):
                raise DeepResearchBlocked("INDEX_UNAVAILABLE_OR_RATE_LIMITED")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise DeepResearchBlocked("INDEX_UNAVAILABLE_OR_RATE_LIMITED") from None
        if (not isinstance(payload, dict)
                or not isinstance(payload.get("web"), dict)
                or not isinstance(payload["web"].get("results"), list)):
            raise DeepResearchBlocked("INDEX_RESULT_UNVERIFIED")
        rows = payload["web"]["results"]
        if len(rows) > 20:
            raise DeepResearchBlocked("INDEX_RESULT_TOO_LARGE")
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            url, title, description = (row.get("url"), row.get("title", ""),
                                       row.get("description", ""))
            if (not isinstance(url, str) or len(url) > 1000
                    or not isinstance(title, str) or len(title) > 400
                    or not isinstance(description, str) or len(description) > 1200):
                continue
            # Search titles and snippets are candidate discovery ONLY.
            if not _same_published_name(name, title + " " + description):
                continue
            results.append(url)
    return list(dict.fromkeys(results))[:MAX_INDEX_RESULTS]


def _site_allowed(raw_url, *, source_host, accepted_hosts):
    try:
        u = urlsplit(raw_url)
        host = (u.hostname or "").lower()
        if (host == source_host
                or any(host == d or host.endswith("." + d)
                       for d in BLOCKED_DISCOVERY + tuple(MARKETPLACES))
                or (host not in accepted_hosts
                    and len(accepted_hosts) >= MAX_EXTERNAL_HOSTS)):
            return None
        # _host verifies scheme, credentials, port, unsafe/local names;
        # public DNS and robots are checked on every actual fetch by _page.
        _host(raw_url, frozenset({host}))
        return host
    except (ResearchBlocked, ValueError):
        return None


def _signals(text, url):
    lowered = text.casefold().replace("ё", "е")
    result = []
    for code, phrases, question in SIGNALS:
        for phrase in phrases:
            loc = lowered.find(phrase)
            if loc >= 0:
                # This quote is from fetched page body, NOT the index snippet.
                quote = " ".join(text[max(0, loc-75):loc+len(phrase)+105].split())[:200]
                result.append({"kind": code, "quote": quote,
                               "url": url, "manager_hypothesis": question,
                               "evidence_status": "UNVERIFIED_PUBLIC_CONTEXT"})
                break
    return result[:3]


def independent_company_research(client, *, company_name, signal_kind,
                                 source_host, search_token, now=None):
    """At most three queries and six external pages, never a write or outreach."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    if not (isinstance(company_name, str)
            and PUBLIC_NAME.fullmatch(company_name)
            and _norm(company_name) and len(_norm(company_name).split()) >= 2):
        return {"status": "NO_PUBLIC_COMPANY_IDENTITY", "pages": [],
                "contacts": [], "hypotheses": [], "checked_at": stamp}
    if not search_token:
        return {"status": "INDEX_API_KEY_NOT_CONFIGURED", "pages": [],
                "contacts": [], "hypotheses": [], "checked_at": stamp}
    try:
        hits = _index_search(client, company_name, signal_kind, search_token)
    except DeepResearchBlocked:
        return {"status": "INDEX_UNAVAILABLE_OR_UNVERIFIED", "pages": [],
                "contacts": [], "hypotheses": [], "checked_at": stamp}
    pages, contacts, hypotheses = [], [], []
    seen_hosts, seen_urls = set(), set()
    attempts = 0
    for url in hits:
        if len(pages) >= MAX_EXTERNAL_PAGES or attempts >= MAX_EXTERNAL_FETCHES:
            break
        host = _site_allowed(url, source_host=source_host,
                             accepted_hosts=seen_hosts)
        if host is None or url in seen_urls:
            continue
        seen_urls.add(url)
        attempts += 1
        try:
            page = _page(client, url, frozenset({host}))
        except (ResearchBlocked, httpx.HTTPError, ValueError):
            continue
        # A name occurrence is necessary but never sufficient to prove entity
        # ownership. Results about namesakes remain manual-review candidates.
        if not _same_published_name(company_name, page["text"]):
            continue
        seen_hosts.add(host)
        excerpt = " ".join(page["text"].split())[:380]
        pages.append({"url": url, "excerpt": excerpt, "host": host,
                      "identity": "POSSIBLE_COMPANY_NAME_MATCH_NOT_VERIFIED"})
        hypotheses.extend(_signals(page["text"], url))
        # Third-party directories and news pages must NOT be misattributed as
        # company mailboxes. Even own-domain contacts are just site candidates.
        path = urlsplit(url).path.lower()
        if not (path in ("", "/") or
                any(w in path for w in ("/contact", "/kontakt", "/o-kompanii",
                                      "/about", "/rekvizit"))):
            continue
        for c in _contacts(page, host, stamp):
            if c["type"] == "email":
                local = c["value"].split("@", 1)[0].lower()
                if local not in ROLE_MAILBOX:
                    continue
            contacts.append({**c,
                             "identity": "WEBSITE_CANDIDATE_NOT_LEGAL_ENTITY_PROOF",
                             "origin": "INDEPENDENT_PUBLIC_PAGE"})
    return {
        "status": "INDEX_SEARCH_DONE" if hits else "INDEX_NO_RELEVANT_RESULTS",
        "checked_at": stamp, "pages": pages[:MAX_EXTERNAL_PAGES],
        "contacts": list({(c["type"], c["value"], c["url"]): c
                          for c in contacts}.values())[:8],
        "hypotheses": list({(h["kind"], h["url"]): h
                            for h in hypotheses}.values())[:8],
        "index_candidates": min(len(hits), MAX_INDEX_RESULTS),
        "independent_pages_checked": len(pages),
        "external_fetch_attempts": attempts,
        "company_identity_verified": False,
        "contact_outreach_authorized": False,
    }


def append_independent_intelligence(fields, data):
    """Evidence-first addendum to existing nine fields; do not change Score."""
    if not isinstance(fields, dict) or set(fields) != AI_FIELDS:
        raise DeepResearchBlocked("NINE_AI_FIELDS_REQUIRED")
    if not isinstance(data, dict) or data.get("status") not in {
            "INDEX_SEARCH_DONE", "INDEX_NO_RELEVANT_RESULTS",
            "INDEX_API_KEY_NOT_CONFIGURED", "INDEX_UNAVAILABLE_OR_UNVERIFIED",
            "NO_PUBLIC_COMPANY_IDENTITY"}:
        return fields
    lines = [
        "НЕЗАВИСИМОЕ ИССЛЕДОВАНИЕ КОМПАНИИ ВО ВНЕШНЕМ ИНТЕРНЕТЕ",
        "Проверено: " + str(data.get("checked_at", "")),
        "Статус: " + data["status"],
        "Внешние сайты и совпадение названия НЕ доказывают юрлицо, проект, "
        "бюджет или согласие на контакт.",
    ]
    pages = data.get("pages") or []
    for entry in pages[:MAX_EXTERNAL_PAGES]:
        lines.append("ВНЕШНИЙ ИСТОЧНИК: " + entry["url"])
        lines.append("Опубликовано на странице (контекст, не факт сделки): "
                     + entry["excerpt"][:300])
    hypotheses = data.get("hypotheses") or []
    if hypotheses:
        lines.append("ВОЗМОЖНЫЕ ПРИЧИНЫ ПОТРЕБНОСТИ — ПРОВЕРИТЬ:")
        for h in hypotheses[:6]:
            lines.append("• " + h["manager_hypothesis"])
            lines.append("Фрагмент: " + h["quote"] + " | " + h["url"])
    else:
        lines.append("Независимых подтверждённых признаков проекта не найдено.")
    contacts = data.get("contacts") or []
    if contacts:
        lines.append("ДОПОЛНИТЕЛЬНЫЕ КОНТАКТЫ САЙТОВ-КАНДИДАТОВ (сверить владельца):")
        for c in contacts[:8]:
            lines.append(c["type"] + ": " + c["value"] + " | " +
                         c["role"] + " | внешний источник: " + c["url"])
    else:
        lines.append("Дополнительные корпоративные контакты не подтверждены.")
    lines.extend((
        "ПЛАН: сопоставить названия с ИНН и юридическим лицом; "
        "сверить даты публикаций, фактическое открытие/проект и потребность.",
        "До разрешения менеджера и собственных STOP/CRM-проверок "
        "не отправлять сообщения и не считать это квалифицированным лидом.",
    ))
    appendix = "\n".join(lines)
    out = dict(fields)
    for suffix, suffix_text in (
        ("AI_FULL_REPORT", "\n\n" + appendix),
        ("AI_MANAGER_BRIEF", "\nНезависимых внешних источников: " +
         str(len(pages)) + ". " + lines[-2]),
        ("AI_LIMITATIONS", " Статус независимого интернет-поиска: " +
         data["status"] + "; совпадения организаций требуют сверки."),
    ):
        code = PREFIX + suffix
        available = max(0, 12000-len(out[code]))
        out[code] += suffix_text[:available]
    if hypotheses:
        code = PREFIX + "AI_RECOMMENDATION"
        addition = (" Возможные потребности от внешних сигналов: " +
                    "; ".join(h["kind"] for h in hypotheses[:4]) +
                    ". Проверить доказательства и адресатов в полном отчёте.")
        out[code] = (out[code] + addition)[:12000]
    if contacts:
        code = PREFIX + "AI_NEXT_ACTION"
        out[code] = (out[code] +
                     " Сверить независимые контактные сведения и юрлицо.")[:12000]
    return out


def model_safe_context(independent):
    """Optional short public excerpt, never contact, PII or URLs to a model."""
    if not isinstance(independent, dict):
        return ""
    parts = []
    for page in (independent.get("pages") or [])[:3]:
        safe = _safe_model_excerpt(page.get("excerpt", ""))
        if safe:
            parts.append(safe[:170])
    return " | ".join(parts)[:550]
