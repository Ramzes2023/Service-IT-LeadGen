# Безопасная диагностика BADGE_READBACK_UNCERTAIN — 02.11.2026

Пользователь выполнил однократную команду `RUN_SERVICEIT_KANBAN_SCORE_BADGE.ps1 -InspectLive -ApplyDemo -ConfirmDemoOnly` на корпоративном Bitrix24. Оператор вернул:

```
status = BADGE_BLOCKED_OR_UNCERTAIN
safe_reason_code = BADGE_READBACK_UNCERTAIN
blind_retry = FORBIDDEN
```

**Из этого нельзя заключить, что запись не произошла.** Код дошёл до `crm.item.update`, но точное поле `UF_CRM_13_SVCIT_AI_SCORE_BADGE` после `crm.item.get` не совпало с `🟢 90/100 — высокий приоритет проверки`. В журнале операции может быть `UNCERTAIN`. Bitrix24 предупреждает, что некорректный ключ в `fields` может быть **проигнорирован** без явной ошибки. Возможны также форматирование/отсутствие возвращённого нового поля. Пытаться повторно записать значение с тем же или иным StateDir запрещено.

## Разработан специальный аудит без записи

- `leadpilot/serviceit_kanban_badge_recovery.py`, `scripts/RUN_SERVICEIT_KANBAN_BADGE_READONLY_DIAG.ps1`.
- Только чтение `crm.item.get` дважды (оригинальные UF + camelCase), `crm.item.fields` дважды, `userfieldconfig.list/get` для кода, только для smart process 1062, item 3. Чтение исходного Score 90, версии, наличия отчёта и точного DEMO identity.
- Локальный SQLite журнал читается **с URI mode=ro, без создания таблиц/файлов**. Отображаются только `VERIFIED`, `UNCERTAIN`, `NOT_RESERVED`; никакого сброса/новых флагов.
- Ни значение всех полей/AI-отчёта, ни credentials/token, ни содержимое неподтверждённого badge не печатаются. Для badge показываются безопасные shape, длина, 12 символов digest и match flags.
- Без `-InspectLive` аудит только локальный, без webhook и CRM запросов; `-InspectLive` выдаёт единый сводный JSON.
- **Никаких `crm.item.update`, `userfieldconfig.add`, `-ApplyDemo`, массовой обработки, звонков, AI inference, открытия браузера.**

Рекомендуемый безопасный запуск из pinned worktree:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -PythonPath $Python -InspectLive
```

После него сравнить `readback_with_original_names`, `readback_with_camelcase_names`, `badge_item_fields`, `ledger.items` и только **потом** решать, нужен ли фикс кодировки, маппинга/транзакции или ещё одно независимое разрешённое изменение. Следующий write не выполнять, пока нельзя однозначно установить факт предшествующей записи. Отдельное отображение badge на плитке Kanban через UI пока не сделано.

Официальные контракты:
- https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-get.html
- https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-update.html
