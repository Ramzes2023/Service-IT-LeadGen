"""Manager task drafting is context-aware, deterministic and conservative."""
from datetime import datetime, timezone

import pytest

from leadpilot.serviceit_stage3_all_new_task_automation import manager_task_fields

NOW = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("kind,source,title,prefix,phrase", [
    ("tender", "B2B-Center", "Закупка услуг 1С",
     "📑 ТЕНДЕР", "Проверить тендер"),
    ("vacancy", "trudvsem", "Вакансия программиста 1С",
     "👥 ВАКАНСИЯ", "Проверить вакансию"),
    ("direct", "inbound", "Ищем подрядчика для Битрикс24",
     "🎯 ЗАПРОС УСЛУГ", "Квалифицировать прямую заявку"),
    ("", "", "Неясный запрос на оборудование",
     "⚪ УТОЧНИТЬ ТИП", "Проверить источник"),
])
def test_manager_task_classifies_three_signals_and_unknown(
        kind, source, title, prefix, phrase):
    item = {
        "title": title,
        "UF_CRM_13_SVCIT_SIGNAL_KIND": kind,
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": source,
        "UF_CRM_13_SVCIT_REGION": "Москва",
    }
    task = manager_task_fields(item, 120, 42, "T426_120", now=NOW)
    assert task["TITLE"].startswith(prefix)
    assert phrase in task["TITLE"]
    assert task["RESPONSIBLE_ID"] == 42
    assert task["UF_CRM_TASK"] == ["T426_120"]
    description = task["DESCRIPTION"]
    assert "\n" in description
    assert "ВНУТРЕННЯЯ ПРОВЕРКА" in description
    assert "НЕ подтверждена" in description
    assert "НЕ звонить" in description
    assert "связаться" not in task["TITLE"].lower()
    assert "Lead Score" in description


def test_unknown_title_does_not_create_qualified_lead_claim():
    task = manager_task_fields({}, 120, 42, "T426_120", now=NOW)
    assert "Проверить источник" in task["TITLE"]
    assert "ГОТОВЫЙ ПРОДАЖНЫЙ ЛИД" in task["DESCRIPTION"]
    assert "НЕ" in task["DESCRIPTION"]
