"""Source-neutral professional Bitrix24 manager report — offline only."""
from dataclasses import replace

import pytest

from leadpilot.serviceit_ai_report import SignalReport
from leadpilot.serviceit_manager_ai_card import (
    AI_ORDER, KANBAN_FIELDS, DETAIL_SECTIONS,
    format_verified_card, manager_layout_manifest, CardPresentationBlocked,
)
from leadpilot.serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD,
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


def assessment(score=90):
    return {
        "lead_score": score,
        "score_version": "svc-score-v1",
        "mode": "RESEARCH_OR_REVIEW",
        "human_decision_required": True,
        "outreach_authorized_by_this_module": False,
    }


def report(score=90):
    return SignalReport(
        score=score,
        score_version="svc-score-v1",
        summary="Оценка сигнала 90/100; услуга для проверки: 1С.",
        service_recommendation="1С",
        evidence_quote="Компания ищет специалиста по сопровождению 1С",
        evidence_reference="evidence-sha256-test",
        possible_need="Возможное сопровождение 1С. Не подтверждено.",
        limitations="Бюджет и решение о покупке неизвестны.",
        verification_status="HYPOTHESIS_UNVERIFIED",
        score_explanation="Данные независимой проверки",
        manager_checklist=("Сверить источник.", "Проверить дубли.", "Получить разрешение менеджера."),
        next_action="Проверить источник и корпоративные допуски.",
        full_report="Сервис ИТ | AI-анализ\nОснование — проверенная дословная цитата.",
        provider="gigachat",
        model="GigaChat-2",
        timestamp="2026-11-02T16:00:00+00:00",
    )


def test_one_twelve_field_professional_report():
    fields = format_verified_card(report(), assessment())
    assert len(fields) == 12
    assert set(fields) == set(AI_FIELDS) | {SCORE_FIELD, VERSION_FIELD, BADGE_FIELD}
    assert fields[SCORE_FIELD] == 90
    assert fields[BADGE_FIELD].startswith("🟢 90/100")
    summary = fields[PREFIX + "AI_SUMMARY"]
    full = fields[PREFIX + "AI_FULL_REPORT"]
    assert "GigaChat" in summary and "НЕ разрешение на контакт" in summary
    assert "Контакт с клиентом: автоматически НЕ РАЗРЕШЁН" in full
    assert "Проверить источник" in summary
    assert "1. Сверить источник." in fields[PREFIX + "AI_MANAGER_BRIEF"]
    assert fields[PREFIX + "AI_QUOTE"] == report().evidence_quote
    assert fields[PREFIX + "AI_STATUS"] == "HYPOTHESIS_UNVERIFIED"
    assert all(key.startswith("UF_CRM_13_SVCIT_") for key in fields)


@pytest.mark.parametrize("score,expected", [
    (90, "🟢"), (70, "🟡"), (30, "🔴"),
])
def test_color_priority_is_deterministic_not_model_confidence(score, expected):
    fields = format_verified_card(report(score), assessment(score))
    assert fields[BADGE_FIELD].startswith(expected)
    assert fields[SCORE_FIELD] == score


@pytest.mark.parametrize("changed", [
    {"provider": "NOT_RUN"},
    {"verification_status": "NOT_RUN"},
    {"verification_status": "HOLD"},
])
def test_never_promote_not_run_or_hold_report(changed):
    with pytest.raises(CardPresentationBlocked, match="GENUINE_GIGACHAT_REPORT_REQUIRED"):
        format_verified_card(replace(report(), **changed), assessment())


def test_score_conflict_is_blocked():
    with pytest.raises(CardPresentationBlocked, match="SCORE_REPORT_CONFLICT"):
        format_verified_card(report(80), assessment(70))


def test_no_source_specific_fields_and_no_ui_writes():
    manifest = manager_layout_manifest()
    assert manifest["status"] == "LAYOUT_PLAN_NOT_APPLIED"
    assert manifest["entity_type_id"] == 1062
    assert manifest["category_id"] == 21
    assert manifest["crm_writes"] == 0
    assert manifest["user_interface_changed"] is False
    assert manifest["automation_enabled"] is False
    assert manifest["kanban_fields"] == list(KANBAN_FIELDS)
    assert len(manifest["ai_fields"]) == 9
    assert set(manifest["ai_fields"]) == set(AI_FIELDS)
    assert len(DETAIL_SECTIONS) == 4
    assert all("B2B-Center" not in field and "Bidzaar" not in field
               for section in manifest["detail_sections"]
               for field in section["fields"])


def test_no_outreach_or_source_mutation_in_payload():
    fields = format_verified_card(report(), assessment())
    assert not any(k in fields for k in (
        "title", "stageId", "categoryId", "contactIds", "phone", "email",
        PREFIX + "SIGNAL_URL", PREFIX + "COMPANY_INN",
        PREFIX + "STOP_REASON", PREFIX + "REVIEW_DECISION",
    ))


def test_manager_fields_order_is_complete():
    assert tuple(PREFIX + name for name in AI_ORDER) == tuple(
        PREFIX + name for name in (
            "AI_STATUS", "AI_SUMMARY", "AI_QUOTE", "AI_HYPOTHESIS",
            "AI_LIMITATIONS", "AI_RECOMMENDATION", "AI_NEXT_ACTION",
            "AI_MANAGER_BRIEF", "AI_FULL_REPORT",
        )
    )
