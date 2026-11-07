# Сервис ИТ — миграция на корпоративный VPS и PostgreSQL

Подготовлено 7 ноября 2026. Это пакет кода и развёртывания, а не подтверждение, что VPS уже настроен.

## Получено от Михаила

Сервер: 80.66.79.156. PostgreSQL: только 127.0.0.1:5432, база leadgen, пользователь leadgen. Установлен Caddy, предложен домен leadgen.serviseit.ru. DNS, TLS, характеристики ОС, SSH, права PostgreSQL и реально работающий сервер ещё необходимо проверить. Пароли из переписки скрыты и НЕ доступны автоматизации. Не переносить root-пароль в GitHub или чат. После первичного администрирования заменить распространённые пароли.

## Что создано

1. leadpilot/serviceit_vps_pg_queue.py — отдельная таблица serviceit_vps_new_cards, атомарные PostgreSQL-блокировки FOR UPDATE SKIP LOCKED, однократная выдача карточки, состояние UNCERTAIN после сбоя вместо безусловного повтора.
2. leadpilot/serviceit_vps_ingress.py — отдельный FastAPI приёмник только официального события ONCRMDYNAMICITEMADD из serviseit.bitrix24.ru и проверка application_token. Приёмник НЕ делает запросов к CRM, не вызывает ИИ и не пишет ничего кроме ID в собственную очередь.
3. leadpilot/serviceit_vps_worker.py — координатор с 10-минутным циклом, максимум 6 карточек за цикл. В READONLY проверяет метаданные Bitrix. В LIVE повторно использует существующий проверенный exact worker, тот же GitHub encrypted CAS одноразовый журнал, Brave Web + LLM Context, основной RouterAI, резервный GigaChat, точную проверку 9 AI-полей. Сохраняет независимые проверки допуска, формального Score и запрета контактов.
4. Dockerfile.serviceit-vps, requirements-vps.txt и deploy/serviceit-vps.compose.yml — изолированный серверный пакет. Docker host networking нужен исключительно для доступа к локальному PostgreSQL; web ingress слушает ТОЛЬКО 127.0.0.1:18780. Non-root UID 10001, read-only rootfs, no-new-privileges, удалены Linux capabilities. Резервный GigaChat получает только проверенный локальный TLS trust anchor, без отключения сертификата.
5. deploy/serviceit-vps.caddy.example — конфигурация обратного прокси для публичного HTTPS, не заменяет другие сайты.
6. deploy/serviceit-vps-readonly-install.sh и workflow ServiceIT VPS guarded READONLY deployment — только ручной SSH-деплой с проверенным fingerprint хоста и контрольной суммой релиза, без включения CRM-записи.
7. ДВА прежних GitHub AI-workflow сохраняются, пока владелец не включит GitHub variable SERVICEIT_VPS_CUTOVER_ACTIVE=YES. Не отключать действующую автоматизацию до испытаний.

## Что должен подготовить оператор VPS

- Проверить ОС, CPU/RAM, установленный Docker Engine и Compose, доступность Caddy/DNS/TLS, бэкап/восстановление PG. PostgreSQL 5432 оставить недоступным снаружи.
- Создать отдельного SSH пользователя serviceitdeploy с ключом; не использовать root для штатного деплоя. Docker group фактически предоставляет root-эквивалентные права, ограничить пользователя и SSH.
- Создать вне Git папку HOME/serviceit/config/vps.env из deploy/serviceit-vps.env.example. Оставить WRITER_MODE=READONLY, CUTOVER_AUTHORIZED=NO, INTAKE_ENABLED=NO, VPS_POLL_ENABLED=NO.
- Создать приватную папку /etc/serviceit/secrets, доступную UID/GID 10001 с необходимыми правами. Не коммитить и не вставлять содержимое секретов в сообщения: pg_password, bitrix_webhook, bitrix_app_token, event_bridge_key, gh_ledger_key, github_ledger_pat, brave_api_key, routerai_key, gigachat_credentials. bitrix_app_token — ОТДЕЛЬНЫЙ секрет исходящих событий, не REST webhook. GitHub PAT должен иметь только минимальные необходимые права на encrypted ledger branch; event_bridge_key и gh_ledger_key должны совпадать с ключами существующей рабочей среды, а не быть случайно сгенерированы заново.
- Выделить пользователю leadgen права на создание одной новой таблицы очереди и индекса, не менять существующие бизнес-таблицы.
- В GitHub настроить vars SERVICEIT_VPS_HOST=80.66.79.156 и SERVICEIT_VPS_DEPLOY_USER=serviceitdeploy, секреты SERVICEIT_VPS_DEPLOY_SSH_KEY и SERVICEIT_VPS_SSH_KNOWN_HOSTS; SSH fingerprint проверить независимо. Создать защищённый Environment serviceit-vps-readonly.
- После подготовки вручную запустить workflow ServiceIT VPS guarded READONLY deployment с текстом подтверждения READONLY_ONLY. Скрипт запустит контейнеры и миграцию только собственной таблицы, затем проверит локальный /health. Это не включает реальные CRM-записи.

## Caddy, события и контролируемое переключение

- После проверки DNS/HTTPS вручную подключить конфигурацию Caddy для leadgen.serviseit.ru, проверить caddy validate. Зарегистрировать в корпоративном Bitrix24 корректное исходящее событие onCrmDynamicItemAdd на HTTPS путь /integrations/serviceit/bitrix24/new-card, проверить действующий application_token. До этого не включать INTAKE_ENABLED=YES.
- Подтвердить всю связку в режиме READONLY, корректный реальный PostgreSQL, ключи RouterAI/Brave/GigaChat, CAS ledger и запрет изменять исторические карточки.
- Сначала установить в GitHub vars SERVICEIT_VPS_CUTOVER_ACTIVE=YES, удостовериться, что старые AI jobs завершились. Затем только отдельным решением владельца на сервере разрешить WRITER_MODE=LIVE, CUTOVER_AUTHORIZED=YES, OLD_GH_WRITERS_DISABLED_ACK=YES и WORKER_MODEL_PRIVACY_APPROVED=YES. Для RouterAI требуется ROUTERAI_PRIVACY_APPROVED=YES_1062_21. Для 10-минутного опроса разрешить VPS_POLL_ENABLED=YES только с подтверждённой начальной UTC датой BITRIX_POLL_START_UTC.
- Новые карточки обрабатываются с существующей криптографической одноразовой защитой и без повторной записи при неопределённом результате; состояния REVIEW/UNCERTAIN разбираются вручную.
- На первой настоящей новой карточке проверить 9 AI полей, Brave, основной AI, ссылочные доказательства, STOP/дубли/Lead Score, менеджерскую задачу и Канбан. Пока нет этого теста, статус Live E2E — НЕ ПОДТВЕРЖДЁН.

## Что НЕ переносится этим первым безопасным релизом

Это подготовка ядра: Bitrix event + PostgreSQL очередь + AI/Brave + девять полей. Другие независимые GitHub Actions (STOP, FNS/МСП, постсозданная проверка дублей, задачи менеджерам, Kanban, финансовые автоматизации) ПОКА остаются на GitHub. Они требуют отдельных проверок, прежде чем переносить каждый writer и планировщик на сервер. Их нельзя безопасно отключить или дублировать без работающего VPS. Источники лидов НЕ подключаются, как и просил владелец.
