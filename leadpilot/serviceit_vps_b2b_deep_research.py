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

import httpx

from .ai import AIProvider, config_from_env
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

RECEIPT = "B2B_RESEARCH_V1"
MAX_PAGES = 3
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



def brave_notice_page(client, tender_id, allowed, key, browser=None):
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
                    "count": 5, "country": "RU",
                    "search_lang": "ru", "safesearch": "strict"},
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
            if (not isinstance(body, str) or
                    re.search(r"(?<!\d)" + tender_id + r"(?!\d)", body) is None):
                continue
            return {
                "status": "PUBLIC_PAGES_INSPECTED",
                "pages": [{
                    "url": candidate,
                    "excerpt": " ".join(body.split())[:500],
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


def source_findings(item, notice, env, client):
    hosts = host_allowlist(env)
    browser = None
    if (env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"
            and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES"):
        from .serviceit_vps_browser_client import render_approved_url
        browser = lambda url: render_approved_url(url, hosts, client=client)
    key = secret_file(env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True)
    results = research_public(
        item, client, hosts,
        deep_search_enabled=env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED") == "YES",
        deep_search_key=key,
        deep_llm_context_enabled=env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES",
        browser_render=browser,
    )
    # Original marketplace publication may require login. Brave is a URL
    # discovery fallback, not permission to bypass access controls.
    if results.get("status") != "PUBLIC_PAGES_INSPECTED":
        candidate = brave_notice_page(
            client, notice["tender_id"], hosts, key, browser)
        if candidate is not None:
            return candidate
    return results


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
    if item[PREFIX + "AI_STATUS"] != SKIP_AI:
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
    if set(safe) != AI_FIELDS or any(not 0 < len(v) <= 12000
            or any(ord(c) > 0xFFFF for c in v) for v in safe.values()):
        raise B2BResearchBlocked("B2B_RESEARCH_OUTPUT_INVALID")
    return safe


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
    if item.get(PREFIX + "AI_STATUS") != SKIP_AI:
        # Skip genuinely analyzed cards without starving later pending records.
        # PG namespace marks this exact card checked, but leaves CRM unchanged.
        fence = PostgresNativeFence(connection, RECEIPT)
        if fence.reserve(card) == "RESERVED":
            fence.finalize(card, "HOLD")
        return {"status": "B2B_RESEARCH_PRESERVE_EXISTING_AI", "research": 0}
    _, title = safe_notice(notice, card, source_id, item)
    # Read-only public search/JS fetch can fail; it is never a reason to
    # suppress the task or classify an unverified buyer as qualified.
    findings = source_findings(item, notice, env, client)
    allowed = model_safe_source_title(title, findings)
    fence = PostgresNativeFence(connection, RECEIPT)
    if fence.reserve(card) != "RESERVED":
        return {"status": "B2B_RESEARCH_ALREADY_RESERVED", "research": 0}
    fields = None
    model_invoked = False
    try:
        answer = None
        if allowed:
            cfg = config_from_env({**env, "AI_PROVIDER": "routerai", "AI_ENABLE": "1",
                                   "ROUTERAI_API_KEY": secret_file(
                                       env, "SERVICEIT_ROUTERAI_API_KEY")})
            if cfg.provider != "routerai":
                raise B2BResearchBlocked("B2B_ROUTERAI_ONLY")
            # No CRM JSON, company contacts/INN, private notes or URL to LLM.
            with AIProvider(cfg, retry_unauthorized=False) as model:
                model_invoked = True
                answer = model.classify(allowed)
        fields = draft_fields(item, findings, ai=answer, safe_title=allowed)
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
                "customer_contacts_sent": 0, "lead_qualified": False}
    except Exception:
        # RESERVATION may precede paid model request and CRM write. Once
        # reached, failures are terminal for this version, not blindly retried.
        fence.finalize(card, "UNCERTAIN")
        return {"status": "B2B_RESEARCH_UNCERTAIN_NO_REPLAY",
                "research": 0, "routerai_invoked": model_invoked}
