# Bitrix24: точное несоответствие DEMO-карточки №3 — диагностика от 01.11.2026

## Факты живого запуска

Пользователь запустил `RUN_SERVICEIT_ONE_DEMO_TASK_DEDUPE_READONLY.ps1` из PR #49 (`0df5ee9...`); результат:

```text
status=BLOCKED_READ_ONLY
reason_code=EXACT_DEMO_GATE_EXACT_CARD_FAILED
crm.type.getByEntityTypeId=OK
crm.enum.ownertype=OK
tasks.task.getFields=OK
crm.item.get=OK
owner_symbol_verified=true
task_link_enabled=true
automation_confirmed_off=true
task_fields_ready=true
exact_demo_card_readback=false
task_created=false
crm_writes=NO
```

Таким образом, права, связь со смарт-процессом 1062, поддержка задач и доступ к карточке №3 исправны. **По крайней мере одно поле карточки не совпадает с константами тестового эталона**; без более подробного чтения неизвестно, была ли это ручная правка, формат REST или устаревший эталон. Не создавать новую карточку и не перезаписывать существующую. Этот запуск завершился до `tasks.task.list`, `tasks.task.add` и локального одноразового SQLite-резервирования.

## Новая безопасная проверка: 14 флагов

В `leadpilot/bitrix_1062_task_metadata.py` исходное строгое сравнение учебной карточки выделено в `_demo_item_checks`, полностью сохраняя **тот же критерий одобрения**. Каждое условие возвращает **только true/false**:

- `item_id_matches`
- `entity_type_matches`
- `category_matches`
- `stage_matches`
- `title_matches`
- `all_16_original_uf_names_present`
- `idempotency_key_matches`
- `source_is_demo_fixture`
- `signal_id_matches`
- `demo_inn_matches`
- `demo_company_name_matches`
- `demo_source_url_matches`
- `no_real_contacts`
- `no_real_company_link`

Все 14 условий нужны для `exact_demo_card_readback=true`; не расслаблять их до подтверждения фактических требований CRM.

Модуль `leadpilot/bitrix_1062_task_diagnostics.py` покажет `exact_demo_card_checks` только если последнее условие общего gate нарушено. Команда `RUN_SERVICEIT_ONE_DEMO_TASK_DEDUPE_READONLY.ps1` теперь прикладывает этот список через повторный read-only анализ, без передачи ФИО, INN, реальных значений CRM, URL сигналов, webhook или полного ответа REST. Проверка **ничего не меняет** на портале.

## Что дальше

После успешного GitHub CI запустить эту же read-only PowerShell-команду на новом закреплённом коммите main. В результате появится один или несколько `...=false`. По ним оценить, изменена ли стадия/карточка, пропало ли одно из 16 исходных полей или эталон не соответствует возвращаемому Bitrix формату.

**Если совпадение нарушено, не делать `tasks.task.add` и не создавать карточку повторно**; возможные реальные связи с контактами должны приводить только к блокировке. Восстановление допустимо только после независимой проверки того, какие данные уже находятся в существующей учебной карточке, а не через автоматическое переписывание корпоративной CRM.
