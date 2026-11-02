# Service IT Lead Engine 2.0 — первый автономный контур

Задача: https://github.com/Ramzes2023/Service-IT-LeadGen/issues/83

## Реализовано
- Отдельный Gmail API OAuth2 клиент с доступом только на чтение: поиск сообщений с официального адреса marketplace@b2b-center.ru и получение MIME, без изменений почты, без связи с ChatGPT automation.
- PostgreSQL-хранилище отдельных Gmail message ID, закупок и версий каждого тендера. Дубли не создают новую закупку, каждая новая редакция сохраняется отдельно. Тела писем не хранятся.
- Проверка предмета закупки по правилам Сервис ИТ; статусы только OUT_OF_SCOPE или TENDER_OPPORTUNITY_HOLD, никаких ложных лидов.
- Изолированный Docker Compose, автоперезапуск и регулярная проверка раз в 300 секунд; по умолчанию источник выключен.
- Реальные тесты на отдельной временной PostgreSQL и имитации Gmail OAuth API в GitHub Actions.

## Однократная авторизация и ввод в эксплуатацию
1. Выбрать и отдельно согласовать сервер. Не использовать VPS VALYQON и его данные без отдельного разрешения.
2. Создать собственный Google Cloud OAuth-проект, включить Gmail API, предоставить ровно gmail.readonly и получить refresh token по согласию владельца почты. OAuth токены из подключения Gmail в ChatGPT нельзя передать этому серверу. Для restricted scopes действуют требования Google; у OAuth приложения в Testing refresh token может истечь через 7 дней.
3. Создать отдельный локальный секретный файл среды по образцу deploy/serviceit-v2.env.example: сильный пароль новой PostgreSQL и OAuth client ID, client secret, refresh token. Никогда не передавать секреты в Issue, commit или чат.
4. В отдельном проверенном Docker окружении выполнить docker compose --env-file /private/path.env -f deploy/serviceit-v2.compose.yml config --quiet и затем docker compose --env-file /private/path.env -f deploy/serviceit-v2.compose.yml up -d --build.
5. Сначала оставить SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=NO. По умолчанию запускается ТОЛЬКО Postgres, потому что worker под профилем gmail-live. Проверить статистику командой docker compose --env-file /private/path.env -f deploy/serviceit-v2.compose.yml --profile gmail-live run --rm gmail-worker python -m leadpilot.serviceit_v2_worker --status.
6. Только после отдельного одобрения доступа к почте установить SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=YES и выполнить docker compose --env-file /private/path.env -f deploy/serviceit-v2.compose.yml --profile gmail-live up -d --build. Без явного профиля обработчик не стартует. Проверить реальное новое письмо, повторный запуск, отсутствие дублей, отдельные версии, отсутствие записи в CRM.

## Критические ограничения
- Это РЕАЛИЗОВАННЫЙ код сбора и хранения, не уже запущенный в облаке сервис: отдельная авторизация Gmail и проверенный сервер пока отсутствуют.
- НЕТ автоматической квалификации покупателей по ИНН/ФНС/Москве/МО/контактам и CRM writer реализован как ОТКЛЮЧЁННЫЙ по умолчанию безопасный модуль; запуск требует отдельного решения менеджера и разрешённой авторизации. Таблица crm_outbox создана только как будущая структура; никаких писем, контактов, лидов и CRM-карточек этот код НЕ создаёт.
- Входящее поле From пока не подтверждает SPF/DKIM; оригинал закупки, право использования данных, дату/статус и компанию надо проверять отдельно.
- Старую работающую систему GitHub Issue #66 -> Bitrix24 не отключать до подтверждения новой цепочки.

## Критерии приёмки
- CI: безопасный Docker Compose, mock Gmail OAuth, disposable Postgres, replay/изменение закупки, отказ без авторизации.
- Live: Gmail -> Postgres реальное письмо -> новый номер закупки -> повтор без дубля -> исправление как новая версия существующей закупки.
- При сбое не писать в Битрикс и не терять состояние; при достижении целей подключить квалификацию и защищённый CRM outbox отдельным PR.

Официальная документация: https://developers.google.com/workspace/gmail/api/guides/list-messages ; https://developers.google.com/workspace/gmail/api/guides/push ; https://developers.google.com/workspace/gmail/api/auth/scopes


## Этап D — новая очередь доставки в Битрикс24 (код, не включена в production)

Реализован модуль `leadpilot/serviceit_v2_delivery.py` и оператор `leadpilot/serviceit_v2_delivery_operator.py`. Он использует **собственную PostgreSQL outbox**, не зависит от GitHub Issue #66, от ChatGPT или GigaChat. Целевая CRM строго `entityTypeId=1062`, `categoryId=21`, стадия `DT1062_21:NEW`, только `tender-opportunity / HOLD`. Квалифицированные лиды не создаёт.

**Этапы и защита:**
1. Отдельно уполномоченный оператор с разрешением менеджера и доступом к приватной базе проверяет закупку и получает внешний номер решения `SVCIT-...`; затем устанавливает локально `SERVICEIT_V2_HOLD_STAGING_APPROVED=YES` и вызывает `python -m leadpilot.serviceit_v2_delivery_operator --stage-one 4661617 --approval-ref SVCIT-OWNER-20261102-A1` (номер — иллюстрация синтетической команды, **НЕ использовать на настоящей карточке №7**). Наличие просто строки `SVCIT-...` не доказывает выдачу разрешения; оно должно быть согласовано независимо.
2. При отдельном корпоративном разрешении на запись администратор передаёт `SERVICEIT_V2_BITRIX_WEBHOOK` через изолированный менеджер секретов на сервере. Включает **оба** локальных переключателя `SERVICEIT_V2_CRM_HOLD_ENABLED=YES` и `SERVICEIT_V2_CRM_WRITES_APPROVED=YES` с лимитом/контролем операций. **В текущем Compose этих переменных и webhook нет: опасный автоматический запуск исключён.**
3. Одна контролируемая операция `python -m leadpilot.serviceit_v2_delivery_operator --deliver-once` сначала переводит выбранный PENDING в RESERVED в PostgreSQL, затем выполняет только `crm.type.getByEntityTypeId`, `crm.category.list`, `crm.item.fields`, `crm.status.list`, `crm.item.list`, при отсутствии дубля — **один** `crm.item.add` и `crm.item.get`. После подтверждения — VERIFIED, ID карточки сохраняется.
4. Если удалённый REST ответил неопределённо, операция остаётся в `POST_UNCERTAIN`. **Никогда автоматически не повторять POST**; отдельно провести `--reconcile-one <id>` (только CRM-list/get) и сравнить номер источника и категорию. При отсутствии найденной карточки оставить на ручную проверку.
5. Оператор `--status` возвращает только агрегированные состояния. Не раскрывать секреты, исходное тело писем, персональные контакты и корпоративные данные. Никогда не удалять резервирование ради повторной попытки.

**Критическое ограничение:** корректность обнаружения фальшивых заказчиков и официальная проверка ФНС/ИНН/Москва/МО ещё не внедрены; интерфейс staging требует внешнего одобрения. В новом v2 нет запущенного CRM worker, GigaChat, самостоятельного lead promotion или авто-отправки исторических писем. Это сознательная граница текущего этапа. Код нельзя считать подтверждённой живой записью без отдельной приёмки в корпоративной CRM.
