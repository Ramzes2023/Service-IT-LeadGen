# All-lead Bitrix24 — предпросмотр схемы перед автоматическим CRM writer

## Основание

Пользователь подтвердил локальный `SERVICEIT_ALL_LEAD_BATCH_PREVIEW` (PR #68): три входные записи, две компании, один дубликат, два Score (90/85), корректные цветные метки, ноль CRM-записей. Все события **синтетические** и `HOLD_UNVERIFIED`.

В корпоративном смарт-процессе `entityTypeId=1062`, `CRM_13`, `categoryId=21` числовой Score 90 уже виден в Канбане. Дополнительное поле `UF_CRM_13_SVCIT_AI_SCORE_BADGE` присутствует, но после старых попыток записи и ручного сохранения `EMPTY`; два отдельных однократных журнала операции находятся в `UNCERTAIN`. Старые скрипты записи **никогда не повторять**, не удалять журналы, не обходить их новыми StateDir.

## Что проверяет новый оператор — строго read-only

- `leadpilot/serviceit_all_lead_crm_schema_preflight.py` — чтение только `crm.type.getByEntityTypeId`, `crm.item.fields` для `useOriginalUfNames=Y/N`, `userfieldconfig.list/get` для трёх полей.
- Exact entity type **1062 / internal 13**, отключены automation/bizproc, используются только соответствующие пользовательские поля.
- Для **всего smart process**, а не отдельного DEMO item #3: `UF_CRM_13_SVCIT_LEAD_SCORE`, `UF_CRM_13_SVCIT_SCORE_VERSION`, `UF_CRM_13_SVCIT_AI_SCORE_BADGE`.
- Проверяется наличие, однозначное оригинальное и camelCase имя *из live metadata*, `isReadOnly=false`, `isImmutable=false`, `isMultiple=false` у всех трёх, а также `userTypeId=string` у badge/version, длина и REGEXP в пользовательских настройках badge. Текст REGEXP и любые значения клиентов **не выводятся**. Неподходящие характеристики -> `SERVICEIT_ALL_LEAD_SCHEMA_BLOCKED`. Даже PASS не даёт разрешения писать CRM.
- Никогда не вызывает `crm.item.add/update/list/get`, не читает карточек клиентов или существующие журналы, не меняет общий вид Канбана, не запускает AI, роботов, телефонию, рассылку или сервер.
- Windows SecureString-обёртка `scripts/RUN_SERVICEIT_ALL_LEAD_CRM_SCHEMA_READONLY.ps1`, default offline, флаг `-InspectLive` вызывает только read-only методы.

После CI и pinned checkout выполнить:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -PythonPath $Python -InspectLive
```

Секрет корпоративного вебхука вводить только в скрытой консольной подсказке, не публиковать в чат/GitHub.

Ожидаемый PASS: `SERVICEIT_ALL_LEAD_SCHEMA_READONLY_PASS`, `crm_writes=0`. Возможен BLOCK (не ошибка источников): если поле имеет неподдерживаемые ограничения или metadata отсутствует. Тогда нужно отдельно разбираться с причиной, **не** отправлять write для теста.

## После PASS

Следующее требование — отдельно согласованный корпоративный адаптер и idempotent outbox для реальных лидов: источник → верификация → Score+badge → точная проверка дублей/клиентов → один разрешённый `crm.item.add` с полным набором и readback. Только после этого можно включать регулярный worker. Ни один зелёный Score не отменяет проверку legal/stop/customer.

Текущий PR — **preflight без writes**, не полноценный рабочий канал до Битрикс. Метка на плитке Канбана не появится только от успешного preflight.

Официальные API: 
- https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-fields.html
- https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-add.html
