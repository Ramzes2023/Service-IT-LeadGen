# Service IT — установка Apps Script без Node.js, npm и clasp

Дата: 2026-11-02. **Только этап подготовки владельцем; не production-деплой.**

## Почему нужен этот способ
Предыдущий Windows-установщик зависел от скачивания portable Node.js; его V2 закончил `SERVICEIT_APPSSCRIPT_INSTALL=NOT_COMPLETED` и не менял существующий Google Apps Script. V3 с закреплённым Node.js был подготовлен в предыдущем чате, но его результат на Windows не подтверждён. В этом документе дан резервный путь, вообще не требующий Node.js, `npm`, `npx` или `clasp`.

## Что делает помощник
`scripts/OPEN_SERVICEIT_APPS_SCRIPT_NO_NODE.ps1` только:
- считывает существующий Windows Git clone `%USERPROFILE%\Service_IT_GitHub_Repo\Service-IT-LeadGen`, выполняет `git fetch` для DRAFT PR #84;
- сверяет два **точных blob SHA** официально подготовленных `integrations/apps_script/Code.gs` и `appsscript.json`, останавливается при изменениях;
- считывает один из двух файлов, копирует его в локальный буфер обмена; открывает список скриптов Google в браузере.

Помощник **не** авторизуется в Google, не меняет редактор Google, не получает токены и пароли, не включает Gmail пересылку или триггеры, не обращается в Битрикс24. Текущие карточки #3/#7 не затрагиваются. Наличие готового кода не является доказательством работающей интеграции.

## Безопасная установка владельцем в уже существующий Apps Script

1. Войти в Google под `ramzesisayev444@gmail.com`. Открыть **существующий** проект **Service IT — Gmail Tender Automation** (не создавать второй). Перед заменой кода создать копию старого `Код.gs` и `appsscript.json` в приватном локальном архиве. Если непонятно, какой проект — не вставлять никуда.
2. В локальном клоне получить вспомогательную ветку либо скачать файл через GitHub UI; проверить, что установленный Git доступен. Для проверки в Windows PowerShell 5.1:

```powershell
$Repo = "$env:USERPROFILE\Service_IT_GitHub_Repo\Service-IT-LeadGen"
git -C $Repo fetch origin fix/serviceit-appscript-no-node-safe-handoff-20261102
$Script = Join-Path $env:TEMP 'OPEN_SERVICEIT_APPS_SCRIPT_NO_NODE.ps1'
git -C $Repo show 'FETCH_HEAD:scripts/OPEN_SERVICEIT_APPS_SCRIPT_NO_NODE.ps1' | Set-Content -LiteralPath $Script -Encoding ASCII
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -Part Code
```

3. Помощник выводит `REVIEWED_GIT_BLOBS=PASS` и кладёт **только проверенный исходный Code.gs** в буфер. В редакторе существующего проекта открыть `Код.gs`, заменить **только после бэкапа** через Ctrl+A / Ctrl+V, сохранить.
4. В Google редакторе открыть ⚙ **Настройки проекта** и поставить **Показывать файл манифеста appsscript.json в редакторе**. Вернуться в PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -Part Manifest
```

5. В редакторе открыть `appsscript.json`. **После бэкапа** вставить содержимое буфера, сохранить. Проверить `Gmail API (v1)` и `runtimeVersion: V8`. Не использовать кнопки «Развернуть» или установки триггеров на этом этапе.
6. Выбрать в меню функций **`checkB2BCenter`** и нажать **Выполнить**. Это только чтение авторизованного Gmail без отправки данных наружу. Стандартное Google OAuth-разрешение `gmail.readonly` пользователь подтверждает только сам. Ожидаемые безопасные маркеры:
   `SERVICEIT_MODE=READ_ONLY`;
   `B2B_VENDOR_MESSAGES_WITHIN_30_DAYS=N`;
   `EXTERNAL_TRANSMISSION=0`;
   `CRM_WRITES=0`.
   Если ошибка или запрашивается неожиданный scope — остановиться, ничего не активировать.
7. Запустить **`serviceITStatus`** для проверки конфигурации. До развёртывания отдельного разрешённого HTTPS-приёмника ожидается отсутствие триггера и `SERVICEIT_FORWARDING_ENABLED=false`.

## Чего делать ПОКА НЕЛЬЗЯ
- Не выполнять `installPollingTrigger`, `pollAndForwardB2B`, не выставлять `SERVICEIT_SEND_ENABLED=YES` до готового корпоративно разрешённого HTTPS+HMAC-приёмника и PostgreSQL.
- Не вставлять HMAC, Gmail/OAuth секреты, webhook Битрикс, токены или MIME письма в чат, GitHub Issues или исходники.
- Не использовать VALYQON VPS/домены, другой проект, старый webhook или личные учетные данные без отдельного разрешения.
- Не выключать существующий мониторинг и не считать старую карточку #7 доказательством полного нового E2E.

## Следующий реальный production gate
DRAFT PR #84 содержит Apps Script, FastAPI HMAC receiver, PostgreSQL и read-only/dedup tests, но ещё не развёрнут на независимом одобренном сервере с HTTPS-доменом. Нужно отдельно запустить и проверить **без CRM записи**: signed test → HTTP ACK → PostgreSQL commit → replay dedup → реальное новое Gmail уведомление → корректное принятие. Только затем разрешён `installPollingTrigger`, после отдельного допуска — интеграция в CRM 1062/21 с обоснованными проверками потребности/ФНС/дублей/согласованной политикой. Никаких квалифицированных лидов из одного email.

Источник: PR #84 и Issue #83 в `Ramzes2023/Service-IT-LeadGen`; рабочая инструкция по защищённому получателю `docs/SERVICEIT_V2_APPS_SCRIPT_INGRESS.md` находится в ветке PR #84.
