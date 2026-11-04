"""Three Signal types are shown from ALL new-card paths, no CRM title rewrite."""
from datetime import datetime, timezone

import pytest

from leadpilot.serviceit_kanban_tile import tile_heading_for_signal
from leadpilot.serviceit_three_signal_triage import classify_signal
from leadpilot.serviceit_windows_autofill import preliminary_fields
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX, hold_analysis_fields
from tests.test_serviceit_manager_ai_card import report, assessment
from leadpilot.serviceit_manager_ai_card import format_verified_card, CardPresentationBlocked

NOW = datetime(2026,11,4,12,0,tzinfo=timezone.utc)


@pytest.mark.parametrize("title,kind,source,expected", [
    ("Закупка услуг 1С", "tender-opportunity", "B2B-Center", "📑 ТЕНДЕР · Закупка услуг 1С"),
    ("Вакансия: программист 1С", "1c_vacancy", "Работа России", "👥 ВАКАНСИЯ · Вакансия: программист 1С"),
    ("Нужны услуги ИТ-аутсорсинга", "direct_request", "Входящая форма", "🎯 ПРЯМАЯ УСЛУГА · Нужны услуги ИТ-аутсорсинга"),
    ("Тендер: ищем в штат сотрудника", "", "", "⚪ УТОЧНИТЬ ТИП · Тендер: ищем в штат сотрудника"),
])
def test_preliminary_kanban_headline_and_hold_no_fake_score(title, kind, source, expected):
    item = {
        "id": 88, "title":title,
        PREFIX+"SIGNAL_KIND":kind,
        PREFIX+"SIGNAL_SOURCE":source,
    }
    fields = preliminary_fields(item)
    assert set(fields) == AI_FIELDS
    assert fields[PREFIX+"AI_SUMMARY"].startswith(expected + "\nHOLD")
    assert "Lead Score не рассчитывался" in fields[PREFIX+"AI_SUMMARY"]
    assert "Lead Score: НЕ РАССЧИТАН" in fields[PREFIX+"AI_FULL_REPORT"]
    assert "title" not in fields and PREFIX+"LEAD_SCORE" not in fields


def test_tender_source_handoff_uses_same_tile_header_and_preserves_full_nine_fields():
    notice = {"source":"B2B-Center", "tender_id":"4661617",
        "original_publication_verified":False, "qualified_lead":False,
        "contact_approved":False, "buyer_inn":None, "fns_msp":None,
        "subject":"Сопровождение 1С по договору"}
    fields = hold_analysis_fields(notice, ["1C"])
    assert len(fields) == 9
    assert fields[PREFIX+"AI_SUMMARY"].startswith("📑 ТЕНДЕР · Сопровождение 1С по договору\nHOLD")
    assert "Lead Score: НЕ РАССЧИТАН" in fields[PREFIX+"AI_FULL_REPORT"]


def test_verified_manager_report_has_signal_type_before_real_formal_score():
    form = format_verified_card(report(), assessment(), tile_heading="👥 ВАКАНСИЯ · Программист 1С")
    assert form[PREFIX+"AI_SUMMARY"].startswith("👥 ВАКАНСИЯ · Программист 1С\n🟢 90/100")
    assert len(form)==12
    assert form[PREFIX+"LEAD_SCORE"] == 90
    assert "title" not in form and "stageId" not in form


@pytest.mark.parametrize("bad",["☠️ injected", "📑 ТЕНДЕР · unsafe\nsecond", "📑 ТЕНДЕР · <script>"])
def test_untrusted_verified_heading_not_allowed(bad):
    with pytest.raises(CardPresentationBlocked,match="KANBAN_HEADING_INVALID"):
        format_verified_card(report(),assessment(),tile_heading=bad)


def test_duplicate_source_created_icon_removed_from_tile_header():
    triage = classify_signal({"title":"Закупка 1С","description":"",
        "signal_kind":"tender-opportunity","source":"B2B-Center","region":"","company":""},now=NOW)
    assert tile_heading_for_signal(triage, "📑 ТЕНДЕР Закупка 1С") == "📑 ТЕНДЕР · Закупка 1С"


def test_untrusted_title_or_missing_type_fails_safe():
    triage = classify_signal({"title":"Сигнал","description":"",
        "signal_kind":"","source":"","region":"","company":""},now=NOW)
    assert tile_heading_for_signal(triage, "http://example.org") == "⚪ УТОЧНИТЬ ТИП · Сигнал требует проверки"
    assert tile_heading_for_signal(triage, "abc\ninn 999") == "⚪ УТОЧНИТЬ ТИП · Сигнал требует проверки"
    assert tile_heading_for_signal(triage, "a"*200).startswith("⚪ УТОЧНИТЬ ТИП · ")
