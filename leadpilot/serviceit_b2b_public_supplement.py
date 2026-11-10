"""Evidence-only additive enrichment for previously analyzed B2B cards.

Does NOT call an LLM, a search endpoint, CRM or Postgres. Intended for
a future owner-gated, versioned, idempotent supplemental writer, not an
automatic replay of prior RouterAI receipts or a stage/score change.
"""
from __future__ import annotations

import re
from .serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
from .serviceit_vps_b2b_deep_research import published_buyer_inn_evidence

URL = re.compile(r"https://[A-Za-z0-9.-]{4,220}/[^\s]{0,700}\Z")
MAX_REPORT = 12000
MAX_OTHER = 4096


class SupplementBlocked(ValueError):
    pass


def _safe_contact(row):
    if not isinstance(row, dict):
        return None
    kind, value, url = row.get("type"), row.get("value"), row.get("url")
    if kind not in ("email", "phone") or not all(
            isinstance(s, str) for s in (value, url)):
        return None
    if not URL.fullmatch(url) or len(value) > 170 or "\n" in value:
        return None
    if kind == "email":
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}@[A-Za-z0-9.-]{2,110}\.[A-Za-z]{2,15}", value):
            return None
    elif not re.fullmatch(r"[+\d ()-]{8,26}", value):
        return None
    return kind, value, url


def supplement_existing_ai(item: dict, findings: dict) -> dict | None:
    """Preserve original citations/AI status; add verifiable public facts only."""
    if not isinstance(item, dict) or any(
            not isinstance(item.get(k), str) or not item[k].strip()
            for k in AI_FIELDS):
        raise SupplementBlocked("BASE_NINE_AI_FIELDS_REQUIRED")
    if not isinstance(findings, dict) or findings.get("status") != "PUBLIC_PAGES_INSPECTED":
        return None
    independent = findings.get("independent_company_research")
    contacts = list(findings.get("contacts") or [])
    if isinstance(independent, dict):
        contacts += list(independent.get("contacts") or [])
    verified = []
    for row in contacts[:18]:
        contact = _safe_contact(row)
        if contact and contact not in verified:
            verified.append(contact)
    published_inn = published_buyer_inn_evidence(findings)
    if not verified and not published_inn:
        return None
    lines = ["ДОПОЛНИТЕЛЬНЫЕ ПРОВЕРЕННЫЕ ПУБЛИКАЦИИ (НЕ ФНС И НЕ РАЗРЕШЕНИЕ НА СВЯЗЬ)"]
    if published_inn:
        inn, url = published_inn["inn"], published_inn["public_source"]
        if URL.fullmatch(url):
            lines.extend([
                "Публично названный заказчик: " + published_inn["buyer_name"],
                "ИНН-кандидат (прошёл проверку контрольной суммы): " + inn,
                "Подтверждающий сайт/публикация: " + url,
                "Статус ЕГРЮЛ/ЕГРИП по ФНС: НЕ ПРОВЕРЕН.",
            ])
    for kind, value, url in verified[:8]:
        lines.append(("Публичный email" if kind == "email" else "Публичный телефон")
                     + ": " + value + " | источник: " + url)
    lines.append("Контакт компании и законность обращения требуют отдельного подтверждения.")
    addon = "\n".join(lines)
    old_report = item[PREFIX + "AI_FULL_REPORT"]
    old_brief = item[PREFIX + "AI_MANAGER_BRIEF"]
    # Never truncate the previously cited report or silently lose old facts.
    if len(addon) + len(old_report) + 5 > MAX_REPORT:
        return None
    fresh = [line for line in lines if line not in old_report]
    if len(fresh) <= 2:
        return None
    brief = "Новые публичные реквизиты/контакты: " + (
        (published_inn["inn"] + " (НЕ ФНС); ") if published_inn else ""
    ) + ", ".join(v for _, v, _ in verified[:3]) + ". Источники в полном отчёте."
    new_brief = (brief + "\n" + old_brief) if brief not in old_brief else old_brief
    if len(new_brief) > MAX_OTHER:
        return None
    out = {key: item[key] for key in AI_FIELDS}
    out[PREFIX + "AI_FULL_REPORT"] = old_report + "\n\n" + addon
    out[PREFIX + "AI_MANAGER_BRIEF"] = new_brief
    if (set(out) != AI_FIELDS
            or any(len(v) > (MAX_REPORT if key == PREFIX + "AI_FULL_REPORT" else MAX_OTHER)
                   for key, v in out.items())):
        raise SupplementBlocked("RESULT_FIELD_LIMIT_EXCEEDED")
    return out


def source_backed_company_inn_update(item: dict, findings: dict) -> dict | None:
    """Prepare ONLY 3 CRM fields if public tender + independent buyer site match.

    Legal source claim is *not* a current EGRUL verification.
    This is intentionally pure: no CRM, Postgres, HTTP or model call.
    """
    from urllib.parse import urlsplit
    from .bitrix_1062_mapping import _is_valid_inn
    INN = PREFIX + "COMPANY_INN"
    REPORT = PREFIX + "AI_FULL_REPORT"
    BRIEF = PREFIX + "AI_MANAGER_BRIEF"
    if (not isinstance(item, dict) or item.get(INN) not in (None, "")
            or not isinstance(findings, dict)
            or findings.get("status") != "PUBLIC_PAGES_INSPECTED"):
        return None
    independent = findings.get("independent_company_research")
    website = independent.get("public_inn") if isinstance(independent, dict) else None
    public = published_buyer_inn_evidence(findings)
    if not isinstance(website, dict) or not isinstance(public, dict):
        return None
    inn = website.get("inn")
    if (not isinstance(inn, str) or not _is_valid_inn(inn)
            or website.get("fns_verified") is not False
            or website.get("provenance") != "PUBLIC_CORPORATE_SELF_DECLARATION"
            or inn != public.get("inn")
            or website.get("buyer_name") != public.get("buyer_name")
            or website.get("public_source") != public.get("public_source")
            or not isinstance(public.get("public_source"), str)
            or not URL.fullmatch(public["public_source"])):
        return None
    # An independent buyer's company page must not actually be a
    # tender marketplace mirror. The legal identity remains unverified by FNS.
    url = urlsplit(public["public_source"])
    if url.scheme != "https" or not url.hostname:
        return None
    from .serviceit_public_evidence_research import MARKETPLACES
    if any(url.hostname == host or url.hostname.endswith("." + host)
           for host in MARKETPLACES):
        return None
    ai = supplement_existing_ai(item, findings)
    if ai is None:
        return None
    return {INN: inn, REPORT: ai[REPORT], BRIEF: ai[BRIEF]}
