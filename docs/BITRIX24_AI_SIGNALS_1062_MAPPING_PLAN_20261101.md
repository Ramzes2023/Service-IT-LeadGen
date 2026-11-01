# Сервис ИТ — картирование смарт-процесса AI-сигналов №1062

Дата анализа: 01.11.2026. Основание: пользовательский sanitized JSON `ServiceIT_CRM_1062_Report.json`, полученный из **реального read-only аудита** портала `serviseit.bitrix24.ru`, плюс исходный dry-run контракт `leadpilot/bitrix_handoff_dryrun.py`.

## Фактически подтверждено

- CRM: существующие **8** коммерческих воронок сделок, их **не изменять**.
- Старый смарт-процесс «Лицензии»: `entityTypeId=1056`, **не изменять**.
- Наш тестовый смарт-процесс: **«Сервис ИТ — AI-сигналы (тест)»** — `entityTypeId=1062`, внутренний `crm.type.id=13`.
- Воронка внутри нового процесса: **`categoryId=21`**, имя `Общая воронка`.
- Поля процесса: **22 стандартных доступных кода**, **0 пользовательских кодов `UF_CRM...`** в ответе `crm.item.fields`.
- В live аудите `crm.type.list`, `crm.category.list.1062`, `crm.item.fields.smart_process.1062` и `crm.status.list.DYNAMIC_1062_STAGE_21` вернули **OK**.
- Скрипт только читал метаданные. **Элементы/карточки не создавались; CRM_WRITE=NO**.

## Фактические стадии категории 21

| STATUS_ID | Текущее название | Целевое значение (проект; НЕ применено) |
|---|---|---|
| `DT1062_21:NEW` | Начало | Новый сигнал |
| `DT1062_21:PREPARATION` | Подготовка | Проверка компании/ИНН |
| *новый status_id, пока отсутствует* | — | Проверка потребности/источника |
| `DT1062_21:CLIENT` | Согласование | Проверка менеджером |
| `DT1062_21:SUCCESS` | Успех | Одобрено к передаче |
| `DT1062_21:FAIL` | Провал | Отклонено / стоп / дубль |

**Предложение:** оставить текущую категорию 21, переименовать пять стадий и добавить **ровно одну** промежуточную. Причину отказа фиксировать отдельным полем, а не создавать много финальных стадий. `SUCCESS` означает **одобрение сигнала менеджером к дальнейшей работе**, а не завершённую коммерческую сделку. До записи проверить числовые `ID` строк стадий через `crm.status.list`; `STATUS_ID` из audit JSON **не равен** числовому `id` для `crm.status.update`. Для новой промежуточной стадии применять только согласованный `crm.status.add`, строго `ENTITY_ID=DYNAMIC_1062_STAGE_21`.

## Предлагаемые дополнительные поля (НЕ созданы)

Внимание: для создания полей используется **`userfieldconfig.add`** (`moduleId=crm`, `field.entityId=CRM_13`), где **13 — внутренний id типа**, а **не** `entityTypeId=1062`. Формат `fieldName=UF_CRM_13_...` и длина не более 50 символов. Существующее поле `title` использовать для короткого заголовка сигнала, системные `stageId`, `categoryId`, `assignedById` использовать по их назначению. Не дублировать стандартные поля без необходимости.

