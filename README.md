# Service IT LeadGen — test assignment / pilot

Offline integration candidate: [readiness, validation and blockers](docs/INTEGRATION_READINESS_20261031.md).
Synthetic manager demo: `python -m leadpilot.cli acceptance-demo` (no CRM or AI calls).

**Отдельный от VALYQON AI репозиторий.** Прототип B2B лидогенерации по техническому заданию ООО «Сервис ИТ»: сигналы потребности → проверка компаний → AI-гипотеза → фиксированный Lead Score → подготовка данных для Битрикс24.

Включено: FastAPI, SQLite, API вакансий «Работа России», проверка ИНН/свежести/дублей, GigaChat-2 c Quality Gate, `read-only` проверка тестового Битрикс24, PowerShell, pytest, GitHub Actions.

**Статус на 01.11.2026**: архитектурный MVP + **подтверждённый корпоративный DEMO Bitrix24** (смарт-процесс 1062/21, карточка 3, Lead Score 90, 9 AI-полей, реальная однократная запись/проверка отчёта GigaChat, задача 26163). **Не промышленная эксплуатация и не реальные квалифицированные лиды.** Источники, согласия/глобальные дубли, телефония, каналы, продажи/KPI, финансовый модуль и пилот остаются в работе. Актуальная [матрица требований Микаила и точка продолжения на 02.11.2026](docs/SERVICEIT_MIKHAIL_REQUIREMENTS_CHECKPOINT_2026-11-01.md). Исторический [снимок от 30.10](docs/STATUS_2026-10-30.md), [инструкция на русском](README_RU.md), [безопасность](docs/SECURITY.md).

Запуск на Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File .\START_LAB.ps1`. Необходимо наличие Python 3.12 и работающего `py -3.12`. Всё происходит в отдельной `.venv`.

Тесты: `.\.venv\Scripts\python.exe -m pytest -q`.

Сохранение этапов: `powershell -NoProfile -ExecutionPolicy Bypass -File .\SAVE_STEP.ps1 -Message 'feat: этап'`.

**Не загружайте в GitHub** реальные токены и вебхуки, закрытые сведения заказчика, базы данных, экспортированные исходные записи или ТЗ без разрешения.
