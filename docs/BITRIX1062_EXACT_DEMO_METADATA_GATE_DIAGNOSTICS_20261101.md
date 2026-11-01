# ServiceIT — устранение неопределённого результата EXACT_DEMO_METADATA_NOT_READY

Дата: 01.11.2026. Источник: пользовательский журнал защищённого read-only оператора PR #48 на корпоративном портале Bitrix24.

## Подтверждённые наблюдения

Проверка `RUN_SERVICEIT_ONE_DEMO_TASK_DEDUPE_READONLY.ps1` остановилась:

```text
status=BLOCKED_READ_ONLY
reason_code=EXACT_DEMO_METADATA_NOT_READY
task_created=false
crm_writes=NO
```

Обнаружение дублей не стартовало: `discover_demo_manager_task` завершился после `audit_synthetic_task_link`, перед первым `tasks.task.list`. Никаких `tasks.task.add` / CRM-записей не выполнялось.

**Ранее на том же корпоративном портале** другая проверка успешно показала `METADATA_READY_TASK_NOT_CREATED`, четыре REST метода OK и пять булевых критериев истинны. Эти наблюдения не позволяют точно утверждать, почему при более позднем чтении они перестали выполняться. Не нужно без доказательств заново менять webhook права, настройки смарт-процесса, карточку или создавать новые DEMO записи.

## Изменение

В `leadpilot/bitrix_1062_task_dedupe_readonly.py` теперь:
- строго разделяются сбои REST-чтения и ошибки детерминированных gates;
- если не выполнен **ровно один** критерий, выводится соответствующий статический код:
  `EXACT_DEMO_GATE_OWNER_SYMBOL_FAILED`, `EXACT_DEMO_GATE_TASK_LINK_FAILED`, `EXACT_DEMO_GATE_AUTOMATION_OFF_FAILED`, `EXACT_DEMO_GATE_TASK_FIELDS_FAILED`, `EXACT_DEMO_GATE_EXACT_CARD_FAILED`;
- множественные или неизвестные отказы: `EXACT_DEMO_MULTIPLE_OR_UNKNOWN_GATES_FAILED`;
- исключения read-only метаданных выводятся только через allowlist: `EXACT_DEMO_METADATA_READ_...`, `...SCHEMA_UNEXPECTED` либо `...READ_UNCLASSIFIED`.
- При любом `EXACT_DEMO_...` **в том же запуске** вызывается ранее принятый безопасный `diagnose_task_metadata` с раздельными REST status `crm.type.getByEntityTypeId`, `crm.enum.ownertype`, `tasks.task.getFields`, `crm.item.get` и, если доступны, пятью булевыми gates.
- Вывод содержит лишь фиксированные статусы и булевы признаки — **никаких содержимых задач, исходных данных CRM, webhook, ФИО сотрудника, внешних контактов или ответов REST с ошибкой**.
- Не отключается поиск дублей и не создаются реальные задачи; внешние источники, worker, рассылка остаются отключёнными.

Модули `bitrix_1062_one_shot_task.py` и `bitrix_1062_auto_owner_demo_task.py` также сохраняют эти новые статические причины без превращения их в общую ошибку, но **live POST до прохождения preflight всё равно невозможен**.

## Следующая проверка

После зелёного CI запустить только `RUN_SERVICEIT_ONE_DEMO_TASK_DEDUPE_READONLY.ps1` на закреплённом коммите main. Результат должен содержать `reason_code`, при проблеме metadata — `metadata_diagnostics`.

При `exact_demo_card_readback=false` анализировать только конкретные критерии учебной карточки #3 и изменения с момента прежнего успешного readback. Не переписывать существующую карточку вслепую. При сбоях REST — изучать конкретный метод/статус, без секретных URL.

**Успех `NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF` не означает разрешения на реальное создание задачи или наличие глобальной дедупликации.** Задача владельцу webhook может вызвать внутреннее уведомление. Команды на создание без явной необходимости не повторять.
