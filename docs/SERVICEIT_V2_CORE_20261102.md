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
5. Сначала оставить SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=NO, проверить postgres и статистику командой docker compose --env-file /private/path.env -f deploy/serviceit-v2.compose.yml exec gmail-worker python -m leadpilot.serviceit_v2_worker --status.
6. Только после отдельного одобрения доступа к почте установить SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=YES и перезапустить обработчик. Проверить реальное новое письмо, повторный запуск, отсутствие дублей, отдельные версии, отсутствие записи в CRM.

## Критические ограничения
- Это РЕАЛИЗОВАННЫЙ код сбора и хранения, не уже запущенный в облаке сервис: отдельная авторизация Gmail и проверенный сервер пока отсутствуют.
- НЕТ автоматической квалификации покупателей по ИНН/ФНС/Москве/МО/контактам и нет разрешённого CRM writer в новом контуре. Таблица crm_outbox создана только как будущая структура; никаких писем, контактов, лидов и CRM-карточек этот код НЕ создаёт.
- Входящее поле From пока не подтверждает SPF/DKIM; оригинал закупки, право использования данных, дату/статус и компанию надо проверять отдельно.
- Старую работающую систему GitHub Issue #66 -> Bitrix24 не отключать до подтверждения новой цепочки.

## Критерии приёмки
- CI: безопасный Docker Compose, mock Gmail OAuth, disposable Postgres, replay/изменение закупки, отказ без авторизации.
- Live: Gmail -> Postgres реальное письмо -> новый номер закупки -> повтор без дубля -> исправление как новая версия существующей закупки.
- При сбое не писать в Битрикс и не терять состояние; при достижении целей подключить квалификацию и защищённый CRM outbox отдельным PR.

Официальная документация: https://developers.google.com/workspace/gmail/api/guides/list-messages ; https://developers.google.com/workspace/gmail/api/guides/push ; https://developers.google.com/workspace/gmail/api/auth/scopes
