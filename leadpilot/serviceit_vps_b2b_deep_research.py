"""Post-delivery evidence-first research for exact B2B cards already in Bitrix.

HOLD means no sales/outreach approval, NOT a ban on public fact finding.
This is a separate one-use PG receipt; it never replays the original AI_NINE
card creation or modifies source, deal stage, score, task, contact or STOP.
Disabled unless the VPS coordinator explicitly opts in. No unverified LLM
claim is promoted to a buyer fact.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urljoin, urlsplit

import httpx

from .ai import config_from_env
from .serviceit_b2b_routerai_deep import (
    public_documents, investigate_routerai, cited_fields,
    source_only_investigation,
)
from .bitrix_metadata_audit import validate_webhook
from .serviceit_ai_report import plain
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_enrichment_worker import Rest, normalized, validate_identity, fingerprint
from .serviceit_preliminary_gigachat import model_hypothesis_fields
from .serviceit_public_evidence_research import (
    research_public, enrich_manager_fields, ResearchBlocked, _host,
    _page, _safe_model_excerpt,
)
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_vps_pg_queue import VpsBlocked, secret_file

RECEIPT = "B2B_RESEARCH_V4"
RESEARCHABLE_STATES = frozenset((
    "NOT_RUN_SOURCE_UNVERIFIED",
    "PUBLIC_RESEARCH_NO_VERIFIED_PUBLIC_TEXT",
    "PUBLIC_PAGES_ROUTERAI_UNVERIFIED",
    "PUBLIC_RESEARCH_ROUTERAI_NO_VERIFIED_QUOTES",
    "PUBLIC_RESEARCH_AI_NOT_RUN",
    "PUBLIC_RESEARCH_SOURCE_CITED_AI_UNVERIFIED",
    "PUBLIC_RESEARCH_SOURCE_NO_QUOTES_AI_UNVERIFIED",
))
MAX_PAGES = 6
SOURCE_ID = re.compile(r"B2B-CENTER-(\d{5,12})\Z")
MODEL_ALLOWED = frozenset(("tender-opportunity",))
SKIP_AI = "NOT_RUN_SOURCE_UNVERIFIED"


class B2BResearchBlocked(ValueError):
    pass


def safe_notice(notice, card, source_id, item):
    """A signed, immutable PG notice beats the decorated CRM Kanban title.

    The notice originally passed HMAC/source URL validation during intake.
    Still cross-check it with the exact verified CRM card before any network.
    Never take the publicly sent model title from a mutable Kanban heading.
    """
    match = SOURCE_ID.fullmatch(source_id) if isinstance(source_id, str) else None
    if (not match or type(card) is not int or type(notice) is not dict
            or notice.get("tender_id") != match.group(1)
            or notice.get("source") != "B2B-Center"
            or notice.get("source_url") != item.get(PREFIX + "SIGNAL_URL")
            or item.get(PREFIX + "SIGNAL_ID") != source_id
            or item.get(PREFIX + "SIGNAL_KIND") not in MODEL_ALLOWED
            or notice.get("original_publication_verified") is not False
            or notice.get("contact_approved") is not False):
        raise B2BResearchBlocked("B2B_NOTICE_CARD_IDENTITY_MISMATCH")
    url = notice["source_url"]
    host = _host(url, frozenset({"b2b-center.ru"}))
    if host not in ("b2b-center.ru", "www.b2b-center.ru"):
        raise B2BResearchBlocked("B2B_SOURCE_HOST_UNAPPROVED")
    subject = notice.get("subject")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 240:
        raise B2BResearchBlocked("B2B_NOTICE_TITLE_INVALID")
    # The original email subject may use punctuation that a model-safe
    # public-title validator intentionally prohibits. Do NOT strip an
    # arbitrary instruction or private name and silently send a guess.
    try:
        safe_title = plain(subject, 240)
    except ValueError:
        safe_title = ""
    return url, safe_title


def selected_one(connection):
    """Only delivered, verified B2B cards without this research receipt."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT b.card_id,b.source_id,b.notice_json "
            "FROM serviceit_vps_b2b_inbox b "
            "WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM serviceit_vps_native_receipts r "
            "WHERE r.namespace=%s AND r.card_id=b.card_id) "
            "ORDER BY b.received_at,b.source_id LIMIT 1",
            (RECEIPT,))
        return cur.fetchone()


def host_allowlist(env):
    names = [x.strip().lower() for raw in (
        env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", ""),
        env.get("SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS", "")
    ) for x in raw.split(",") if x.strip()]
    if not 1 <= len(names) <= 80:
        raise B2BResearchBlocked("PUBLIC_HOSTS_MISSING")
    hosts = frozenset(names)
    for name in hosts:
        _host("https://" + name + "/", hosts)
    if "b2b-center.ru" not in hosts:
        raise B2BResearchBlocked("B2B_HOST_NOT_ALLOWED")
    return hosts



def brave_notice_page(client, tender_id, allowed, key, browser=None, *, subject=""):
    """One search for a public tender ID, independently validate indexed links.

    Search titles and snippets are never accepted as evidence and never sent
    to the model. Every candidate must be on the official allowlisted B2B host
    and contain the exact published tender ID in separately fetched page text.
    """
    if (not key or not isinstance(key, str) or not 12 <= len(key) <= 200
            or not key.isascii() or "\n" in key or "\r" in key
            or not isinstance(tender_id, str)
            or not re.fullmatch(r"\d{5,12}", tender_id)):
        return None
    from .serviceit_independent_company_research import SEARCH_URL
    try:
        response = client.get(
            SEARCH_URL,
            params={"q": "site:b2b-center.ru " + tender_id,
                    "count": 5},
            headers={"X-Subscription-Token": key,
                     "Accept": "application/json",
                     "Cache-Control": "no-cache"},
            follow_redirects=False, timeout=10.0,
        )
        if (response.status_code != 200
                or not 0 < len(response.content) <= 160000
                or "application/json" not in response.headers.get("content-type", "")):
            return None
        payload = response.json()
        rows = payload.get("web", {}).get("results", [])
        if not isinstance(rows, list) or len(rows) > 20:
            return None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None
    for hit in rows[:5]:
        if not isinstance(hit, dict):
            continue
        candidate = hit.get("url")
        try:
            host = _host(candidate, frozenset({"b2b-center.ru"}))
            if host not in ("b2b-center.ru", "www.b2b-center.ru"):
                continue
            try:
                page = _page(client, candidate, allowed)
            except (ResearchBlocked, httpx.HTTPError, ValueError):
                page = browser(candidate) if browser is not None else None
            if not isinstance(page, dict):
                continue
            body = page.get("text", "")
            if not isinstance(body, str):
                continue
            exact = re.compile(r"(?<!\d)" + tender_id + r"(?!\d)")
            if exact.search(body) is None and subject:
                # The standard HTML extractor keeps only its FIRST 7K chars.
                # Search/category pages can contain the exact published lot
                # further down. Read the same robots-approved page in full,
                # never a search snippet, and require BOTH exact ID and
                # at least two distinctive procurement-title words nearby.
                from .serviceit_b2b_public_mirrors import matched_excerpt
                from .serviceit_public_evidence_research import (
                    _robots, _get, VisibleText,
                )
                _robots(client, candidate, allowed)
                raw = _get(client, candidate, allowed)
                parser = VisibleText()
                parser.feed(raw)
                body = matched_excerpt(" ".join(parser.parts), tender_id, subject)
            elif exact.search(body) is None:
                continue
            elif subject:
                from .serviceit_b2b_public_mirrors import matched_excerpt
                body = matched_excerpt(body, tender_id, subject)
            if not body or exact.search(body) is None:
                continue
            return {
                "status": "PUBLIC_PAGES_INSPECTED",
                "pages": [{
                    "url": candidate,
                    "excerpt": " ".join(body.split())[:4000],
                }],
                "contacts": [],
                "model_excerpt": _safe_model_excerpt(body),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "company_site_candidate": "",
                "employer_named_in_source": "",
                "browser_rendered_unverified": bool(page.get("browser_unverified")),
                "independent_company_research": None,
                "discovery": "BRAVE_EXACT_ID_PAGE_INDEPENDENTLY_READ",
            }
        except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
            continue
    return None


