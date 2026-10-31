# Service IT LeadGen — test assignment / pilot

Offline integration candidate: [readiness, validation and blockers](docs/INTEGRATION_READINESS_20261031.md).
Synthetic manager demo: `python -m leadpilot.cli acceptance-demo` (no CRM or AI calls).

**Отдельный от VALYQON AI репозиторий.** Прототип B2B лидогенерации по техническому заданию ООО «Сервис ИТ»: сигналы потребности → проверка компаний → AI-гипотеза → фиксированный Lead Score → подготовка данных для Битрикс24.

Включено: FastAPI, SQLite, API вакансий «Работа России», проверка ИНН/свежести/дублей, GigaChat-2 c Quality Gate, `read-only` проверка тестового Битрикс24, PowerShell, pytest, GitHub Actions.

**Статус**: лабораторный MVP. Нет подтверждённой записи в CRM или полноценной AI-квалификации реальных лидов. См. [фактический прогресс](docs/STATUS_2026-10-30.md), [инструкцию на русском](README_RU.md) и [безопасность](docs/SECURITY.md).

Запуск на Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File .\START_LAB.ps1`. Необходимо наличие Python 3.12 и работающего `py -3.12`. Всё происходит в отдельной `.venv`.

Тесты: `.\.venv\Scripts\python.exe -m pytest -q`.

Сохранение этапов: `powershell -NoProfile -ExecutionPolicy Bypass -File .\SAVE_STEP.ps1 -Message 'feat: этап'`.

**Не загружайте в GitHub** реальные токены и вебхуки, закрытые сведения заказчика, базы данных, экспортированные исходные записи или ТЗ без разрешения.