| Предлагаемый код | Подпись | Тип | Поле offline-контракта | Правило |
|---|---|---|---|---|
| `UF_CRM_13_SVCIT_COMPANY_INN` | ИНН организации | `string` | `company_inn` | Контрольная сумма в Python, 10/12 цифр; не создавать карточку компании автоматически |
| `UF_CRM_13_SVCIT_COMPANY_NAME` | Наименование организации | `string` | `company_name` | Первично по проверенной записи, без контактов |
| `UF_CRM_13_SVCIT_REGION` | Регион | `string` | `region` | Пилот: Москва/Московская область |
| `UF_CRM_13_SVCIT_SIGNAL_ID` | ID исходного сигнала | `string` | `signal_id` | Неизменяемый ID конкретного события |
| `UF_CRM_13_SVCIT_SIGNAL_DATE` | Дата сигнала | `date` | `signal_date` | Дата исходного объявления/закупки |
| `UF_CRM_13_SVCIT_SIGNAL_SOURCE` | Источник | `string` | `signal_source` | Нормализованное название первоисточника |
| `UF_CRM_13_SVCIT_SIGNAL_URL` | Ссылка на подтверждение | `string` | `signal_url` | Публичный первичный URL; не URL клиента |
| `UF_CRM_13_SVCIT_SUGGESTED_SERVICE` | Предполагаемая услуга | `string` | `suggested_service` | 1С, IT-аутсорсинг, ККТ и др.; это гипотеза |
| `UF_CRM_13_SVCIT_LEAD_SCORE` | Оценка сигнала | `integer` | `lead_score` | 0–100; вычисление/валидация на стороне Python |
| `UF_CRM_13_SVCIT_SCORE_VERSION` | Версия скоринга | `string` | `score_version` | Модель/версия формулы оценки |
| `UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION` | Следующее действие менеджера | `string` | `manager_next_action` | Без автоматических звонков/рассылок |
| `UF_CRM_13_SVCIT_EVIDENCE_STATUS` | Статус проверки доказательства | `string` | `evidence_status` | Непроверено/подтверждено/ошибка |
| `UF_CRM_13_SVCIT_REVIEW_DECISION` | Решение менеджера | `string` | `review_decision` | Ожидает/одобрено/отклонено; не путать с готовой продажей |
| `UF_CRM_13_SVCIT_STOP_REASON` | Причина блокировки | `string` | `stop_reason` | Стоп-лист/дубль/действующий клиент/нет подтверждения |
| `UF_CRM_13_SVCIT_IDEMPOTENCY_KEY` | Ключ идемпотентности | `string` | `idempotency_key` | Хэш ИНН+ID сигнала; уникальность обеспечивать также в локальной БД |
| `UF_CRM_13_SVCIT_SIGNAL_KIND` | Тип сигнала | `string` | `signal_kind` | Вакансия/закупка/расширение/другое |

Все 16 пользовательских полей — **предложение для конфигурации**, а не реальная схема, не обещание успешного API создания. Перед изменением удостовериться в доступных типах через `userfieldconfig.getTypes`, провести `userfieldconfig.list` для `entityId=CRM_13`, сопоставить реальные названия и избежать повторного создания. Некоторые поля при необходимости можно объединить или оставить в локальной базе, прежде чем устанавливать финальный объём.

## Контрольный порядок до live записи

1. Повторный read-only снимок `crm.type.get`, `crm.category.list`, `crm.item.fields`, `crm.status.list`, `userfieldconfig.list` для **конкретных** ID.
2. Зафиксировать изменение только для `CRM_13` / `entityTypeId=1062` / `categoryId=21`, проверить отключённую автоматизацию и права. **Ни один существующий процесс/сделку не трогать.**
3. После отдельного согласования конфигурационных изменений с администратором: создавать поля и изменять стадии идемпотентно — `already exists` = skip; фиксировать результаты; при неоднозначном таймауте перед повтором проверять read-back. Никаких автоматических массовых операций.
4. Read-back каждого поля: `fieldName`, `entityId`, `userTypeId`, отсутствие конфликтующих кодов/типов. Read-back стадий: реальные `STATUS_ID`, `ID`, `SEMANTICS`, сортировка.
5. Только после **отдельного разрешения на одну синтетическую запись**: `crm.item.add` с `entityTypeId=1062` и `categoryId=21`, именем вида `DEMO ServiceIT AI — no customer`, синтетическими данными, запретом контактов/звонков/роботов; затем `crm.item.get` для read-back и задокументированный способ удаления тестового элемента. Не использовать реальные телефоны/email/ФИО.
6. Для реального handoff нужен независимый внешний источник лидов, проверка ИНН/активного клиента/стоп-листа, ручное одобрение, стабильная идемпотентность `ИНН + signal_id` и аудит. Наличие нового смарт-процесса **не делает поисковые источники и лидогенерацию готовыми**.

## Подтверждение вендора и технические оговорки

