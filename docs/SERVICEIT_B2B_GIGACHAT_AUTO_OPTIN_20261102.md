# Реальный GigaChat перед созданием новых B2B-Center HOLD

## Что изменено в коде

Раньше B2B-Center путь создавал HOLD-запись 1062/21 только с исходными полями. PR #71 добавил честный предварительный разбор (9 полей, AI_STATUS=NOT_RUN_SOURCE_UNVERIFIED) в единичный crm.item.add, не выдумывая Score. Этот PR добавляет **отдельно включаемый** истинный вызов GigaChat именно в этот источник, до создания новой карточки, когда для источника нет точного дубля в CRM/журнале.

Алгоритм: official B2B-Center notice → строгая валидация схемы → GitHub idempotency check + exact Bitrix source check → opt-in+privacy+GigaChat only on stripped subject → verify provider, confidence, exact evidence quote and allowed service → all 9 AI fields in the same original one-shot crm.item.add → readback. Если модель недоступна/не проходит quality gate, создаётся безопасный HOLD с NOT_RUN; никакой ложной AI-квалификации и no retry of existing one-shot writes.

Даже с настоящим GigaChat, закупка, ИНН и исходное извещение не подтверждены: результат только HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED. Lead Score числом и цветная оценка пока не назначаются. Это не ошибка: без исходных проверок невозможно дать обоснованную оценку 90/100.

## Однократная настройка GitHub Actions (владелец/администратор, не в чат)

В Repository Settings → Secrets and variables → Actions:

- Variable SERVICEIT_B2B_CRM_AUTO_ENABLED = YES — отдельное существующее разрешение на создание HOLD. Без него corporate writer не запускается.
- Variable SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED = YES — включить один вызов GigaChat для новой уникальной B2B-Center закупки.
- Variable SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED = YES — организация одобрила передачу **только темы письма** модели. Перед включением оценить режим обработки данных и договор с поставщиком модели.
- Variable AI_ENABLE определяется workflow автоматически из opt-in, AI_PROVIDER=gigachat.
- Secret GIGACHAT_CREDENTIALS — корпоративный API-ключ/credentials модели. Никогда не вставлять его в Issues/чат/Powershell stdout; при необходимости настроить GIGACHAT_SCOPE/GIGACHAT_MODEL согласно действующему корпоративному доступу.

Workflow .github/workflows/b2b-gmail-to-bitrix-hold.yml автоматически передаёт секрет только если оба флага GigaChat включены. Текущие настройки секретов/variables **не прочитаны и не изменены** из этого чата, поэтому включён ли реальный GigaChat на GitHub сейчас — неизвестно.

## Ограничения

- Новый код не изменяет карточку B2B-Center №4661617 задним числом и не запускает модель на уже существующих карточках.
- Другие источники — Bidzaar, SUPPMAN, ONLINECONTRACT и др. — не подключаются автоматически только этим изменением. Для них предназначен единый Bitrix event intake PR #72; worker квалификации и CRM update ещё нужно завершить и безопасно развернуть.
- Нет оснований считать vendor email доказанным запросом клиента. Нужно пройти проверку оригинальной закупки, заказчика/ИНН, ФНС, клиентской базы, стоп-листа, legal basis; только потом рассчитывать детерминированный Score и разрешать следующий шаг.
- Первая предыдущая попытка записи цветного badge №3 и восстановительная попытка имеют UNCERTAIN. Старые журналы не трогать.
- Даже при зелёном CI это **не live GigaChat acceptance**: нужно подтвердить по следующей новой HOLD-карточке и журналу GitHub Actions, не повторяя старые доставки.

### Целевая полная система для всех новых карточек

Приём нового события Bitrix → очередь ID → чтение карточки → проверка доступных данных → evidence + privacy gate → GigaChat и детерминированный Score при достаточности фактов → one-shot update всех нужных полей → readback. Непроверенные HOLD остаются HOLD с честным предварительным разбором, а не фиктивными «высокими» лидами.
