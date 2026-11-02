# B2B-Center №4661617 / Bitrix24 №7 — безопасный однократный backfill

## Установлено фактами 02.11.2026

- GitHub Issue #66: пользовательский исходный комментарий `6579109393` содержит точный JSON `SERVICEIT_B2B_NOTICE_V1` закупки 4661617: точная тема «Право заключения договора оказания услуг по сопровождению учетных систем 1С:Бухгалтерия предприятия и 1С:ЗУП», организатор, URL; `buyer_inn=null`, `original_publication_verified=false`, `qualified_lead=false`. Это письмо поставщика, не доказанная актуальная потребность.
- GitHub Actions run `41458344493` создал элемент `item_id=7` из этого уведомления (`ONE_TENDER_HOLD_VERIFIED`) **до PR #71**, который добавил девять предварительных полей к новым карточкам.
- Владелец выполнил live read-only PR #77 `-InspectLive -ItemId 7`: `source_kind=b2b_center_hold`, `decision=HOLD_MISSING_PRELIMINARY_AI`, `crm_writes=0`, `gigachat_calls=0`. Таким образом backfill нужен именно существующей №7, а не демонстрационной №3.

## Новый ограниченный оператор

`leadpilot/serviceit_b2b_card7_preliminary_backfill.py` и `scripts/RUN_SERVICEIT_B2B_CARD7_PRELIMINARY_BACKFILL.ps1`.

По умолчанию никаких изменений. `-InspectLive` (только GET) проверяет:
- точное `entityTypeId=1062`, `categoryId=21`, `id=7`;
- все исторические исходные поля, включая title, stage, URL, organizer, idempotency/source IDs, совпадают с зафиксированным в GitHub Issue #66 исходником;
- девять AI-полей реально пусты; Score, версия и badge реально пусты;
- девять полей в `crm.item.fields useOriginalUfNames=Y` существуют, writable, single, nonimmutable;
- отдельный локальный `backfill:1062:21:7:preliminary-ai-issue66:v1` ещё не зарезервирован. Read-only команда НЕ создаёт журнал и ничего не изменяет.

В будущей строго отдельно разрешённой однократной операции `-InspectLive -ApplyOnce -ApproveExactItem7` скрипт:
1. Повторно читает item и проверяет, что карточку не изменил другой сотрудник.
2. Создаёт **отдельный постоянный** журнал `%LOCALAPPDATA%\ServiceIT\Existing-B2B-Hold-AI\backfill-v1.sqlite3` и резервирует `UNCERTAIN` ДО потенциального POST. Без обхода через смену StateDir/удаление файла. При существующем `UNCERTAIN` или `VERIFIED` новый POST запрещён.
3. Делает **ровно один** `crm.item.update`, `useOriginalUfNames=Y`, только девять уже существующих AI-полей. Ни Score, ни badge, ни title, ни контакты, ни стадия, ни задачи НЕ отправляются в fields.
4. Выполняет `crm.item.get`; строго проверяет девять значений и все защищённые source/hold поля. Только после этого устанавливает новую запись журнала `VERIFIED`.
5. При таймауте, неожиданном ответе или несовпадении readback `UNCERTAIN` остаётся без повторов; проблему затем изучать только read-only. Не трогать два исторических badge-леджера `UNCERTAIN` DEMO №3.

## Что будет в девяти полях

Честный **предварительный HOLD**, а НЕ полноценный GigaChat-анализ как в DEMO №3:
- Статус `NOT_RUN_SOURCE_UNVERIFIED` — модель не запускалась.
- Краткий вывод, точная тема письма поставщика (помечена как тема уведомления, а не проверенная цитата первоисточника).
- Возможная потребность — гипотеза, не факт; ограничения; рекомендация; менеджерский чек-лист; полный отчёт.
- **Без** фиктивных чисел Score/цвета/ИНН, телефонных контактов или подтверждения закупки.

Реальная модель GigaChat и детерминированный Score возможны только после проверки первоисточника, заказчика, актуальности, ФНС/ИНН, клиентов, CRM-дублей, стоп/юридических условий, политик приватности и доказательной базы. Для остальных источников нужен общий production worker, который пока НЕ развёрнут.

## Операторская приёмка

После CI и pinned commit запускать сперва исключительно:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -PythonPath $Python -InspectLive
```

Ожидаемый `B2B_CARD7_PRELIMINARY_AI_READONLY_READY`; `crm_writes=0`; `state=NOT_RESERVED`. Не выполнять `-ApplyOnce` до проверки output, полного тестирования и отдельного решения владельца о записи.

Ни PR, ни CI не меняют корпоративный Битрикс24. Успешный CI не подтверждает live-backfill. Внешняя документация: https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-update.html.