- Документация: [crm.item.fields](https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-fields.html), [userfieldconfig.add](https://apidocs.bitrix24.ru/api-reference/crm/universal/userfieldconfig/userfieldconfig-add.html), [userfieldconfig.list](https://apidocs.bitrix24.ru/api-reference/crm/universal/userfieldconfig/userfieldconfig-list.html), [стадии смарт-процессов](https://apidocs.bitrix24.ru/tutorials/crm/how-to-add-crm-objects/how-to-add-category-to-spa.html).
- В старой копии PowerShell-аудитора остаётся отдельный дефект запроса `DEAL_STAGE_0` для первой коммерческой воронки; исправление `DEAL_STAGE` уже есть в code branch PR #37. На mapping **1062/21** этот дефект не влияет.
- В UI ранее отображались широкие разрешения роли `ServiceIT AI — тест` в блоке **Настройка CRM**, включая управление чужими процессами. После конфигурирования согласовать ограничение этих полномочий с администратором. Секрет вебхука никогда не помещать в GitHub, файлы отчётов или переписку.

**Статус:** `LIVE_METADATA=PASS`, `ENTITY_TYPE_ID=1062`, `INTERNAL_TYPE_ID=13`, `CATEGORY_ID=21`, `USER_FIELDS_EXISTING=0`, `STAGE_CONFIG=PROPOSED_ONLY`, `LIVE_HANDOFF=NO`, `PRODUCTION_READY=NO`.


## 01.11.2026 — готов guarded installer: первый запуск только preflight

Подготовлен отдельный автономный ZIP-оператор `SERVICEIT_AI_SIGNALS_CONFIG_1062_SAFE_PREFLIGHT_20261101.zip`. Его исходные файлы: `serviceit_config_1062.py`, `RUN_SERVICEIT_CONFIG_1062.ps1`, `tests/test_config.py`, `README.txt`. **Это отдельный пользовательский артефакт, пока не закоммичен в PR.**

- `17/17` локальных unit-тестов PASS, Python compile PASS (mock API, без корпоративного webhook).
- Режим без параметра `-Apply` делает **только read-only preflight**, сверяет `typeId=13`, `entityTypeId=1062`, `categoryId=21`, выключенную автоматизацию, `crm.status.list` и коды/типы планируемых 16 полей.
- **Важный дополнительный scope:** `userfieldconfig` вместе с `crm` для `userfieldconfig.getTypes`, `userfieldconfig.list`, `userfieldconfig.add`. Он не обязательно предоставлен существующему входящему вебхуку. Preflight обязан остановиться с безопасным кодом ошибки до записи; настройку scope выполняет администратор портала.
- Опциональный `-Apply` доступен только с отдельными подтверждениями `YES` и `CONFIGURE 1062` на локальном Windows компьютере; включает до 16 добавлений полей, до 5 переименований стадий и 1 новый этап. Все изменения только в `CRM_13` и `DYNAMIC_1062_STAGE_21`, проверяются read-back; конфликт имён/типов или чужая стадия = fail closed.
- Никакие реальные элементы CRM, компании, контакты, сделки, задачи, телефония, роботы, рассылки или источники не изменяются этим оператором.
- Артефакт имеет SHA-256 `64478871c9b8376371f78c72e042c60d10be7db291e4be83017782764494730c`. Токен вебхука не хранить в ZIP, логе и GitHub.

**Реальный preflight и apply ещё НЕ выполнены**. `LIVE_CONFIG_FIELDS=NOT_STARTED`; `LIVE_CONFIG_STAGES=NOT_STARTED`; `LIVE_HANDOFF=NO`. Сначала получить вывод read-only preflight, затем оценить необходимость изменения webhook scope и согласовать конфигурационную запись с администратором.


## 01.11.2026 — реальный preflight заблокирован scope

Оператор запустил `SERVICEIT_AI_SIGNALS_CONFIG_1062_SAFE_PREFLIGHT_20261101.zip` без `-Apply`; локальные `17/17 PASS`. Live preflight на `serviseit.bitrix24.ru` завершился безопасной остановкой `SERVICEIT_1062_CONFIG_STATUS=BLOCKED`, `SERVICEIT_1062_CONFIG_REASON=INSUFFICIENT_SCOPE`. Изменений CRM не было: `SERVICEIT_1062_CRM_ITEM_WRITES=0`, `EXISTING_FUNNELS=UNCHANGED_BY_TOOL`.

Локальный отчёт оператора: `%LOCALAPPDATA%/ServiceIT/AI-Config-1062/config-20261101T123333822Z.json`. Требуется добавить в **существующий входящий вебхук ID 27** scope `userfieldconfig` при сохранении `crm` и повторно запустить **только read-only preflight**. Не назначать глобальный admin и не пересоздавать процесс 1062. Любая конфигурационная запись отдельным `-Apply` только после успешного preflight и разрешения администратора. Документация: https://apidocs.bitrix24.ru/tutorials/field-types/how-to-add-user-field-to-spa.html .


## 01.11.2026 — live конфигурация и независимый post-apply read-back: VERIFIED

После явного подтверждения администратора пользователь запустил guarded operator на собственном Windows компьютере, используя только скрытый локальный ввод вебхука. **Фактически полученный вывод**:

```text
TARGET=serviseit.bitrix24.ru; ENTITY_TYPE_ID=1062; CATEGORY_ID=21; INTERNAL_TYPE_ID=13
SERVICEIT_CONFIG_1062_LOCAL_TESTS=PASS  # 17/17
SERVICEIT_1062_CONFIG_STATUS=APPLY_VERIFIED
SERVICEIT_1062_FIELDS_MISSING_BEFORE=16
SERVICEIT_1062_STAGES_RENAME_BEFORE=5
SERVICEIT_1062_NEW_STAGE_NEEDED=TRUE
SERVICEIT_1062_FIELDS_CREATED=16
SERVICEIT_1062_STAGES_CHANGED=6
SERVICEIT_1062_CRM_ITEM_WRITES=0; EXISTING_FUNNELS=UNCHANGED_BY_TOOL
```

Локальный закрытый отчёт применения: `%LOCALAPPDATA%\ServiceIT\AI-Config-1062\config-20261101T130414653Z.json`. После записи пользователь отдельно выполнил **новый read-only preflight**, который завершился:

```text
SERVICEIT_1062_CONFIG_STATUS=PREFLIGHT_READY
SERVICEIT_1062_FIELDS_MISSING_BEFORE=0
SERVICEIT_1062_STAGES_RENAME_BEFORE=0
SERVICEIT_1062_NEW_STAGE_NEEDED=FALSE
SERVICEIT_1062_CRM_ITEM_WRITES=0; EXISTING_FUNNELS=UNCHANGED_BY_TOOL
SERVICEIT_CRM_SCHEMA=VERIFIED
```

Локальный закрытый отчёт проверки: `%LOCALAPPDATA%\ServiceIT\AI-Config-1062\config-20261101T130807434Z.json`. Предыдущие разделы данного документа с пометками `PROPOSED_ONLY`, `NOT_STARTED` и `INSUFFICIENT_SCOPE` сохраняются как **исторический журнал**, но теперь **устарели как описание текущей схемы**.

**Подтверждено:** 16 запланированных полей существуют, 5 названий стадий настроены, одна дополнительная стадия присутствует, иных изменений воронки не требуется. По отчёту оператора клиентские записи не создавались, прочие воронки не изменялись.

**Не подтверждено и не разрешено текущим конфигурационным согласованием:** запись/удаление синтетической карточки через `crm.item.add/update/delete`, менеджерский E2E, ACL/RBAC на уровне реальных сотрудников, независимые проверки выключенных роботов, идемпотентность против реального Bitrix24, live handoff adapter, получение реальных лидов. Эти возможности не следует объявлять готовыми. Для тестовой записи требуется отдельное явное разрешение в изолированном процессе. PR остаётся Draft.

**Секреты:** ранее URL вебхука попал в пользовательский лог переписки; его необходимо перегенерировать/отозвать до дальнейших live REST-тестов. Ротация пока не подтверждена; не копировать URL в GitHub или отчёты.

**Текущий статус:** `SERVICEIT_CRM_SCHEMA=VERIFIED`; `LIVE_HANDOFF=NO`; `PRODUCTION_READY=NO`.