# Public tender pages use different LABELS: "Заказчики: ПАО «...»,"
# or "Заказчик ПАО Московская Биржа ИНН-КПП". Accept only explicitly
# labelled buyers (never an organizer), a legal form and either quoted
# name or bounded unquoted name ending BEFORE a known requisites heading.
# This is public buyer evidence, NOT official FNS/legal confirmation.
BUYER_NAME = (
    r'(?P<form>Общество\s+с\s+ограниченной\s+ответственностью|'
    r'Публичное\s+акционерное\s+общество|Акционерное\s+общество|'
    r'ООО|ПАО|АО|ОАО|ЗАО|ФГБУ|ГБУ|МБУ|ФГАУ|ФГАОУ)'
    r'(?:\s+(?P<descriptor>управляющая\s+компания))?'
    r'(?:\s*[«"](?P<title>[^»"«]{0,75}(?:«[^«»"]{2,75})?)[»"]|'
    r'\s+(?P<plain>[А-ЯЁ][А-ЯЁа-яёA-Za-z0-9 .()\-]{4,70}?)'
    r'(?=\s+(?:ИНН(?:-КПП)?|ОГРН|Документы|Окончание|Порядок|'
    r'Местонахождение|Подача|Начальная|Регион|Объект|Лоты)\b))'
)
PUBLIC_BUYER_LABEL = re.compile(
    r'(?iu)\b(?:заказчики|заказчик|покупатель|наименование\s+заказчика)\b'
    r'\s*[:—–-]?\s*' + BUYER_NAME
)
PUBLIC_SELF_BUYER = re.compile(
    r'(?iu)\bорганизатор\s*[:—–-]?\s*' + BUYER_NAME
    + r'[\s.;]+процедура\s+проводится\s*[:—–-]?\s*для\s+собственных\s+нужд\b'
)
FULL_BUYER_FORMS = {
    "общество с ограниченной ответственностью": "ООО",
    "публичное акционерное общество": "ПАО",
    "акционерное общество": "АО",
}


def _canonical_buyer_match(match):
    form = " ".join(match.group("form").split()).casefold()
    abbreviation = FULL_BUYER_FORMS.get(form, match.group("form").upper())
    title = match.group("title") or match.group("plain")
    if not title:
        raise B2BResearchBlocked("PUBLIC_BUYER_TITLE_MISSING")
    title = " ".join(title.split()).strip(" .,-").replace("«", "„")
    if "„" in title:
        title = title.rstrip("“") + "“"
    if not 4 <= len(title) <= 90:
        raise B2BResearchBlocked("PUBLIC_BUYER_TITLE_INVALID")
    qualifier = " УК" if match.group("descriptor") else ""
    return abbreviation + qualifier + " «" + title + "»"


def _self_buyer(row):
    """Use the first-party organizer only when it explicitly buys for itself."""
    try:
        url = row.get("url")
        if _host(url, frozenset({"b2b-center.ru"})) not in {
                "b2b-center.ru", "www.b2b-center.ru"}:
            return ""
        tender = re.fullmatch(r"/(?:app/market-next|market)/[a-z0-9-]+/tender-([0-9]{5,12})/?",
                             urlsplit(url).path)
        body = " ".join(row["excerpt"].split())
        if not tender or not re.search(r"(?<!\d)№\s*" + tender[1] + r"(?!\d)", body):
            return ""
        match = PUBLIC_SELF_BUYER.search(body)
        return _canonical_buyer_match(match) if match else ""
    except (ResearchBlocked, ValueError, KeyError, AttributeError):
        return ""


def public_buyer_candidate(findings):
    """Public buyer's legal label, NOT independently verified FNS identity."""
    if not isinstance(findings, dict) or findings.get("status") != "PUBLIC_PAGES_INSPECTED":
        return ""
    pages = (findings.get("pages") or [])[:3]
    for row in pages:
        if not isinstance(row, dict) or not isinstance(row.get("excerpt"), str):
            continue
        found = PUBLIC_BUYER_LABEL.search(" ".join(row["excerpt"].split()))
        if found:
            return _canonical_buyer_match(found)
    return next((buyer for row in pages if (buyer := _self_buyer(row))), "")


