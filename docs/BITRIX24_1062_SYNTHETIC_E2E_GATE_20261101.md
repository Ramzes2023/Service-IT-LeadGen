# ServiceIT Bitrix24 1062 — synthetic E2E operator handoff (01.11.2026)

## Verified live configuration
- Portal: `serviseit.bitrix24.ru`; smart process `entityTypeId=1062`; `crm.type.id=13`; category `21`.
- Real guarded config apply: `SERVICEIT_1062_CONFIG_STATUS=APPLY_VERIFIED`, `FIELDS_CREATED=16`, `STAGES_CHANGED=6`, no client record writes; original eight commercial funnels unchanged according to operator log.
- Separate read-only post-apply: `SERVICEIT_CRM_SCHEMA=VERIFIED`, `FIELDS_MISSING=0`, `STAGES_RENAME_NEEDED=0`, `NEW_STAGE_NEEDED=False`.
- Current scope: schema complete; real item-level E2E not yet executed. Do not claim live handoff or production readiness.

## Next acceptance gate — one synthetic card only
An independent downloadable local operator archive was assembled and tested, named `ServiceIT_Bitrix24_1062_Synthetic_E2E_20261101.zip`. SHA-256 of that exact local archive: `a4c82f77e45d91cfb8dc7a3248a8adaab087e30db148fc245220689642ddfee3`. The binary archive and its code **have not been committed to this PR**. This document tracks the gate only; require separate source review before treating the artifact as part of the canonical repository.

Local independent checks: Python compile PASS and 7/7 isolated unittest mocks PASS. **No live portal write/read was performed by the assistant.** The operator uses existing project `.venv\Scripts\python.exe` and does not need installation. Its `-Live` mode requires a freshly rotated webhook and confirmed automation OFF, then explicitly targets only 1062/21 and checks 16 UF codes, six stages and `isAutomationEnabled=N/isBizProcEnabled=N`. It generates one synthetic item, gets/updates/checks stage, deletes the **same owned item only**, checks disappearance, and writes a sanitized local report. No sources, emails, calls, clients, deals or other funnel writes.

Safety handling: after ambiguous create/network timeout, **no automatic retry**. Local report under `%LOCALAPPDATA%\ServiceIT\Bitrix24-Synthetic-E2E\synthetic-*.json` allows controlled recovery scoped to the unique synthetic marker with read-back and ID confirmation. If cleanup cannot be verified, report `MANUAL_CLEANUP_REQUIRED` and stop; do not declare pass.

### Outstanding safety gate
A prior webhook secret was pasted into chat by mistake. **Rotate/revoke that old secret before any further live REST call**; rotation remains operator-unverified. Confirm no robots or automated notifications are active for process 1062 before testing. Existing admin approval covered configuration; the owner subsequently explicitly authorized a single synthetic item create/update/delete in the isolated process, no other changes.

Status: `BITRIX_SCHEMA_VERIFIED=YES`; `BITRIX_SYNTHETIC_LIVE_E2E=NOT_RUN`; `REAL_LEADS=0`; `SOURCE_ADAPTER=NOT_CONNECTED`; `PRODUCTION_READY=NO`. Keep PR draft and do not merge before a successful clean portal test and review.


## 01.11.2026 — Windows cp1252 guard-test repair (no live CRM writes)

Owner ran the PowerShell-input variant of the synthetic E2E operator. The **10-test local guard suite stopped with 1 error** before asking for any live webhook and **before any CRM item operation**. Root cause in the test, not the API: `Path(svc.__file__).read_text()` used the host Windows default `cp1252` and failed to decode byte `0x9d` in UTF-8 Python source (`UnicodeDecodeError`).

The revised downloadable artifact is `ServiceIT_Bitrix24_1062_E2E_WIN_ENCODING_FIXED_20261101.zip`, exact SHA-256 `89a412f48165169baa8aa80e6ca5bc9b2ca42c53e0bedc71d16c3bc15d62b129`. **Minimal change**: `read_text(encoding='utf-8')` in the guard test; no changes to live API workflow. Independent local `unittest`: **10 passed**, Python compile PASS. This archive is **not yet part of the GitHub source tree**; retain as an independent test artifact.

