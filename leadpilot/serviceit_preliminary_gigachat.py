"""Opt-in public-title GigaChat hypothesis for an otherwise unverified Bitrix card.

Only a public tender title is sent to the model, and only when both the privacy
decision and a strict hostname allowlist are set. Never send CRM item JSON,
company name, phone, INN, full document, private notes or source URL.

This preliminary path is NOT a verified company, Lead Score, permission to
contact or a qualification decision. Model confidence is not Lead Score.
"""
from __future__ import annotations

import math
from urllib.parse import urlsplit

from .serviceit_ai_report import plain
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .ai import ALLOWED

TITLE_MAX = 240


class PreliminaryAIBlocked(ValueError):
    pass


def allowed_public_title(item: dict, hosts: frozenset[str]) -> str:
    if not isinstance(item, dict) or not isinstance(hosts, frozenset) or not hosts:
        raise PreliminaryAIBlocked("PUBLIC_SOURCE_ALLOWLIST_MISSING")
    kind = item.get(PREFIX + "SIGNAL_KIND")
    if kind != "tender-opportunity":
        raise PreliminaryAIBlocked("PUBLIC_TENDER_KIND_REQUIRED")
    source_url = item.get(PREFIX + "SIGNAL_URL")
    if not isinstance(source_url, str) or len(source_url) > 1200:
        raise PreliminaryAIBlocked("PUBLIC_URL_REQUIRED")
    try:
        url = urlsplit(source_url)
        scheme, port, username, password, fragment, host = (
            url.scheme, url.port, url.username, url.password,
            url.fragment, url.hostname,
        )
    except ValueError:
        raise PreliminaryAIBlocked("PUBLIC_URL_INVALID") from None
    if (scheme != "https" or port not in (None, 443)
            or username or password or fragment or not host):
        raise PreliminaryAIBlocked("PUBLIC_URL_INVALID")
    host = host.lower()
    if host not in hosts and not any(host.endswith("." + item) for item in hosts):
        raise PreliminaryAIBlocked("PUBLIC_HOST_NOT_APPROVED")
    title = item.get("title")
    try:
        return plain(title, TITLE_MAX)
    except ValueError:
        raise PreliminaryAIBlocked("PUBLIC_TITLE_UNSAFE") from None


def model_hypothesis_fields(base_fields: dict, title: str, ai: dict) -> dict:
    """Upgrade nine preliminary review fields to an honest GigaChat hypothesis."""
    if not isinstance(base_fields, dict) or set(base_fields) != AI_FIELDS:
        raise PreliminaryAIBlocked("NINE_BASE_FIELDS_REQUIRED")
    try:
        title = plain(title, TITLE_MAX)
        if (not isinstance(ai, dict) or ai.get("provider") != "gigachat"
                or ai.get("origin") != "AI_hypothesis"
                or ai.get("quality_status") != "complete"
                or ai.get("service") not in ALLOWED
                or ai["service"] == "Требуется проверка"
                or not isinstance(ai.get("evidence_quote"), str)
                or ai["evidence_quote"] not in title):
            raise PreliminaryAIBlocked("GIGACHAT_UNVERIFIED_OUTPUT")
        confidence = ai.get("confidence")
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 < confidence <= 1):
            raise PreliminaryAIBlocked("GIGACHAT_CONFIDENCE_INVALID")
        quote = plain(ai["evidence_quote"], 300)
        possible_need = plain(ai["possible_need"], 400)
        service = plain(ai["service"], 80)
        model_name = plain(ai.get("model", "GigaChat"), 80)
    except (ValueError, KeyError, TypeError):
        raise PreliminaryAIBlocked("GIGACHAT_UNVERIFIED_OUTPUT") from None
    fields = dict(base_fields)
    status = "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    hypothesis = (
        "GigaChat (гипотеза, НЕ факт): " + possible_need
        + " Сначала проверьте первоисточник и наличие действительной закупочной потребности."
    )
    fields[PREFIX + "AI_STATUS"] = status
    fields[PREFIX + "AI_QUOTE"] = quote
    fields[PREFIX + "AI_HYPOTHESIS"] = hypothesis
    fields[PREFIX + "AI_RECOMMENDATION"] = (
        "Предполагаемое направление «" + service
        + "». Проверить с менеджером; подтверждения спроса нет."
    )
    fields[PREFIX + "AI_SUMMARY"] = (
        "GigaChat выделил возможную потребность по общедоступной теме тендера. "
        "Это НЕ подтверждённый лид. Lead Score не рассчитан. "
        "Нужны независимые проверки источника, ИНН, дублей, STOP и права контакта."
    )
    fields[PREFIX + "AI_LIMITATIONS"] += (
        " Модель видела только публичное название сигнала; "
        "её confidence не является Lead Score."
    )
    fields[PREFIX + "AI_FULL_REPORT"] = (
        "СЕРВИС ИТ — ПРЕДВАРИТЕЛЬНЫЙ AI-ОТЧЁТ ДЛЯ МЕНЕДЖЕРА\n"
        "Статус: ГИПОТЕЗА GigaChat / источник и покупатель НЕ подтверждены\n"
        "Lead Score: НЕ РАССЧИТАН\n"
        "Контакт: НЕ РАЗРЕШЁН\n"
        "Модель: " + model_name + "\n\n"
        "Дословный фрагмент названия источника:\n" + quote + "\n\n"
        "Возможная потребность (гипотеза):\n" + hypothesis + "\n\n"
        "Предложение:\n" + fields[PREFIX + "AI_RECOMMENDATION"] + "\n\n"
        "Что сделать менеджеру:\n" + fields[PREFIX + "AI_MANAGER_BRIEF"] + "\n\n"
        "Следующий шаг:\n" + fields[PREFIX + "AI_NEXT_ACTION"]
    )
    if not all(isinstance(v, str) and 0 < len(v) <= 12000 for v in fields.values()):
        raise PreliminaryAIBlocked("MODEL_REPORT_FIELD_TOO_LONG")
    return fields


def attempted_but_unverified_fields(base_fields: dict) -> dict:
    fields = dict(base_fields)
    fields[PREFIX + "AI_STATUS"] = "GIGACHAT_ATTEMPT_UNVERIFIED"
    fields[PREFIX + "AI_SUMMARY"] = (
        "Анализ GigaChat был запрошен, но достоверный результат не получен. "
        "Потребность не подтверждена, Score не рассчитан."
    )
    fields[PREFIX + "AI_FULL_REPORT"] = fields[PREFIX + "AI_FULL_REPORT"].replace(
        "GigaChat: NOT_RUN", "GigaChat: ATTEMPT_UNVERIFIED"
    )
    return fields