def _linked_b2b_buyer_inn(findings, buyer, client):
    """Read the same organizer's linked public profile for a self-purchase."""
    from .bitrix_1062_mapping import _is_valid_inn

    for row in findings["pages"][:3]:
        if _self_buyer(row) != buyer:
            continue
        url = row["url"]
        hosts = frozenset({"b2b-center.ru"})
        normalize = lambda text: " ".join(re.findall(r"(?iu)[а-яёa-z0-9]+", text.casefold()))
        try:
            for href, label in row.get("profile_links", []):
                if normalize(label) != normalize(buyer):
                    continue
                profile = urljoin(url, href)
                target = urlsplit(profile)
                if (target.hostname != urlsplit(url).hostname or target.query
                        or not re.fullmatch(r"/firms/[a-z0-9-]+/[0-9]+/?", target.path)):
                    continue
                text = _page(client, profile, hosts)["text"]
                short = re.search(r"(?iu)краткое\s+наименование\s+(.{4,120}?)\s+полное\s+наименование", text)
                if not short or normalize(short[1]) != normalize(buyer):
                    return None
                inn = re.search(r"(?iu)\bИНН\s*[:№]?\s*(\d{10}|\d{12})(?!\d)", text[short.end():short.end()+750])
                if inn and _is_valid_inn(inn[1]):
                    return {"inn": inn[1], "buyer_name": buyer, "public_source": profile,
                            "fns_verified": False, "provenance": "PUBLIC_B2B_LINKED_BUYER_PROFILE"}
                return None
        except (ResearchBlocked, httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
    return None


def _public_company_self_declaration(client, buyer, known_pages, token, *, allow_extra_discovery=False):
    """Bounded public-only company/site discovery; not legal FNS verification.

    A name match alone is not enough. Require the same legal name AND a
    checksum-valid INN printed together on a separately fetched HTTPS page.
    Search snippets are candidate URLs only, never evidence. Follow robots,
    public DNS and no-redirect policy from the shared public page fetcher.
    """
    if not buyer or not isinstance(known_pages, list):
        return None
    from urllib.parse import urlsplit
    from .serviceit_public_evidence_research import (
        _host, _page, EMAIL, _contacts, ResearchBlocked, MARKETPLACES,
    )
    from .bitrix_1062_mapping import _is_valid_inn
    from .serviceit_independent_company_research import SEARCH_URL
    from .serviceit_b2b_public_mirrors import MIRRORS
    match = re.fullmatch(r'(ООО|ПАО|АО|ОАО|ЗАО)\s+«([^»]{4,90})»', buyer)
    if match is None:
        return None
    form, name = match.groups()
    form_long = {"ООО": "общество с ограниченной ответственностью",
                 "ПАО": "публичное акционерное общество",
                 "АО": "акционерное общество"}.get(form, form)
    legal = re.compile(
        r'(?iu)(?:' + re.escape(form) + '|' +
        r'\s+'.join(map(re.escape, form_long.split())) +
        r')\s*[«"]' + re.escape(name) + r'[»"]')
    urls = [x.get("url") for x in known_pages[:3]
            if isinstance(x, dict) and isinstance(x.get("url"), str)]
    # Extra source-backed discovery is also needed when Brave found OTHER
    # company pages but none with the same legal buyer and printed INN.
    # The limit remains three previously found + five extra candidate URLs.
    if (allow_extra_discovery and isinstance(token, str)
            and token.isascii() and 12 <= len(token) <= 200):
        try:
            response = client.get(
                SEARCH_URL,
                params={"q": '"' + buyer + '" официальный сайт контакты ИНН',
                        "count": 5, "country": "RU", "search_lang": "ru"},
                headers={"X-Subscription-Token": token,
                         "Accept": "application/json"},
                follow_redirects=False, timeout=10.0)
            if (response.status_code == 200 and len(response.content) <= 160000
                    and "application/json" in response.headers.get("content-type", "")):
                rows = response.json().get("web", {}).get("results", [])
                if isinstance(rows, list):
                    urls += [x.get("url") for x in rows[:5] if isinstance(x, dict)]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
    results = []
    attempted = set()
    for raw in urls:
        if len(attempted) >= 8 or len(results) >= 3:
            break
        if not isinstance(raw, str) or raw in attempted:
            continue
        attempted.add(raw)
        try:
            host = _host(raw, frozenset({(urlsplit(raw).hostname or "").lower()}))
            # A tender aggregator prints the procurement's buyer legal name
            # and INN, but its footer phone belongs to the PLATFORM. It
            # cannot establish a buyer-owned official company mailbox/phone.
            if any(host == p or host.endswith("." + p)
                   for p in (MARKETPLACES | MIRRORS)):
                continue
            page = _page(client, raw, frozenset({host}))
            body = " ".join(page["text"].split())
        except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError, KeyError):
            continue
        valid = []
        for label in list(legal.finditer(body))[:8]:
            context = body[label.end():label.end()+480]
            # Never read the subsidiary's or another organization’s INN.
            context = re.split(
                r'(?iu)(?:ООО|ПАО|АО|ОАО|ЗАО|Общество\s+с\s+'
                r'ограниченной\s+ответственностью)\s*[«"]',
                context, maxsplit=1)[0]
            for inn in re.findall(r'(?iu)\bИНН\s*(?:[/–-]\s*КПП\s*)?(?:[/№:]|\s){0,6}(\d{10}|\d{12})(?!\d)',
                                  context):
                if _is_valid_inn(inn):
                    valid.append(inn)
        if len(set(valid)) != 1:
            continue
        inn = valid[0]
        stamp = datetime.now(timezone.utc).isoformat()
        company_contacts = []
        site_label = host.split(".")[-2] if "." in host else host
        for value in EMAIL.findall(body)[:24]:
            email = value.lower()
            domain = email.split("@")[-1]
            # Official-looking public mail can use a different TLD (.ru/.com).
            # Require the same domain stem as the legally named source website.
            if domain.split(".")[-2] != site_label:
                continue
            local = email.split("@")[0]
            if local not in {"info", "office", "contact", "sales", "tender",
                             "procurement", "1c", "support", "partners"}:
                continue
            company_contacts.append({
                "type": "email", "value": email, "url": raw,
                "role": "публичная корпоративная почта; право обращения не подтверждено",
                "checked_at": stamp,
                "origin": "LEGAL_NAME_AND_INN_ON_PUBLIC_PAGE_NOT_FNS",
            })
        # The public company's own phone is allowed ONLY after its legal
        # name and checksum-valid INN were read together on this exact page.
        # Previously _contacts() omitted the provenance marker, so V11's
        # strict checked_public_contacts() silently dropped every phone.
        # Never reclassify the general corporate line as the tender manager.
        for contact in _contacts(page, host, stamp):
            if contact.get("type") == "phone":
                company_contacts.append({
                    **contact,
                    "origin": "LEGAL_NAME_AND_INN_ON_PUBLIC_PAGE_NOT_FNS",
                    "role": "публичный телефон компании; тендерный ответственный не подтверждён",
                })
        results.append({"inn": inn, "url": raw, "contacts": company_contacts[:6]})
    if not results or len({x["inn"] for x in results}) != 1:
        return None
    contacts = {}
    for row in results:
        for c in row["contacts"]:
            contacts[(c["type"], c["value"])] = c
    return {"inn": results[0]["inn"], "buyer_name": buyer,
            "public_source": results[0]["url"],
            "contacts": list(contacts.values())[:8],
            "fns_verified": False, "provenance": "PUBLIC_CORPORATE_SELF_DECLARATION"}