Next operator step: download the new ZIP, unpack to a *new folder*, and run the guarded PowerShell operator; do **not** reuse the previous Windows encoding-failing ZIP. A live result has not yet been observed. The previously disclosed webhook must be rotated before using the live mode; robots must be checked OFF. Only one synthetic item in 1062/21 is authorized; never rerun the live operation after an ambiguous create without inspecting the saved report and recovering the owned marker.


## 01.11.2026 — успешный реальный синтетический E2E; переход к программному mapping

**Пользователь фактически выполнил** Windows-оператор `ServiceIT_Bitrix24_1062_E2E_WIN_ENCODING_FIXED_20261101.zip` и прислал вывод:

```text
Ran 10 tests in 0.199s
OK
SERVICEIT_1062_LOCAL_GUARD_TESTS=PASS
SERVICEIT_SYNTHETIC_E2E_STATUS=PASS_SYNTHETIC_E2E_CLEAN
SERVICEIT_SYNTHETIC_E2E_REPORT=.../synthetic-20261101T134333-12c740aa.json
SERVICEIT_CRM_SYNTHETIC_CARD_LEFT=0; REAL_CUSTOMER_WRITES=0
SERVICEIT_1062_OPERATOR_EXIT=0
SERVICEIT_1062_OPERATOR=COMPLETE
E2E_EXIT=0
```

Локальный приватный отчёт на ПК: `%LOCALAPPDATA%\ServiceIT\Bitrix24-Synthetic-E2E\synthetic-20261101T134333-12c740aa.json`. Проверенный оператором результат: одна тестовая карточка создана, прочитана/обновлена и удалена, **0 осталось**. Корпоративные данные и прочие воронки не изменялись по выводу оператора. Секрет вебхука в GitHub не добавлялся. Подтверждение ротации вебхука — только ответ оператора на закрытый guard-вопрос, независимо статус ротации не проверялся.

После этой приёмки добавлен в PR независимый от реального CRM **offline-only mapping**: `leadpilot/bitrix_1062_mapping.py`, `tests/test_bitrix_1062_mapping.py`, `scripts/RUN_SERVICEIT_BITRIX_1062_MAPPING_ACCEPTANCE.ps1`. Он сопоставляет прошедшие offline-валидацию *строго синтетические* намерения с оригинальными 16 пользовательскими полями `UF_CRM_13_...`, системными `categoryId=21`, `stageId=DT1062_21:NEW`, `entityTypeId=1062`, `useOriginalUfNames=Y`. Защита проверяет ИНН, дату, фиктивные источник/URL, формат данных, стабильный ключ `ИНН+signal_id`, отсутствие контактов и чужих полей. Выход — **только план, без HTTP**, и он не является разрешением на live запись.

Результат локальных unit-тестов разработчика: **26 passed** (изолированный модуль и негативные сценарии). Интеграционный тест с реальным `leadpilot.bitrix_handoff_dryrun` добавлен в GitHub и ждёт независимой проверки на рабочем дереве; размещён Windows runner. Проверка GitHub Actions на последнем коммите пока отсутствует.

**Актуальная граница:** `BITRIX_LIVE_SCHEMA=VERIFIED`, `BITRIX_SYNTHETIC_LIVE_E2E=PASS_CLEAN`, `BITRIX_SOURCE_ADAPTER=NOT_CONNECTED`, `MANAGER_APPROVAL_AND_DEDUP_LIVE=NOT_VERIFIED`, `PRODUCTION_READY=NO`. Этим документом обновлены прежние исторические отметки `NOT_RUN`.

Следующая задача — интеграционные тесты контракта в изолированном worktree, независимый обзор live-адаптера и отдельная проверка бизнес-процесса менеджера. Источники остаются вне объёма текущего этапа.
