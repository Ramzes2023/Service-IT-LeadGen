# Исправление Bitrix24 задачи менеджера после диагностики 01.11.2026

## Фактический результат работы пользователя

```text
crm.type.get:        HTTP 400 OTHER_OR_NOT_PROVIDED
crm.enum.ownertype:  OK
tasks.task.getFields: HTTP 401 INSUFFICIENT_SCOPE
crm.item.get:        OK
methods_ok: 2
task_created: false
crm_writes: NO
```

Изолированная реальная тестовая карточка #3 успешно читается. **Ни задача менеджеру, ни изменения CRM не выполнялись.**

## Причина №1 — неверный REST-метод в коде (теперь исправлено)

Официальная документация разграничивает:
- `crm.type.get` — принимает обязательное **`id`**, внутренний идентификатор типа, **не** `entityTypeId`.
- `crm.type.getByEntityTypeId` — принимает `entityTypeId=1062` и возвращает данные смарт-процесса для нашей задачи.

До исправления приложение ошибочно вызывало `crm.type.get` с `{"entityTypeId":1062}`. Это достаточно объясняет HTTP 400; ранее гипотеза о правах CRM была **не подтверждена**, и её не следует повторять как факт.

В модулях `bitrix_1062_task_metadata.py` и `bitrix_1062_task_diagnostics.py` заменены **название метода** и тестовые REST-моки. Запрос теперь:

```json
{"method":"crm.type.getByEntityTypeId","params":{"entityTypeId":1062}}
```

Обновлена и строка разрешённых методов Windows PowerShell. Дополнительно диагностика сможет показать 3 булевых признака смарт-процесса из этого метода даже без права на задачи:

- `correct_entity_type`
- `task_link_enabled` — подтверждены `isUseInUserfieldEnabled` и `linkedUserFields["TASKS_TASK|UF_CRM_TASK"]`
- `automation_confirmed_off`

**Никакие настройки портала программно не меняются.**

Документация: https://apidocs.bitrix24.ru/api-reference/crm/universal/user-defined-object-types/crm-type-get.html и https://apidocs.bitrix24.ru/api-reference/crm/universal/user-defined-object-types/crm-type-get-by-entity-type-id.html.

## Причина №2 — отсутствие REST-права `task` у вебхука

`tasks.task.getFields` вернул **HTTP 401 `INSUFFICIENT_SCOPE`** — однозначный признак отсутствия нужного scope. Метод требует право `task`, тогда как ранее использованный webhook имел рабочий доступ к CRM.

Только корпоративный администратор/владелец интеграции должен:
1. Открыть **Разработчикам → Вебхуки/Интеграции**, найти конкретный входящий вебхук, открыть **Редактировать**.
2. Проверить **Права доступа**: нужны минимально `CRM` + `Задачи (task)` для узкого тестового сценария.
3. Подтвердить корпоративное разрешение на работу с задачами. Не расширять права поддержки, не давать `user` или административный scope без надобности и не публиковать секрет вебхука.
4. Повторить **только read-only** диагностику после сохранения прав или с отдельным специально разрешённым вебхуком.

Официальная документация `tasks.task.getFields` с scope task: https://apidocs.bitrix24.ru/api-reference/tasks/tasks-task-get-fields.html .

## Запуск без записи в CRM

После приёмки исправления в main Windows runner:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/RUN_SERVICEIT_TASK_DIAG_READONLY.ps1
```

Вывод без корпоративных данных и секретов. Реальная задача **не создаётся**, и даже `METADATA_READY_TASK_NOT_CREATED` не означает подтверждения личности Михаила или разрешения на живую постановку задания.

**Следующие отдельные критерии готовности**: метод `tasks.task.getFields` доступен, задача может быть прикреплена к процессу по `UF_CRM_TASK`, проверены технический ID исполнителя и права Михаила, исключены дубли/неопределённые POST, получено корпоративное разрешение на одну учебную задачу. Реальные контакты и источники остаются отключены.
