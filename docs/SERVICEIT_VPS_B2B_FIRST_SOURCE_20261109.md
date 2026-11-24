# B2B-Center: первый источник на VPS (пилот 09.11.2026)

Tracking: Issue #314. **Код в PR — не подтверждение работающего production.**

## Подтверждённые исходные условия

- VPS coordinator / AUX / business: реальные 300–305-секундные циклы, Issue #307.
- Gmail сегодня получает официальные B2B-Center письма со свежими закупками, включая 1С, IT-оборудование и ККТ. Тестовое письмо подписано DKIM и прошло DMARC по Google Authentication-Results.
- Старые четыре GitHub-to-CRM source writer заблокированы PR #302, их нельзя включать.
- Официальный B2B-Center API существует, однако метод для поиска закупок поставщиком и тариф документируются в личном кабинете. Без подтверждения не использовать неизвестные API или HTML-скрейпинг.

## Целевая схема

B2B-Center → официальное Gmail-уведомление → отдельный VPS worker (Gmail API, Gmail readonly OAuth) → Gmail DKIM/DMARC + проверка даты ПОСЛЕ активации → точный номер и разрешённый URL закупки → независимый Bitrix source duplicate lookup → PostgreSQL one-use reservation → только один новый HOLD smart-process 1062/21 → exact CRM readback → native PostgreSQL card queue → действующий 5-минутный RouterAI/9 AI/Канбан/task/Score.

Повторное письмо, новая версия того же номера и старый CRM элемент не создают вторую карточку. Внешние контакты, участие в торгах и автоматическая квалификация запрещены. Отсутствие ИНН, ФНС, клиента/STOP или законного контакта означает HOLD, не зелёный VERIFIED.

## Порядок безопасного развёртывания

1. В Google Cloud OAuth создать согласованный клиент Gmail readonly для почтового ящика B2B-Center, выдать владельцем разрешение и получить offline refresh token. Учитывать ограничения restricted scope и время жизни токенов в Google Testing. Существующий ChatGPT Gmail коннектор НЕ даёт токен для VPS. Не отправлять OAuth токены в GitHub или чат.
2. Установить секреты на VPS в защищённый каталог сервисного uid 10001: b2b_gmail_client_id, b2b_gmail_client_secret, b2b_gmail_refresh_token. Права файлов — как у текущих production secrets.
3. На VPS в приватном env задать новый точный SERVICEIT_VPS_B2B_ACTIVATION_UTC и SERVICEIT_VPS_B2B_GMAIL_ACCOUNT. Оставить SOURCE_ENABLED=NO и CRM_WRITE_ENABLED=NO до проверок.
4. Выполнить read-only preflight: здоровые 5 контейнеров и PostgreSQL, настоящие Gmail OAuth разрешения, доступ к строго известной карточке B2B-CENTER-4665948 в точном CRM duplicate lookup, отсутствие тестового source ID, все 9 AI-полей и остальные разрешения 1062/21. Старые GitHub source writers обязаны оставаться disabled.
5. Развернуть ТОЛЬКО новый отдельный Docker сервис из deploy/serviceit-vps-b2b.compose.yml. Сначала SOURCE_ENABLED=YES, CRM_WRITE_ENABLED=NO; подтвердить чтение свежего реального письма с аутентификацией и отсутствие CRM POST.
6. После чистой приемки read-only включить только CRM_WRITE_ENABLED=YES и первый canary: не более 1 карточки за цикл, 3 за 24 часа. Никакого backfill старых писем и массового импорта. Другие исходники OFF.
7. Подтвердить по логам и реальному CRM exact GET: новая 1062/21 HOLD карточка с уникальным source ID, единственный crm.item.add, PostgreSQL VERIFIED receipt + queued ID, затем отдельный RouterAI 9 полей/Канбан/задачи/Score readback. При любой неопределенности внешнего POST — POST_UNCERTAIN, никаких автоматических или ручных повторов.
8. Проверить отсутствие исходящих писем и звонков. После успешного пилота отдельно решить расширение лимита и менеджерскую бизнес-приёмку.

## Временные ограничения и статус

VPS проверяет почту каждые 300 секунд, но часть уведомлений B2B-Center приходит с периодичностью около 3 часов — фактическое обнаружение не может предшествовать письму.

- Код source worker и Compose: staged PR.
- Независимое production Google OAuth на VPS: НЕ ПОДТВЕРЖДЕНО.
- Production Docker B2B-source container: НЕ ЗАПУЩЕН.
- Реальная новая HOLD карточка с VPS и RouterAI E2E: НЕ ПОДТВЕРЖДЕНА.
- Корпоративный STOP, разрешение контакта: НЕ ПОДТВЕРЖДЕНЫ.

**Нельзя объявлять LIVE до контрольного создания и прочтения настоящей новой карточки.**
