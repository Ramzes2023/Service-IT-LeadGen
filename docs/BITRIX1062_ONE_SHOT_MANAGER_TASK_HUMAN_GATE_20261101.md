# Service IT — одна учебная задача менеджера для карточки Bitrix24 #3

Состояние: **в GitHub подготовлен защищённый одноразовый оператор; живое создание ещё не выполнялось**. В портале уже подтверждено `METADATA_READY_TASK_NOT_CREATED`: process 1062, category 21, CRM item #3, вся схема полей и права `crm`+`task` доступны. Это НЕ означает, что задача существует.

## Действие

`scripts/RUN_SERVICEIT_ONE_SHOT_LINKED_DEMO_TASK.ps1`:

1. Просит указать числовой **ID сотрудника**, проверенный пользователем по корпоративному профилю `/company/personal/user/<ID>/`. **ID не выдумывается**. Владелец webhook не считается автоматически Михаилом.
2. Требует независимого подтверждения `VERIFIED` и точной фразы `AUTHORIZE_ONE_SERVICEIT_DEMO_TASK_1062_3`. **Создание может уведомить сотрудника внутри Bitrix24.**
3. Запрашивает вебхук скрыто в PowerShell. Код и лог **не печатают секрет**.
4. Вновь проверяет `crm.type.getByEntityTypeId`, `crm.enum.ownertype`, `tasks.task.getFields`, **только** `crm.item.get` для 1062/#3, затем ограниченный `tasks.task.list` по `UF_CRM_TASK=T426_3` и по уникальному заголовку DEMO.
5. **До POST** создаёт локальный журнал `%LOCALAPPDATA%\ServiceIT\One-Demo-Manager-Task\one-demo-task.sqlite3`, вставляет уникальный постоянный ключ в статусе `UNCERTAIN_POST_RESERVED` и коммитит транзакцию. Уже существующая строка запрещает новый POST.
6. Только при успешных проверках выполняет **один** `tasks.task.add` с `RESPONSIBLE_ID=<проверенный сотрудник>`, `UF_CRM_TASK=["T426_3"]`, учебным описанием **«НЕ ЗВОНИТЬ / НЕ ПИСАТЬ / НЕ ОТПРАВЛЯТЬ КП»**. Никаких писем, звонков, клиентов, CRM-карточек или real procurement sources не создаёт.
7. Проверяет `tasks.task.get` с явным `select=["ID","TITLE","RESPONSIBLE_ID","UF_CRM_TASK"]`, подтверждает ID, название, получателя, привязку, затем ставит `VERIFIED`. Неполный ответ/тайм-аут оставляет журнал в `UNCERTAIN_POST_RESERVED` или `POST_ACK_NEEDS_GET`. **Никаких слепых повторов POST**.

## Ограничения

- Это **внутренняя учебная задача**, не задача на реальный контакт.
- Проверка `tasks.task.list` ограничена правами webhook. `NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF` **не гарантирует** отсутствия скрытых задач других сотрудников. Перед тестом требуется осознанное подтверждение владельца.
- Технический `RESPONSIBLE_ID` вводится по реальному профилю; API не доказывает, что это Михаил. Не называйте задачу назначенной Михаилу, пока он не подтвердит свой профиль и доступ.
- После `BLOCKED_OR_UNCERTAIN` **не запускать заново без проверки журнала и уже созданных задач**. Даже если задача создана, GET readback может быть временно недоступен; повторный POST создаст дубль.
- Локальный durable ledger не является глобальной блокировкой разных серверов/операторов. Это demo-gate, не production worker.
- Появление задачи может инициировать внутреннее уведомление исполнителю; поэтому запуск должен выполняться оператором с корпоративным разрешением.

## Документация Bitrix24

- [tasks.task.add](https://apidocs.bitrix24.ru/api-reference/tasks/tasks-task-add.html)
- [tasks.task.get](https://apidocs.bitrix24.ru/api-reference/tasks/tasks-task-get.html)
- [Как привязать задачу к смарт-процессу](https://apidocs.bitrix24.ru/tutorials/tasks/how-to-connect-task-to-spa.html)
- [tasks.task.list](https://apidocs.bitrix24.ru/api-reference/tasks/tasks-task-list.html)

**Гейт:** требуется зелёный CI и независимое тестирование на Windows перед тем, как вручную создавать реальную учебную задачу. Никакой production activation, source ingestion или реальных лидов этот PR не включает.