def followup_public_buyer_research(findings, env, client, key):
    """Follow a verified public BUYER label to its candidate corporate pages.

    No new source intake, no automatic customer identity/INN certification,
    no contact sending, no CRM write. This does not read vendor email titles.
    """
    if env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED") != "YES":
        return findings
    if not isinstance(findings, dict) or findings.get("status") != "PUBLIC_PAGES_INSPECTED":
        return findings
    buyer = public_buyer_candidate(findings)
    if not buyer:
        return findings
    previous = findings.get("independent_company_research")
    linked = (None if published_buyer_inn_evidence(findings)
              else _linked_b2b_buyer_inn(findings, buyer, client))
    if linked:
        return {**findings, "independent_company_research": {
            **(previous if isinstance(previous, dict) else {}), "public_inn": linked,
        }}
    # A successful company search may contain only candidate pages, not INN.
    # Reuse those candidates; do not let its status suppress requisites lookup.
    reusable = (isinstance(previous, dict)
                and previous.get("company_name_provenance") ==
                    "PUBLIC_BUYER_LABEL_LEGAL_IDENTITY_UNVERIFIED")
    if (isinstance(previous, dict) and not reusable
            and previous.get("status") != "NO_PUBLIC_COMPANY_IDENTITY"):
        return findings  # Unattributed prior search must not trigger a second query.
    from .serviceit_independent_company_research import independent_company_research
    try:
        independent = dict(previous) if reusable else independent_company_research(
            client, company_name=buyer, signal_kind="tender",
            source_host="b2b-center.ru", search_token=key,
            llm_context_enabled=env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES",
        )
    except (ValueError, httpx.HTTPError):
        return findings
    if not isinstance(independent, dict):
        return findings
    independent["company_name_provenance"] = "PUBLIC_BUYER_LABEL_LEGAL_IDENTITY_UNVERIFIED"
    website = _public_company_self_declaration(
        client, buyer, independent.get("pages") or [], key,
        allow_extra_discovery=(env.get("SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED") == "YES"))
    if website:
        independent["public_inn"] = {
            key: website[key] for key in
            ("inn", "buyer_name", "public_source", "fns_verified", "provenance")
        }
        existing = independent.get("contacts") or []
        independent["contacts"] = list({
            (c["type"], c["value"]): c for c in
            (existing + website["contacts"]) if isinstance(c, dict)
            and c.get("type") in ("email", "phone") and c.get("value")
        }.values())[:8]
    result = dict(findings)
    result["independent_company_research"] = independent
    return result



def _supplement_thin_public_evidence(candidate, notice, env, client, *, index_hints=None):
    """Add at most one independently verified approved public detail page.

    Native/Brave often find only an indexed tender headline with no actionable
    buyer, conditions or documentation. Such a page must not suppress the
    already-approved free Tendem Search/Fetch fallback. This does NOT turn
    index text into verified evidence, run another model or write Bitrix.
    """
    if (not isinstance(candidate, dict)
            or candidate.get("status") != "PUBLIC_PAGES_INSPECTED"
            or not isinstance(candidate.get("pages"), list)):
        return candidate
    pages = [p for p in candidate["pages"] if isinstance(p, dict)
             and isinstance(p.get("url"), str)
             and isinstance(p.get("excerpt"), str)]
    if not pages:
        return candidate
    if (env.get("SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED") != "YES"
            or env.get("SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED") != "YES"):
        return candidate
    # Existing full independently fetched Tendem detail is already enough:
    # never query the same vendor again and never demote primary evidence.
    if any(p["url"].startswith(("https://tendem.ru/tenders/",
                                "https://www.tendem.ru/tenders/")) for p in pages):
        return candidate
    # Keywords and missing-value labels never prove dossier completeness.
    from .serviceit_b2b_free_public_fallback import read_free_public_mirror
    free = read_free_public_mirror(
        notice, env, client, index_hints=index_hints)
    if (not isinstance(free, dict)
            or free.get("status") != "PUBLIC_PAGES_INSPECTED"
            or not isinstance(free.get("pages"), list)):
        return candidate
    from .serviceit_b2b_remote_public_fetch import approved_url
    tender_id = notice.get("tender_id") if isinstance(notice, dict) else None
    if not isinstance(tender_id, str) or not re.fullmatch(r"\d{5,12}", tender_id):
        return candidate
    exact = re.compile(r"(?<!\d)" + re.escape(tender_id) + r"(?!\d)")
    seen = {p["url"] for p in pages}
    additions = [p for p in free["pages"]
                 if isinstance(p, dict) and isinstance(p.get("url"), str)
                 and isinstance(p.get("excerpt"), str)
                 and approved_url(p["url"])
                 and exact.search(p["excerpt"])
                 and p["url"] not in seen]
    additions = additions[:1]
    if not additions:
        return candidate
    updated = dict(candidate)
    updated["pages"] = pages + additions
    new_urls = {p["url"] for p in additions}
    updated["structured_facts"] = (
        list(candidate.get("structured_facts") or [])
        + [fact for fact in (free.get("structured_facts") or [])
           if isinstance(fact, dict) and any(
               isinstance(field, dict)
               and field.get("source_url") in new_urls
               for field in fact.values())]
    )
    updated["free_public_supplemented"] = True
    return updated


def _merge_independent_tinyfish(candidate, fetched, tender_id):
    """Merge one independently fetched public page without trusting Search text.

    Existing evidence and its order are preserved. The free provider has
    already validated the full source page, but we verify HTTPS host and
    exact signed tender number again at this cross-provider boundary.
    Never create a second CRM writer or treat a snippet as a verified fact.
    """
    if not isinstance(candidate, dict) or not isinstance(fetched, dict):
        return candidate
    if (fetched.get("status") != "PUBLIC_PAGES_INSPECTED"
            or not isinstance(fetched.get("pages"), list)
            or not isinstance(tender_id, str)
            or not re.fullmatch(r"\d{5,12}", tender_id)):
        return candidate
    from .serviceit_b2b_remote_public_fetch import approved_url
    exact = re.compile(r"(?<!\d)" + re.escape(tender_id) + r"(?!\d)")
    verified = []
    seen = set()
    for row in fetched["pages"][:2]:
        if not isinstance(row, dict):
            continue
        url, excerpt = row.get("url"), row.get("excerpt")
        if (not isinstance(url, str) or not approved_url(url)
                or not isinstance(excerpt, str)
                or not exact.search(excerpt) or url in seen):
            continue
        seen.add(url)
        verified.append({"url": url, "excerpt": excerpt[:4700]})
    if not verified:
        return candidate
    result = dict(candidate)
    prior = (list(candidate.get("pages") or [])
             if candidate.get("status") == "PUBLIC_PAGES_INSPECTED"
             else [])
    prior = [p for p in prior[:MAX_PAGES] if isinstance(p, dict)
             and isinstance(p.get("url"), str)
             and isinstance(p.get("excerpt"), str)]
    known = {p["url"] for p in prior}
    new_rows = [p for p in verified if p["url"] not in known]
    available = max(0, MAX_PAGES - len(prior))
    added = new_rows[:available]
    if added:
        result["status"] = "PUBLIC_PAGES_INSPECTED"
        result["pages"] = prior + added
        trusted_urls = {p["url"] for p in added}
        facts = [f for f in fetched.get("structured_facts", [])
                 if isinstance(f, dict) and any(
                     isinstance(v, dict) and v.get("source_url") in trusted_urls
                     for v in f.values())] if isinstance(
                         fetched.get("structured_facts"), list) else []
        result["structured_facts"] = (list(result.get("structured_facts") or [])
                                      + facts)
        if not prior:
            result["model_excerpt"] = (fetched.get("model_excerpt") or
                                       _safe_model_excerpt(added[0]["excerpt"]))
        result["free_public_supplemented"] = True
    result["tinyfish_independent_verified"] = True
    result["tinyfish_verified_page_count"] = len(verified)
    result["tinyfish_verified_page_limit"] = bool(new_rows[available:])
    return result


