"""Real native-title automation only mutates future three-signal names."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from leadpilot.serviceit_kanban_live_title_auto import (
    KanbanTitleBlocked, title_decision, title_rest, process_one,
)

NOW = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("signal,source,title,label", [
    ("tender", "b2b-center", "Закупка 1С", "📑 ТЕНДЕР"),
    ("vacancy", "trudvsem", "Нужен программист 1С", "👥 ВАКАНСИЯ"),
    ("direct", "direct", "Нужна настройка Битрикс24", "🎯 ПРЯМАЯ УСЛУГА"),
    ("", "", "Неопределённая потребность", "⚪ УТОЧНИТЬ ТИП"),
])
def test_native_title_icons_and_original_text_preserved(signal,source,title,label):
    card = {"title": title,
            "UF_CRM_13_SVCIT_SIGNAL_KIND": signal,
            "UF_CRM_13_SVCIT_SIGNAL_SOURCE": source}
    out, state = title_decision(card, now=NOW)
    assert state == "TITLE_UPDATE_REQUIRED"
    assert out == f"{label} · {title}"


def test_already_correct_title_skipped():
    card = {"title": "📑 ТЕНДЕР · Закупка 1С",
            "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender",
            "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "b2b-center"}
    assert title_decision(card, now=NOW) == (
        None, "ALREADY_CORRECT_NATIVE_TITLE")


def test_conflicting_existing_badge_is_not_overwritten():
    card = {"title": "👥 ВАКАНСИЯ · Закупка 1С",
            "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender"}
    assert title_decision(card, now=NOW)[1] == "EXISTING_DIFFERENT_BADGE_HOLD"


def test_long_title_not_truncated():
    with pytest.raises(KanbanTitleBlocked):
        title_decision({"title": "а"*245,
                        "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender"}, now=NOW)


@pytest.mark.parametrize("id", [3, 7, 0])
def test_never_update_history(id):
    client = Mock()
    with pytest.raises(KanbanTitleBlocked):
        title_rest(client, "https://example.invalid", id,
                   {"title": "📑 ТЕНДЕР · Закупка 1С"})
    client.post.assert_not_called()


def test_no_other_fields_written():
    c = Mock()
    c.post.return_value.status_code = 200
    c.post.return_value.content = b'{"result":{"item":{"id":10}}}'
    c.post.return_value.json.return_value = {"result": {"item": {"id": 10}}}
    title_rest(c, "https://example.invalid", 10,
               {"title": "📑 ТЕНДЕР · Закупка 1С"})
    assert c.post.call_args.kwargs["json"]["fields"] == {
        "title": "📑 ТЕНДЕР · Закупка 1С"}


def test_pre_cutover_card_not_touched(monkeypatch):
    from leadpilot import serviceit_kanban_live_title_auto as m
    monkeypatch.setattr(m, "read_item", lambda *_: {
        "id": 55, "categoryId": 21, "entityTypeId": 1062,
        "title": "Закупка",
        "createdTime": "2026-11-01T00:00:00+00:00",
        "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender",
    })
    monkeypatch.setattr(m, "after_activation",
                        lambda *_: datetime(2026, 11, 5,
                                             tzinfo=timezone.utc))
    fence = Mock()
    with pytest.raises(KanbanTitleBlocked):
        process_one(None, {}, "", 55, fence, now=NOW)
    fence.reserve.assert_not_called()


def test_unambiguous_badge_is_not_formal_sales_approval():
    card = {"title": "Требуется системный администратор",
            "UF_CRM_13_SVCIT_SIGNAL_KIND": "vacancy",
            "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "trudvsem"}
    value, decision = title_decision(card, now=NOW)
    assert "ВАКАНСИЯ" in value
    assert "Lead Score" not in value
    assert decision == "TITLE_UPDATE_REQUIRED"
