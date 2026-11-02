"""Manager-facing Bitrix24 text presentation for all approved ServiceIT AI reports.

Pure formatting. Never connects to CRM, changes user fields, infers permissions,
qualifies a supplier email or calls GigaChat. The universal event worker applies
this same contract regardless of the origin/source of the card.

Bitrix smart-process 1062 / category 21 uses string user fields. Avoid HTML,
Markdown or UI-injected code: the long report is plain readable Russian text.
"""
from __future__ import annotations

from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, fields_from_assessment,
)

ENTITY_TYPE_ID = 1062
CATEGORY_ID = 21

# Layout PLAN ONLY. Bitrix layout is a user/account setting and requires
# authenticated corporate UI acceptance; this file never changes UI config.
KANBAN_FIELDS = (
    "title",
    BADGE_FIELD,
    PREFIX + "AI_STATUS",
    PREFIX + "SUGGESTED_SERVICE",
    PREFIX + "AI_SUMMARY",
)
DETAIL_SECTIONS = (
    ("01 · Ключевое решение", (
        BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, PREFIX + "AI_STATUS",
        PREFIX + "AI_SUMMARY", PREFIX + "AI_NEXT_ACTION",
    )),
    ("02 · Исходные данные и доказательства", (
        PREFIX + "SIGNAL_SOURCE", PREFIX + "SIGNAL_URL",
        PREFIX + "COMPANY_NAME", PREFIX + "COMPANY_INN",
        PREFIX + "AI_QUOTE", PREFIX + "AI_LIMITATIONS",
    )),
    ("03 · AI-гипотеза и план менеджера", (
        PREFIX + "AI_HYPOTHESIS", PREFIX + "AI_RECOMMENDATION",
        PREFIX + "AI_MANAGER_BRIEF",
    )),
    ("04 · Полный отчёт", (PREFIX + "AI_FULL_REPORT",)),
)
AI_ORDER = (
    "AI_STATUS", "AI_SUMMARY", "AI_QUOTE", "AI_HYPOTHESIS",
    "AI_LIMITATIONS", "AI_RECOMMENDATION", "AI_NEXT_ACTION",
    "AI_MANAGER_BRIEF", "AI_FULL_REPORT",
)


class CardPresentationBlocked(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise CardPresentationBlocked(code)


def format_verified_card(report, assessment: dict) -> dict:
    """Build the existing 9 AI + 3 scoring fields, no new Bitrix schema.

    This is for a separately verified source with a genuine, opt-in GigaChat
    result. Independently unverified sources must instead stay HOLD/NOT_RUN.
    """
    _require(report is not None and isinstance(assessment, dict),
             "REPORT_OR_ASSESSMENT_INVALID")
    _require(getattr(report, "verification_status", None) == "HYPOTHESIS_UNVERIFIED"
             and getattr(report, "provider", None) == "gigachat",
             "GENUINE_GIGACHAT_REPORT_REQUIRED")
    score = fields_from_assessment(assessment)
    _require(report.score == score[SCORE_FIELD]
             and report.score_version == score[VERSION_FIELD],
             "SCORE_REPORT_CONFLICT")
    _require(isinstance(report.evidence_quote, str)
             and 0 < len(report.evidence_quote) <= 300,
             "QUOTE_INVALID")
    _require(all(isinstance(getattr(report, attr, None), str)
                 and getattr(report, attr).strip()
                 for attr in (
                     "summary", "possible_need", "limitations",
                     "service_recommendation", "next_action", "full_report",
                 )), "INCOMPLETE_REPORT")
    checklist = getattr(report, "manager_checklist", None)
    _require(isinstance(checklist, (list, tuple))
             and 1 <= len(checklist) <= 12
             and all(isinstance(s, str) and 0 < len(s) <= 500 for s in checklist),
             "MANAGER_CHECKLIST_INVALID")

    badge = score[BADGE_FIELD]
    summary = (
        badge + "\n"
        + "AI: гипотеза GigaChat, НЕ доказательство покупки и НЕ разрешение на контакт.\n"
        + report.summary + "\n"
        + "Действие: " + report.next_action
    )
    full = (
        "СЕРВИС ИТ — AI-ОТЧЁТ ДЛЯ МЕНЕДЖЕРА\n"
        + badge + "\n"
        + "Статус: гипотеза GigaChat, проверка менеджера обязательна\n"
        + "Контакт с клиентом: автоматически НЕ РАЗРЕШЁН\n"
        + "────────────────────────────────\n\n"
        + report.full_report
    )
    brief = "\n".join(f"{i}. {step}" for i, step in enumerate(checklist, 1))
    values = (
        report.verification_status,
        summary,
        report.evidence_quote,
        report.possible_need,
        report.limitations,
        report.service_recommendation,
        report.next_action,
        brief,
        full,
    )
    ai_fields = {PREFIX + suffix: val
                 for suffix, val in zip(AI_ORDER, values)}
    _require(set(ai_fields) == AI_FIELDS
             and all(isinstance(v, str) and v.strip()
                     for v in ai_fields.values()), "NINE_AI_FIELDS_INVALID")
    # Never touch source metadata, identity, stage, task, outreach or clients.
    return {**ai_fields, **score}


def manager_layout_manifest() -> dict:
    """Read-only layout handoff for authenticated Bitrix UI configuration."""
    return {
        "status": "LAYOUT_PLAN_NOT_APPLIED",
        "entity_type_id": ENTITY_TYPE_ID,
        "category_id": CATEGORY_ID,
        "kanban_fields": list(KANBAN_FIELDS),
        "detail_sections": [
            {"title": name, "fields": list(fields)}
            for name, fields in DETAIL_SECTIONS
        ],
        "ai_fields": [PREFIX + name for name in AI_ORDER],
        "score_fields": [SCORE_FIELD, VERSION_FIELD, BADGE_FIELD],
        "user_interface_changed": False,
        "crm_writes": 0,
        "automation_enabled": False,
    }