def verified_original_b2b_details(candidate, notice, client):
    """Prefer the full, safely reduced ORIGINAL public B2B details over a 4k head.

    Requires a canonical signed tender URL, robots + HTTPS GET, and exact
    number/title proof in fetched visible text. Does not read vendor email
    or search snippets, download gated files, or use an external AI provider.
    """
    from .serviceit_b2b_public_mirrors import detailed_public_excerpt
    from .serviceit_public_evidence_research import _robots, _get, VisibleText
    if not isinstance(candidate, dict) or not isinstance(notice, dict):
        return candidate
    url, tid, subject = (notice.get("source_url"), notice.get("tender_id"),
                         notice.get("subject"))
    if (type(url) is not str or type(tid) is not str
            or type(subject) is not str or not re.fullmatch(r"\d{5,12}", tid)):
        return candidate
    parsed = urlsplit(url)
    expected_path = re.fullmatch(
        r"/(?:app/market-next|market)/[a-z0-9-]+/tender-(\d{5,12})/?",
        parsed.path,
    )
    allowed = frozenset({"b2b-center.ru"})
    try:
        if (parsed.query or parsed.fragment or not expected_path
                or expected_path.group(1) != tid
                or _host(url, allowed) not in ("www.b2b-center.ru", "b2b-center.ru")):
            return candidate
        _robots(client, url, allowed)
        raw = _get(client, url, allowed)
        parser = VisibleText()
        parser.feed(raw)
        excerpt = detailed_public_excerpt(parser.parts, tid, subject)
    except (ResearchBlocked, httpx.HTTPError, ValueError, TypeError):
        return candidate
    if not excerpt:
        return candidate
    result = dict(candidate)
    pages = list(result.get("pages") or [])
    if any(type(p) is not dict for p in pages):
        return candidate
    original = next((p for p in pages if p.get("url") == url), {})
    # Carry exact first-party profile links to the buyer resolver. Previously
    # only the text survived, so the linked-profile path was never reachable.
    profile_links = []
    for href, label in parser.links[:2000]:
        target = urlsplit(urljoin(url, href))
        if (target.hostname == parsed.hostname and not target.query
                and not target.fragment
                and re.fullmatch(r"/firms/[a-z0-9-]+/[0-9]+/?", target.path)):
            profile_links.append((href, label))
    primary = {**original, "url": url, "excerpt": excerpt,
               "profile_links": profile_links[:12]}
    result["pages"] = ([primary] + [p for p in pages if p.get("url") != url])[:MAX_PAGES]
    result["status"] = "PUBLIC_PAGES_INSPECTED"
    result["original_b2b_detail_independently_read"] = True
    visible = " ".join(parser.parts)
    if re.search(r"(?iu)документация\s+от\s+организатора\s*[:.]?\s*"
                 r"доступно\s+зарегистрированным\s+пользователям", visible):
        result["document_access"] = "AUTH_REQUIRED"
    return result


