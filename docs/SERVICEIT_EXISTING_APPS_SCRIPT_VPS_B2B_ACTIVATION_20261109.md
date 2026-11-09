# B2B-Center: существующий Google Apps Script → VPS → Bitrix24

09.11.2026. Issue #314, PR #316. Альтернатива с новым Google Cloud OAuth PR #315 НЕ нужна, пока работает уже авторизованный Gmail Apps Script.

## Архитектура

Gmail уведомление B2B-Center → существующий Code.gs (5-минутный таймер) → HTTPS /integrations/serviceit/b2b-center/notice с HMAC подписью → Caddy → отдельный 127.0.0.1:18781 Python receiver → PostgreSQL durable inbox → отдельный VPS writer → проверка положительного CRM дубля + точного source ID → PostgreSQL POST_UNCERTAIN receipt ДО crm.item.add → одна HOLD карточка в 1062/21 → crm.item.get readback → native PostgreSQL card queue → 5-минутный RouterAI/9 полей/Канбан/задача/Score.

GitHub не является транспортом заявок, старые Github-to-CRM writers остаются OFF. Данные из Gmail передаются только минимальным публичным уведомлением. Ни исходящих звонков, ни писем клиенту, ни участия в торгах.

## Обязательная безопасная последовательность

1. CI по PR #316 должен быть PASS. Проверить здоровье существующего VPS и 5-минутного ядра, бэкапы, отсутствие старых GH CRM writers.
2. Развернуть ТОЛЬКО новые receiver и writer из deploy/serviceit-vps-b2b-direct.compose.yml. На старте оба flags SOURCE_RECEIVER_ENABLED=NO, B2B_WRITER_ENABLED=NO. Никаких изменений coordinator/aux/business/FNS/observer.
3. Передать уже существующий private Apps Script HMAC secret на VPS в /etc/serviceit/secrets/b2b_ingest_hmac с правом чтения только uid 10001; не выводить ключ в чат, git, Issue, stdout или Compose environment. Ключ был предусмотрен прежним B2B Apps Script V9 установщиком как SERVICEIT_B2B_INGEST_HMAC_KEY (GitHub secret). Если ключи различаются, signed probe не пройдёт и отправка не включится.
4. В действующий Caddy сайт leadgen.serviseit.ru вставить ТОЛЬКО фрагмент deploy/serviceit-vps-b2b.caddy.fragment до общего catch-all 404. Перед reload — backup и caddy validate. HTTPS маршруты /health и старый Bitrix event оставить без изменений, новый 18781 не открывать наружу.
5. Включить только receiver: SERVICEIT_VPS_B2B_RECEIVER_ENABLED=YES, SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK=YES, writer=NO. HTTPS GET /integrations/serviceit/b2b-center/health должен ответить B2B_INGRESS_ALIVE и source_enabled=true. POST /probe с HMAC и PG SELECT 1 — B2B_VPS_SIGNED_PROBE_PASS, crm_writes=0.
6. Открыть существующий авторизованный Gmail Apps Script проект; проверить serviceITStatus(), реальный Google trigger и execution logs. Заменить ТОЛЬКО Code.gs на проверенный код из main после merge, сохранить secret.gs и прежний Bitrix Event Bridge без изменений. Старый V9 installer -Apply НЕ запускать: он нацелен на устаревший GitHub INGEST.
7. Только когда receiver/probe доступны, пользователь ОДИН раз запускает функцию activateVpsB2BCenter() в редакторе Google Apps Script. Функция проверяет HTTPS health + подписанный no-write probe, ставит новое server-cutover время и пяти-минутный trigger. Почтовая история до cutover автоматически не импортируется.
8. Убедиться, что новые настоящие письма после активации поступают в PostgreSQL как PENDING без создания карточек. Проверить source ID, sender DKIM/DMARC, URL, dedup. Не считать письма до timestamp новыми.
9. При чистой positive-control CRM проверке B2B-CENTER-4665948, реальном разрешении источника и бизнес-приёмке включить VPS B2B writer. Первичный лимит: 1 новая HOLD карточка за 5 минут и 3 за 24 ч. POST_UNCERTAIN не повторять: только ручная сверка чтением.
10. Реальная E2E-приёмка: новая карточка smart process 1062/21, один add, exact readback, DB VERIFIED, native queue, реальный RouterAI + 9 AI полей, Канбан, менеджерская задача, FNS/Score (при отсутствии доказательств только HOLD). Фиксировать обезличенные факты в Issue #314. Пока E2E не выполнен, НЕ писать «источник live».

## Важные ограничения ИИ

RouterAI реальный synthetic вызов PASS (GitHub run 42645774368). Chromium реальный PUBLIC_RENDER PASS. Brave mounted/настроен (run 42638380176). Этого достаточно для технической готовности, но НЕ доказывает глубокое исследование каждой живой карточки. Веб-браузер ограничен allowlist публичных сайтов; STOP, ИНН, регион, ФНС и юридическое право контакта проверяются независимо. Квалифицированный лид без подтверждений запрещён.

## Статус

Код PR #316 и тесты ≠ production deployment. Новый Docker receiver, Caddy route, shared HMAC на VPS и live Google trigger должны быть отдельно подтверждены. Не менять production автоматически при отсутствии подтверждённых полномочий/секретов.
