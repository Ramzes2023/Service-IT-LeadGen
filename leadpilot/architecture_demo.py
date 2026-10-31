"""One-command non-source architecture showcase with SYNTHETIC data only.

Produces a browser-friendly static HTML and JSON after exercising real
local core logic. No HTTP listener, no CRM requests, no messages, no calls.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from .core import ingest, qualify, suppress, metrics
from .workflow_ops import initialize, review, stage_simulation, deliver_simulation, report

CARDS = (
    ("Lead intake", "Companies and independent signals; exact INN never merges on shared phone/domain"),
    ("Lead Score", "Deterministic local scoring with explanations and a version"),
    ("Manager review", "Review with reason and immutable, local audit trail"),
    ("Safety & stop list", "Existing customer, STOP and expired signal block simulated handoff"),
    ("Bitrix24 boundary", "Durable local simulation and retry; real portal is NOT CONNECTED"),
    ("KPI", "Count local steps without inventing revenue, contracts or client consent"),
)


def simulate() -> dict:
    now = date.today()
    with TemporaryDirectory(prefix="serviceit-architecture-demo-") as directory:
        db = Path(directory) / "demo.sqlite3"
        initialize(db)
        sample = {
            "inn": "7700000009", "name": "ООО «ДЕМО-ПРОЕКТ 1»",
            "region": "Москва", "industry": "розничная торговля",
            "source_name": "synthetic-fixture",
            "source_url": "https://example.invalid/demo/it-need",
            "signal_type": "procurement",
            "signal_date": (now - timedelta(days=1)).isoformat(),
            "evidence": "Синтетический запрос на внешнее сопровождение 1С",
        }
        a = ingest(db, sample)
        a_repeat = ingest(db, sample)
        another = ingest(db, {
            **sample, "source_name": "synthetic-second",
            "source_url": "https://example.invalid/demo/second-signal",
            "signal_type": "new_store",
            "evidence": "Синтетическое открытие нового магазина",
        })
        b = ingest(db, {
            **sample, "inn": "7800000008",
            "name": "ООО «ДЕМО-КЛИЕНТ 2»",
            "source_url": "https://example.invalid/demo/current-client",
            "existing_customer": True,
        })
        qualify(db, today=now.isoformat())
        with sqlite3.connect(db) as con:
            signals = [
                r[0] for r in con.execute(
                    "SELECT id FROM signals WHERE company_id=? ORDER BY id", (a["company_id"],)
                )
            ]
            b_signal = con.execute(
                "SELECT id FROM signals WHERE company_id=?", (b["company_id"],)
            ).fetchone()[0]
        for sid in signals:
            review(db, a["company_id"], sid, decision="relevant",
                   reviewer_role="manager", reason="Синтетический менеджер проверил сигнал",
                   today=now)
            stage_simulation(db, a["company_id"], sid, today=now)
        failure = deliver_simulation(db, service_available=False, today=now)
        recovered = deliver_simulation(db, service_available=True, today=now)
        duplicate_replay = deliver_simulation(db, service_available=True, today=now)
        review(db, b["company_id"], b_signal, decision="relevant",
               reviewer_role="manager", reason="Синтетическая проверка существующего клиента",
               today=now)
        existing_held = stage_simulation(db, b["company_id"], b_signal, today=now)
        suppress(db, sample["inn"], "all", "Синтетическое прекращение рекламы")
        stopped_held = stage_simulation(db, a["company_id"], signals[0], today=now)
        with sqlite3.connect(db) as con:
            company_count = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            signal_count = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            audit_count = con.execute("SELECT COUNT(*) FROM workflow_audit").fetchone()[0]
        counts = report(db)
        board = metrics(db)
        return {
            "mode": "SYNTHETIC_ARCHITECTURE_DEMO_ONLY",
            "built_on": now.isoformat(), "source_connections_made": 0,
            "crm_writes": "NO", "external_calls": 0, "messages_sent": 0,
            "company_count": company_count, "signal_count": signal_count,
            "first_created": a["new_company"],
            "repeat_new_company": a_repeat["new_company"],
            "repeat_new_signal": a_repeat["new_signal"],
            "second_signal_created": another["new_signal"],
            "workflow_audit_events": audit_count,
            "failure": failure, "recovered": recovered,
            "duplicate_replay": duplicate_replay,
            "existing_client_hold": existing_held["status"],
            "stop_list_hold": stopped_held["status"],
            "local_workflow": counts, "demo_metrics": board,
            "real_bitrix24": "NOT_CONNECTED",
            "manager_login_roles": "NOT_AUTHENTICATED_OUTSIDE_DEMO",
            "real_client_contacts": "NONE",
            "phone_email_bot_connectors": "NOT_CONNECTED",
            "actual_contracts_revenue_mrr": "NOT_VERIFIED",
        }


def render(result: dict) -> str:
    tiles = "".join(
        '<article class="tile"><strong>' + escape(title) + '</strong><p>' +
        escape(desc) + '</p></article>' for title, desc in CARDS
    )
    company = result["company_count"]
    signal = result["signal_count"]
    simulated = result["recovered"]["simulated_completed"]
    a = result["workflow_audit_events"]
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Сервис ИТ — демонстрация архитектуры</title>
<style>
:root{{--ink:#173052;--muted:#718097;--blue:#176eea;--bg:#f2f5fb;--border:#e2e8f2}}
*{{box-sizing:border-box}}body{{font:15px/1.5 Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}}
header{{padding:35px max(24px,calc((100vw - 1140px)/2));background:#122340;color:white}}
header h1{{font-size:30px;margin:0 0 6px}}header p{{color:#bdd0eb;margin:0;max-width:780px}}
main{{max-width:1140px;margin:auto;padding:24px}}
.notice{{border:1px solid #eccd85;background:#fff9e8;border-radius:14px;padding:16px 20px;margin:12px 0 22px;color:#77571e}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.card,.tile,.section{{background:white;border:1px solid var(--border);border-radius:15px;padding:20px}}
.card span{{display:block;font-size:12px;color:var(--muted)}}.card b{{font-size:31px}}
h2{{font-size:20px;margin:32px 0 14px}}
.flow{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.tile strong{{font-size:15px}}.tile p{{font-size:13px;color:var(--muted);margin:5px 0 0}}
.good{{color:#117c57;font-weight:700}}.warn{{color:#ab6418;font-weight:700}}
.table{{width:100%;border-collapse:collapse}}.table td{{padding:10px 12px;border-bottom:1px solid var(--border)}}
.table td:last-child{{text-align:right}}footer{{padding:25px 0;color:var(--muted);font-size:12px}}
@media(max-width:820px){{.flow{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><header>
<h1>Сервис ИТ · Архитектура MVP</h1>
<p>Сквозной демонстрационный сценарий: от компании и потребности до решения менеджера, безопасной очереди и аналитики</p>
</header><main>
<div class="notice"><strong>ДЕМОНСТРАЦИЯ.</strong> Все компании и сигналы синтетические. Битрикс24, АТС, боты и реальные источники не подключались. Никаких реальных сообщений и CRM-записей.</div>
<div class="grid"><div class="card"><span>Тестовые компании</span><b>{company}</b></div>
<div class="card"><span>Независимые сигналы</span><b>{signal}</b></div>
<div class="card"><span>Имитаций доставки</span><b>{simulated}</b></div>
<div class="card"><span>События аудита</span><b>{a}</b></div></div>
<h2>Как устроен конвейер</h2><div class="flow">{tiles}</div>
<h2>Сценарии приёмки</h2><section class="section"><table class="table">
<tr><td>Тот же ИНН и тот же сигнал</td><td class="good">Не создаётся дубль</td></tr>
<tr><td>Тот же ИНН, новый сигнал</td><td class="good">Сигнал сохраняется отдельно</td></tr>
<tr><td>CRM временно недоступна (симуляция)</td><td class="good">Операция сохранена и повторена</td></tr>
<tr><td>Повторная передача того же сигнала</td><td class="good">Не создаёт второй операции</td></tr>
<tr><td>Существующий клиент</td><td class="good">Передача остановлена</td></tr>
<tr><td>Появление компании в стоп-листе</td><td class="good">Следующая передача заблокирована</td></tr>
<tr><td>Синтетический Lead Score</td><td class="good">Формула и объяснение работают локально</td></tr>
<tr><td>Реальная запись в Bitrix24</td><td class="warn">Ожидает доступ и испытания</td></tr>
<tr><td>Телефония, мессенджеры, корпоративные роли</td><td class="warn">Ожидают корпоративные интеграции</td></tr>
<tr><td>Продажи и договоры</td><td class="warn">Не подтверждены</td></tr>
</table></section>
<footer>Сервис ИТ · Только локальный безопасный прототип · Generated {escape(result["built_on"])} · Никакой отправки сообщений</footer>
</main></body></html>"""


def main():
    import argparse
    p = argparse.ArgumentParser(description="Produce safe non-source architecture HTML")
    p.add_argument("--html", default="exports/architecture_showcase.html")
    p.add_argument("--json", default="exports/architecture_showcase.json")
    args = p.parse_args()
    root = (Path(__file__).resolve().parents[1] / "exports").resolve()
    html_path = Path(args.html).resolve()
    json_path = Path(args.json).resolve()
    if (html_path == json_path or html_path.suffix.lower() != ".html"
            or json_path.suffix.lower() != ".json"
            or not html_path.is_relative_to(root) or not json_path.is_relative_to(root)):
        raise SystemExit("Outputs must be separate .html/.json under exports")
    result = simulate()
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ARCH_SHOWCASE=CREATED_LOCAL_SYNTHETIC_ONLY")
    print("HTML=" + str(html_path))
    print("JSON=" + str(json_path))
    print("MANAGER_REVIEW_AND_RETRY=SIMULATED_PASS; CRM_WRITES=NO; REAL_LEADS=0")


if __name__ == "__main__":
    main()