def source_findings(item, notice, env, client, *, enable_v18=False,
                    connection=None):
    hosts = host_allowlist(env)
    browser = None
    if (env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"
            and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES"):
        from .serviceit_vps_browser_client import render_approved_url
        browser = lambda url: render_approved_url(url, hosts, client=client)
    # V32: run this approved public provider ONCE on every research invocation,
    # independently of Brave, native page quality and RouterAI success.
    # Its own strict gate permits only public/licensed sources. A transient
    # provider failure must not suppress all other research or allow a write.
    from .serviceit_b2b_free_public_fallback import read_free_public_mirror
    tinyfish_authorized = (
        env.get("SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED") == "YES"
        and (env.get("SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED") == "YES"
             or env.get("SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED") == "YES"))
    tinyfish = None
    if tinyfish_authorized:
        try:
            tinyfish = read_free_public_mirror(
                notice, env, client, index_hints=[])
        except Exception:
            # The stage remains incomplete and is eligible for the normal
            # V19 24-hour source retry. Never log credentials or vendor text.
            tinyfish = None
    def optional(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except Exception:
            return None

    key = optional(secret_file, env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True) or ""
    results = optional(
        research_public, item, client, hosts,
        deep_search_enabled=env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED") == "YES",
        deep_search_key=key,
        deep_llm_context_enabled=env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES",
        browser_render=browser,
        detail_excerpt_chars=4000,
    ) or {"status": "PUBLIC_RESEARCH_INCOMPLETE", "pages": []}
    # Original marketplace publication may require login. Brave is a URL
    # discovery fallback, not permission to bypass access controls.
    # An HTTP 200 login shell or generic index page is NOT evidence of
    # the tender. Require the public document to carry the same exact number.
    # The B2B notice's URL and ID alone are never a substitute for page text.
    exact = re.compile(r"(?<!\d)" + notice["tender_id"] + r"(?!\d)")
    page_proof = (results.get("status") == "PUBLIC_PAGES_INSPECTED"
                  and any(exact.search(p.get("excerpt", ""))
                          for p in results.get("pages", [])[:3]
                          if isinstance(p, dict)))
    from .serviceit_b2b_public_mirrors import brave_mirror_search
    v18 = (enable_v18 is True and
           env.get("SERVICEIT_B2B_V18_SEARCH_ENABLED") == "YES")
    index = None
    if v18 and key and env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES":
        from .serviceit_b2b_tender_index_v18 import tender_index_context
        # Exactly ONE additional official Brave Context request. Failure to
        # reach it never prevents other public research or existing intake.
        index = optional(tender_index_context,
            client, notice["tender_id"], notice["subject"], key)
    mirror = (optional(brave_mirror_search,
        client, notice["tender_id"], notice["subject"], key,
        browser=browser, index=index) if v18
        else optional(brave_mirror_search,
            client, notice["tender_id"], notice["subject"], key))

    def with_index(candidate):
        if not isinstance(candidate, dict):
            return candidate
        result = _merge_independent_tinyfish(
            candidate, tinyfish, notice["tender_id"])
        # Guarded rollout: offline fixtures make no extra source request.
        # The coordinator enables the verified public-detail path explicitly.
        if env.get("SERVICEIT_VPS_B2B_PRIMARY_DETAIL_ENABLED") == "YES":
            result = verified_original_b2b_details(result, notice, client)
        result = dict(result)
        result["tinyfish_independent_stage"] = (
            "DISABLED_BY_SOURCE_POLICY" if not tinyfish_authorized else
            "VERIFIED" if result.get("tinyfish_independent_verified") else
            "NO_VERIFIED_PUBLIC_PAGE_RETRYABLE")
        # Research Pro's generated report is NOT evidence by itself.
        # Only HTTPS public pages that were independently fetched and passed
        # the exact tender-ID/title and robots gates can enter normal
        # RouterAI/Bitrix dossier evidence. This also reuses finished async
        # Pro tasks for later V19/V20 consistency cycles.
        if (connection is not None and
                env.get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED")=="YES"):
            from .serviceit_b2b_tavily_research_mini_v47 import independently_verified_pages
            proven = optional(independently_verified_pages,connection,client,env,notice,browser) or []
            if proven:
                previous = list(result.get("pages") or [])
                seen={p.get("url") for p in previous if isinstance(p,dict)}
                additions=[p for p in proven if p.get("url") not in seen]
                if additions:
                    result["pages"]=(previous[:1]+additions+previous[1:])[:MAX_PAGES]
                    result["status"]="PUBLIC_PAGES_INSPECTED"
                    result["tavily_research_mini_public_verified"]=True
        # Re-run the ordinary verified-buyer follow-up only after all safely
        # merged public sources, so the model/CRM dossier sees both providers.
        result = optional(followup_public_buyer_research, result, env, client, key) or result
        # A readable notice may still omit the buyer or its INN. Try one
        # bounded Tavily discovery for those gaps as well as missing pages.
        if (connection is not None and env.get("SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED") == "YES"
                and not optional(published_buyer_inn_evidence, result)):
            from .serviceit_b2b_tavily_fallback_v44 import verified_mirror_search
            found = optional(verified_mirror_search, connection, client, env, notice, browser)
            if found:
                if result.get("status") != "PUBLIC_PAGES_INSPECTED":
                    result.update(found)
                else:
                    prior = result.get("pages") or []
                    known = {page["url"] for page in prior}
                    added = [page for page in found["pages"] if page["url"] not in known]
                    if added:
                        # The identity extractor checks the first three pages.
                        result["pages"] = (prior[:1] + added + prior[1:])[:MAX_PAGES]
                result["tavily_public_page_verified"] = True
        if v18:
            hints = (index.get("candidates") or []) if isinstance(index, dict) else []
            # Brave search hits are URL hints only; never factual proof.
            result["indexed_candidates"] = hints[:5]
            result["v18_search_status"] = (
                index.get("status", "INDEX_NOT_RUN") if isinstance(index, dict)
                else "INDEX_NOT_RUN")
        return result

    if not page_proof:
        candidate = optional(brave_notice_page,
            client, notice["tender_id"], hosts, key, browser,
            subject=notice["subject"])
        if candidate is not None:
            if mirror is not None:
                candidate = dict(candidate)
                candidate["pages"] = (candidate["pages"][:1] + mirror["pages"][:2])[:3]
            return with_index(candidate)
        if mirror is not None:
            return with_index(mirror)
        # The independent provider already ran, even though Brave failed.
        # with_index merges any separately validated TinyFish source evidence.
        return with_index({
            "status": "PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
            "pages": [], "contacts": [], "model_excerpt": "",
            "checked_at": datetime.now(timezone.utc).isoformat()})
    # Even when original publication can be read, Brave can independently
    # discover a richer PUBLIC mirror/detail page with the exact same ID.
    # Count at most one extra independently fetched page; never trust snippets.
    extra = optional(brave_notice_page,
        client, notice["tender_id"], hosts, key, browser,
        subject=notice["subject"])
    if extra is not None:
        existing = {row.get("url") for row in results.get("pages", [])}
        fresh = [p for p in extra["pages"] if p.get("url") not in existing]
        if fresh:
            results = dict(results)
            results["pages"] = (results.get("pages") or [])[:2] + fresh[:1]
            results["brave_exact_id_independent_page"] = True
    if mirror is not None:
        prior = (results.get("pages") or [])[:1]
        existing = {x.get("url") for x in prior}
        added = [x for x in mirror["pages"]
                 if isinstance(x, dict) and x.get("url") not in existing][:2]
        if added:
            results = dict(results)
            # Keep the independently confirmed official page, then the two
            # richest verified mirrors. Do NOT sacrifice a buyer/terms page
            # to preserve a generic company/site listing.
            results["pages"] = (prior + added)[:3]
            results["brave_public_mirror_independently_verified"] = True
    return with_index(results)


def model_safe_source_title(title, findings):
    """Model gets independently read, sanitized PUBLIC material only.

    The signed supplier-email title is not assumed to be published on the
    marketplace just because the URL returned HTTP 200 (which may be a login
    page). If the published text doesn't corroborate the notice title, send
    only the independently fetched safe excerpt, never the email subject.
    """
    if not title or not isinstance(findings, dict):
        return ""
    if (findings.get("status") != "PUBLIC_PAGES_INSPECTED"
            or not findings.get("pages")):
        return ""
    excerpt = findings.get("model_excerpt", "")
    if not isinstance(excerpt, str) or len(excerpt.strip()) < 24:
        return ""
    try:
        excerpt = plain(excerpt, 900)
    except ValueError:
        return ""
    if not re.search(r"(?i)(?:1[сc]|devops|(?:ит|it)[-\s]*специалист|"
                     r"программист|аутсорс|сервер|инфраструктур|"
                     r"битрикс|ккт|эдо|закупк[аи]|оборудован)", excerpt):
        return ""
    tokens = lambda value: set(re.findall(r"[^\W_]{3,}", value.casefold()))
    meaningful = tokens(title) - {"услуги", "оказание", "предоставление",
                                   "включая", "для", "компании"}
    public = tokens(excerpt)
    # At least two distinctive words from the original notice appear on
    # the public page. This is an inference gate, not proof of buyer identity.
    if len(meaningful & public) >= 2:
        return title
    return plain(excerpt[:240], 240)


def draft_fields(item, findings, *, ai=None, safe_title=""):
    """Keep all existing AI data; add cited facts and optional RouterAI hypothesis.

    The model is allowed to offer an interpretation, but cannot certify an
    entity, current purchasing intent, a deadline, or permission to contact.
    """
    current = {key: item.get(key) for key in AI_FIELDS}
    if any(type(value) is not str or not value.strip()
           or len(value) > 12000 for value in current.values()):
        raise B2BResearchBlocked("B2B_NINE_BASE_FIELDS_NOT_CONFIRMED")
    if item[PREFIX + "AI_STATUS"] not in RESEARCHABLE_STATES:
        raise B2BResearchBlocked("B2B_EXISTING_AI_RESULT_PRESERVED")
    result = {key: value.replace("GigaChat", "RouterAI")
              for key, value in current.items()}
    if ai is not None and safe_title:
        from .serviceit_preliminary_gigachat import PreliminaryAIBlocked
        try:
            result = model_hypothesis_fields(result, safe_title, ai)
        except PreliminaryAIBlocked:
            # An unreliable model response is not a finding.
            result[PREFIX + "AI_STATUS"] = "ROUTERAI_RESULT_UNVERIFIED"
    else:
        result[PREFIX + "AI_STATUS"] = "PUBLIC_RESEARCH_AI_NOT_RUN"
    if isinstance(findings, dict):
        result = enrich_manager_fields(result, findings,
                        signal_kind=item.get(PREFIX + "SIGNAL_KIND", ""))
    # Metadata is about research completeness, not lead qualification.
    result[PREFIX + "AI_LIMITATIONS"] = (
        result[PREFIX + "AI_LIMITATIONS"] +
        " HOLD означает только отсутствие подтверждения лида/контакта; "
        "публичное исследование выполняется независимо от коммерческого решения."
    )[:12000]
    # Production Bitrix rejected supplementary-plane emoji even with HTTP 200.
    safe = {
        key: "".join("•" if ord(c) > 0xFFFF else c for c in value
                     if ord(c) not in (0xFE0F, 0x200D, 0x20E3))
        for key, value in result.items()
    }
    safe = {name: value[:(12000 if name == PREFIX + "AI_FULL_REPORT"
                           else 4096)] for name, value in safe.items()}
    if set(safe) != AI_FIELDS or any(
            not 0 < len(v) <= (12000 if name == PREFIX + "AI_FULL_REPORT"
                               else 4096)
            or any(ord(c) > 0xFFFF for c in v)
            for name, v in safe.items()):
        raise B2BResearchBlocked("B2B_RESEARCH_OUTPUT_INVALID")
    return safe


def published_buyer_inn_evidence(findings):
    """Checksum-valid PUBLIC INN next to the labelled buyer: evidence, not FNS ID.

    Do not infer it from the marketplace's own requisites or a search snippet.
    It must be printed close to the same explicitly named legal buyer in one
    independently read procurement page. The official registry remains pending.
    """
    buyer = public_buyer_candidate(findings)
    if not buyer or not isinstance(findings, dict):
        return None
    from .bitrix_1062_mapping import _is_valid_inn
    found = []
    for row in (findings.get("pages") or [])[:3]:
        if not isinstance(row, dict):
            continue
        excerpt = row.get("excerpt")
        url = row.get("url")
        if not isinstance(excerpt, str) or not isinstance(url, str):
            continue
        for match in PUBLIC_BUYER_LABEL.finditer(" ".join(excerpt.split())):
            if _canonical_buyer_match(match) != buyer:
                continue
            tail = " ".join(excerpt.split())[match.end():match.end()+260]
            # Avoid mixing an unrelated organizer / platform legal identifier.
            tail = re.split(r"(?iu)\b(?:организатор|оператор|площадка|поставщик)"
                            r"\s*[:—–-]", tail, maxsplit=1)[0]
            for candidate in re.findall(
                    r"(?iu)\bИНН(?:\s*[-–]\s*КПП)?\s*[:№]?\s*(\d{10}|\d{12})(?!\d)", tail):
                if _is_valid_inn(candidate):
                    found.append((candidate, url))
    independent = findings.get("independent_company_research")
    if isinstance(independent, dict):
        corporate = independent.get("public_inn")
        if (isinstance(corporate, dict)
                and corporate.get("buyer_name") == buyer
                and corporate.get("fns_verified") is False
                and _is_valid_inn(corporate.get("inn"))):
            found.append((corporate["inn"], corporate["public_source"]))
    # Conflicting buyer INNs are not an identity. Leave ambiguous candidates out.
    unique = set(v for v, _ in found)
    if len(unique) != 1:
        return None
    inn = next(iter(unique))
    return {"inn": inn, "public_source": next(u for v, u in found if v == inn),
            "buyer_name": buyer, "fns_verified": False}


def cited_manager_fields(item, findings, report):
    """Combine RouterAI's verified quotes with independently sourced company data.

    Previously the successful RouterAI path skipped enrich_manager_fields().
    Thus verified public corporate contacts, additional sourced pages and
    independent company intelligence were lost precisely on the best cards.
    Never elevate a candidate contact to a verified legal buyer or send mail.
    """
    fields = cited_fields({key: item[key] for key in AI_FIELDS}, findings, report)
    fields = enrich_manager_fields(
        fields, findings, signal_kind=item.get(PREFIX + "SIGNAL_KIND", ""))
    public_inn = published_buyer_inn_evidence(findings)
    if public_inn:
        inn_header, _, inn_rest = fields[PREFIX + "AI_FULL_REPORT"].partition("\n")
        fields[PREFIX + "AI_FULL_REPORT"] = (
            inn_header + "\n\nРЕКВИЗИТЫ, НАПЕЧАТАННЫЕ В ПУБЛИЧНОМ ИСТОЧНИКЕ (НЕ ФНС)\n"
            + "Заказчик по тексту страницы: " + public_inn["buyer_name"]
            + "\nИНН-кандидат (контрольная сумма корректна): "
            + public_inn["inn"]
            + "\nИсточник: " + public_inn["public_source"]
            + "\nЮридическая идентичность и статус ЕГРЮЛ/ЕГРИП/МСП НЕ "
              "проверены. Не переносить в подтверждённый COMPANY_INN и "
              "не разрешать исходящий контакт без независимой сверки.\n\n"
            + inn_rest
        )
        fields[PREFIX + "AI_MANAGER_BRIEF"] = (
            "ИНН-кандидат из открытого текста: " + public_inn["inn"]
            + "; ФНС/право контакта ещё не подтверждены.\n"
            + fields[PREFIX + "AI_MANAGER_BRIEF"]
        )
    # Contact/evidence appendices may exceed the tested Bitrix text limits.
    # Normalize supplementary emoji and enforce the per-field CRM contract
    # *after* enrichment, not only before it.
    cleaned = {
        key: "".join("•" if ord(c) > 0xFFFF else c for c in value
                     if ord(c) not in (0xFE0F, 0x200D, 0x20E3))[
                         :12000 if key == PREFIX + "AI_FULL_REPORT" else 4096]
        for key, value in fields.items()
    }
    if (set(cleaned) != AI_FIELDS
            or any(type(v) is not str or not v
                   or len(v) > (12000 if k == PREFIX + "AI_FULL_REPORT" else 4096)
                   for k, v in cleaned.items())):
        raise B2BResearchBlocked("B2B_ENRICHED_REPORT_INVALID")
    return cleaned


def run_once(env, client, connection):
    """One card per 5-min coordinator cycle; no blind write or paid AI replay."""
    if env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED", "NO") != "YES":
        return {"status": "B2B_RESEARCH_DISABLED", "research": 0}
    from .serviceit_vps_worker import live_permitted
    if (not live_permitted(env)
            or env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") != "YES_1062_21"
            or env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT", "NO") != "NO"):
        raise B2BResearchBlocked("B2B_ROUTERAI_CUTOVER_GATES_REQUIRED")
    row = selected_one(connection)
    if row is None:
        return {"status": "B2B_RESEARCH_IDLE", "research": 0}
    card, source_id, notice = row
    PostgresNativeFence.validate_card(card)
    webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    rest = Rest(webhook, client)
    item = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})["item"])
    validate_identity(item, card)
    if item.get(PREFIX + "AI_STATUS") not in RESEARCHABLE_STATES:
        # Skip genuinely analyzed cards without starving later pending records.
        # PG namespace marks this exact card checked, but leaves CRM unchanged.
        fence = PostgresNativeFence(connection, RECEIPT)
        if fence.reserve(card) == "RESERVED":
            fence.finalize(card, "HOLD")
        return {"status": "B2B_RESEARCH_PRESERVE_EXISTING_AI", "research": 0}
    _, signed_title = safe_notice(notice, card, source_id, item)
    # Read-only public search/JS fetch can fail; it is never a reason to
    # suppress the task or classify an unverified buyer as qualified.
    findings = source_findings(item, notice, env, client,
                               connection=connection)
    docs = public_documents(findings)
    fence = PostgresNativeFence(connection, RECEIPT)
    if fence.reserve(card) != "RESERVED":
        return {"status": "B2B_RESEARCH_ALREADY_RESERVED", "research": 0}
    fields = None
    model_invoked = False
    model_error_code = "NOT_RUN"
    try:
        if docs:
            cfg = config_from_env({**env, "AI_PROVIDER": "routerai",
                                   "AI_ENABLE": "1",
                                   "ROUTERAI_API_KEY": secret_file(
                                       env, "SERVICEIT_ROUTERAI_API_KEY")})
            if cfg.provider != "routerai":
                raise B2BResearchBlocked("B2B_ROUTERAI_ONLY")
            # One bounded RouterAI call, with separately read PUBLIC excerpts
            # only. No vendor-email subject, CRM note, contact, INN or URLs.
            model_invoked = True
            try:
                report = investigate_routerai(client, cfg, findings)
                model_error_code = "NONE"
            except Exception as exc:
                # Fixed error codes only, never log any returned text, HTTP
                # bodies, buyer/CRM records, URL, or token. No paid replay.
                reason = str(exc)
                model_error_code = (reason if re.fullmatch(
                    r"[A-Z0-9_]{4,70}", reason)
                    and reason.startswith(("ROUTERAI_", "MODEL_"))
                    else "ROUTERAI_UNVERIFIED")
                report = None
            if report is None:
                # A rejected model schema is NOT proof the public data was
                # useless. Publish deterministic exact quotations and links,
                # with an explicit 'AI unverified' label and no paid retry.
                report = source_only_investigation(findings)
            fields = cited_manager_fields(item, findings, report)
        else:
            fields = draft_fields(item, findings)
            fields[PREFIX + "AI_STATUS"] = "PUBLIC_RESEARCH_NO_VERIFIED_PUBLIC_TEXT"
            fields[PREFIX + "AI_SUMMARY"] = (
                "Открытый первоисточник по номеру закупки не подтверждён. "
                "Проверка публикации и поиск Brave не дали доступного "
                "цитируемого текста; RouterAI не вызывался. "
                "HOLD относится только к квалификации и контакту.")
            # Signed original email intake is first-party source metadata,
            # NOT independently verified PUBLIC evidence. It may appear in
            # internal CRM, but is never sent to Brave or external RouterAI.
            title = signed_title or "Тема источника не прошла безопасную проверку"
            hints = []
            upper = title.casefold()
            for marker,service in (
                ("1с", "1С"), ("1c", "1С"), ("devops", "DevOps"),
                ("битрикс", "Битрикс24"), ("информационн", "ИБ"),
                ("аутсорс", "ИТ-аутсорсинг"), ("crm", "CRM"),
                ("сервер", "серверная инфраструктура")):
                if marker in upper and service not in hints:
                    hints.append(service)
            theme = ", ".join(hints) if hints else "требуется чтение оригинала"
            fields[PREFIX + "AI_RECOMMENDATION"] = (
                "Из темы входящего уведомления возможен профиль услуг: " +
                theme + ". Это гипотеза, а не опубликованное требование "
                "заказчика. Без доступа к первоисточнику запрещено "
                "подтверждать закупку, сумму, сроки и право контакта.")[:4096]
            fields[PREFIX + "AI_MANAGER_BRIEF"] = (
                "Тема подписанного B2B-уведомления: " + title +
                ". Направления для сверки: " + theme +
                ". Из открытых страниц подтверждённых фактов нет. "
                "Проверить права доступа к извещению, подробное ТЗ и срок; "
                "сохранить HOLD до коммерческой квалификации.")[:4096]
            fields[PREFIX + "AI_FULL_REPORT"] = (
                "СЕРВИС ИТ | B2B: НЕДОСТУПНО ПУБЛИЧНОЕ ДОКАЗАТЕЛЬСТВО\n"
                "Происхождение сведений: подтверждённое исходное B2B-уведомление, "
                "НЕ открытая публикация.\n"
                "Предмет из уведомления: " + title + "\n"
                "Потенциальные компетенции для проверки: " + theme + "\n"
                "Проверено: HTTPS-первоисточник, Brave по номеру, независимые "
                "публичные зеркала по номеру и тексту.\n"
                "Открытое подтверждение номера+предмета: НЕТ.\n"
                "Не установлено: юридическое лицо, полный объём ТЗ, бюджет, "
                "дедлайн, участие внешних подрядчиков, контакт и разрешение "
                "обращения. Не создавать выдуманные факты или Lead Score.\n"
                "Действие: уполномоченному сотруднику проверить оригинал "
                "закупки и при необходимости приложить разрешённый документ.\n"
                "RouterAI не вызывался без проверенного публичного текста; "
                "HOLD не ограничивает поиск, только коммерческие действия.")[:12000]
        fresh = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y"})["item"])
        validate_identity(fresh, card)
        if fingerprint(item) != fingerprint(fresh):
            raise B2BResearchBlocked("B2B_MANAGER_EDIT_CONFLICT")
        if any(fresh.get(k) != item.get(k) for k in AI_FIELDS):
            raise B2BResearchBlocked("B2B_AI_EDIT_CONFLICT")
        if not any(fields[k] != item[k] for k in AI_FIELDS):
            fence.finalize(card, "HOLD")
            return {"status": "B2B_RESEARCH_NO_NEW_EVIDENCE", "research": 0}
        # Durable salted digest BEFORE POST. The fence does not permit
        # replay if HTTP or exact field readback becomes uncertain.
        fence.checkpoint_write(card, fields)
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": fields})
        after = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y"})["item"])
        validate_identity(after, card)
        if any(after.get(k) != value for k, value in fields.items()):
            raise B2BResearchBlocked("B2B_RESEARCH_READBACK_UNCERTAIN")
        if not fence.finalize(card, "VERIFIED"):
            raise B2BResearchBlocked("B2B_RESEARCH_RECEIPT_UNCERTAIN")
        return {"status": "B2B_RESEARCH_NINE_FIELDS_VERIFIED",
                "research": 1, "routerai_invoked": model_invoked,
                "source_pages": min(len(findings.get("pages") or []), MAX_PAGES),
                "cited_facts": len(report.get("facts", [])) if model_invoked and report else 0,
                "routerai_failure_code": model_error_code,
                "customer_contacts_sent": 0, "lead_qualified": False}
    except Exception:
        # RESERVATION may precede paid model request and CRM write. Once
        # reached, failures are terminal for this version, not blindly retried.
        fence.finalize(card, "UNCERTAIN")
        return {"status": "B2B_RESEARCH_UNCERTAIN_NO_REPLAY",
                "research": 0, "routerai_invoked": model_invoked}
