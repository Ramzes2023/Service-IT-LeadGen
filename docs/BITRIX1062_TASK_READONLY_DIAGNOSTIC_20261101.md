# ServiceIT — диагностика прав REST для связанной задачи (01.11.2026)

## Факты последнего запуска

Владелец выполнил `scripts/RUN_SERVICEIT_TASK_METADATA_READONLY.ps1` на Windows; исходный скрипт ответил `SERVICEIT_TASK_METADATA=BLOCKED_READONLY` и не сообщил, какой метод не сработал. Это **не означает**, что DEMO-карточка №3 отсутствует. Весь оператор был `READ_ONLY`, без задач и CRM-записей.

После запуска в PowerShell ошибочно вставили предыдущий **вывод консоли** (включая приглашение `PS C:\...`, строки `remote:`, `...=PASS`); последующие `ParserError` и `CommandNotFoundException` — следствие этого, а не ошибки GitHub/Bitrix24. Вставляйте только текст внутри блока PowerShell, без `PS C:\...>`, `>>` и без логов.

## Новая исправленная диагностика

`leadpilot/bitrix_1062_task_diagnostics.py` выполняет только:

- `crm.type.get` для `entityTypeId=1062`;
- `crm.enum.ownertype` для проверки короткого кода типа;
- `tasks.task.getFields` для технической схемы задач;
- `crm.item.get` **только** DEMO-карточки `id=3, entityTypeId=1062`.

Каждый метод получает отдельную обезличенную диагностику (`OK`, `HTTP_ERROR`, `API_ERROR`, `RESULT_UNEXPECTED_TYPE` и т. д.). HTTP-коды и коды ошибок проходят узкий allowlist; **полное тело ответа, вебхук, описание ошибки, данные клиентов и сотрудников никогда не возвращаются**.

Если все четыре метода доступны, скрипт повторяет те же четыре read-only проверки уже существующим строгим модулем и выводит только булевы признаки: `owner_symbol_verified`, `task_link_enabled`, `automation_confirmed_off`, `task_fields_ready`, `exact_demo_card_readback`. Ни задача, ни карточка не создаются или меняются.

### Возможная причина — права вебхука

Официальная документация `tasks.task.getFields` требует scope `task`, а методы CRM — scope `crm`. У входящего вебхука доступ определяется **и scopes в настройках, и правами сотрудника-владельца**. Если `tasks.task.getFields` ответит 403/400, администратор должен проверить права вебхука. Не увеличивайте их автоматически и не вставляйте вебхук в чат/GitHub.

Источники:
- https://apidocs.bitrix24.ru/api-reference/tasks/tasks-task-get-fields.html
- https://apidocs.bitrix24.ru/local-integrations/local-webhooks.html
- https://apidocs.bitrix24.ru/tutorials/tasks/how-to-connect-task-to-spa.html

## Запуск Windows

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/RUN_SERVICEIT_TASK_DIAG_READONLY.ps1
```

Используется **текущая** версия проекта и существующая `.venv`, вебхук вводится скрыто. Рабочую карточку №3 трогать не требуется.

**Никакая задача Михаилу пока не поставлена.** Фактический `RESPONSIBLE_ID` и его права ещё не подтверждены. Только после результата диагностики и разрешения корпоративного администратора допустим отдельный контролируемый тест одной связанной задачи.
