"""Regression suite: demonstration action text cannot carry contact data."""
import pytest

from leadpilot.bitrix_synthetic_text_guard import synthetic_action_is_safe


@pytest.mark.parametrize("text", [
    "Call +7 999 000 0000 to discuss",
    "Связаться по +7 (999) 000-00-00",
    "Наберите 8-999-000-00-00 для связи",
    "Phone 89990000000 direct",
    "Contact 9990000000 today",
    "Please call +1 (415) 555-0101",
    "Написать demo@example.org",
    "Перейти на https://example.org",
    "Воспользоваться tel:+79990000000",
    "Please use mailto:demo.example",
    "Please use wa.me/79990000000",
    "Please use t.me/demo1234",
    "Проверить синтетический запрос\nCall soon",
    "Проверить запрос\rLater",
])
def test_contact_or_control_characters_rejected(text):
    assert synthetic_action_is_safe(text) is False


@pytest.mark.parametrize("text", [
    "Проверить синтетический запрос 1С",
    "Проверить тестовую запись DEMO-ITREQ-001",
    "Присвоить балл 86 и проверить отчёт",
    "Verify test signal on 2026-11-01",
])
def test_safe_demo_actions_still_accepted(text):
    assert synthetic_action_is_safe(text) is True


@pytest.mark.parametrize("value", ["", None, 123, [], "Too", " " * 15])
def test_type_and_length_fail_closed(value):
    assert synthetic_action_is_safe(value) is False
