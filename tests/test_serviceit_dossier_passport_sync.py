from leadpilot.serviceit_b2b_dossier_v11 import REPORT, sync_confirmed_passport

BASE = """Тендер
01  ПАСПОРТ ЗАКУПКИ
Заказчик: не установлен
ИНН: нет подтверждённой связки покупатель—ИНН
Срок подачи: [АРХИВ V10, сверить актуальность] 16.11.2026 16:00
Бюджет: в прочитанных страницах не найден

02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ
География: не установлена
03  ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ
04  КОНТАКТЫ И ЮРИДИЧЕСКАЯ ПРОВЕРКА
Публичный заказчик: не установлен
ИНН: нет подтверждённой связки покупатель—ИНН
05  НАПРАВЛЕНИЕ И ЗАДАЧИ ДЛЯ МЕНЕДЖЕРА
06  ЧТО ПОКА НЕ ДОКАЗАНО
• Сведения о заказчике
• проверенный ИНН / ФНС-МСП
• Срок подачи
• Цена / бюджет
07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ
Проверенный источник и исходный раздел."""


def test_confirmed_data_updates_passport_and_gap_list_idempotently():
    values = {
        "BUYER": "АО «Заказчик»",
        "INN": "7701234567 (публичный источник, проверка ФНС отдельно)",
        "DEADLINE": "16.11.2026 16:00 МСК",
        "LOCATION": "Москва, место поставки",
    }
    fields = {REPORT: BASE, "manual": "оставить"}
    updated = sync_confirmed_passport(fields, values)
    report = updated[REPORT]
    assert "Заказчик: АО «Заказчик»" in report
    assert report.count("География: Москва, место поставки") == 2
    assert "• Срок подачи" not in report and "• Сведения о заказчике" not in report
    assert "• Цена / бюджет" in report
    assert "проверка ФНС отдельно" in report
    assert report.endswith("Проверенный источник и исходный раздел.")
    assert updated["manual"] == fields["manual"]
    assert sync_confirmed_passport(updated, values) == updated


def test_preserve_existing_confirmed_values():
    existing = {REPORT: BASE.replace("Заказчик: не установлен", "Заказчик: другой")}
    assert sync_confirmed_passport(existing, {"BUYER": "АО «Заказчик»"}) == existing
