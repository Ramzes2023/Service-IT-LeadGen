"""Cloud operational watchdog for ServiceIT -> Bitrix24 GitHub Actions.

Only reads GitHub Actions run metadata. Incident reports go to Issue #221.
No CRM writes, no automatic retry of possibly completed CRM writes or tasks.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPO = "Ramzes2023/Service-IT-LeadGen"
API = "https://api.github.com/repos/" + REPO
ISSUE = 221
OWNER_MARKER = "SERVICEIT_CLOUD_WATCHDOG_RUN_READONLY_V1"
STATE_MARKER = "<!-- SERVICEIT_CLOUD_WATCHDOG_STATE_SHA256:"
BAD = {"failure", "cancelled", "timed_out", "action_required"}


@dataclass(frozen=True)
class Target:
    file: str
    label: str
    every: int
    stale: int
    since: str


# Corporate-gated formal nine-proof Score is intentionally excluded.
TARGETS = (
    Target("serviceit-bitrix-poll-fallback.yml", "Поиск новых карточек", 10, 120, "2026-11-04T00:00:00Z"),
    Target("serviceit-bitrix-all-new-batch-ai.yml", "AI обработка", 10, 150, "2026-11-04T09:00:00Z"),
    Target("serviceit-stage3-all-new-task-automation.yml", "Задачи менеджера", 10, 150, "2026-11-04T09:00:00Z"),
    Target("serviceit-stage4-prelim-score-live.yml", "Предварительный Score", 10, 150, "2026-11-05T00:00:00Z"),
    Target("serviceit-postcall-manager-approved-auto.yml", "После звонка", 20, 180, "2026-11-06T11:17:00Z"),
    Target("serviceit-live-new-card-reconciliation.yml", "Сверка AI/задач/Score", 60, 200, "2026-11-05T00:00:00Z"),
    Target("serviceit-finance-hybrid-live.yml", "Финансовые внутренние задачи", 30, 200, "2026-11-05T19:00:00Z"),
)

WATCHED_NAMES = frozenset({
    "ServiceIT Bitrix24 new-card polling fallback (no outgoing webhook)",
    "ServiceIT all-new 1062/21 AI batch catch-up (real CRM, no demo)",
    "ServiceIT stage3 auto internal review task for ALL future 1062/21 cards",
    "ServiceIT provisional Lead Score for every NEW 1062/21 card",
    "ServiceIT manager-confirmed call outcome and next action (CRM only)",
    "ServiceIT hourly real new-card AI / task / Score readback (READ ONLY)",
    "ServiceIT hybrid finance auto / manager fallback for NEW won LeadGen deals",
})


class WatchdogBlocked(ValueError):
    pass


def parsed(iso):
    if not isinstance(iso, str):
        raise WatchdogBlocked("TIME_MISSING")
    try:
        value = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        raise WatchdogBlocked("TIME_INVALID") from None
    if value.tzinfo is None:
        raise WatchdogBlocked("TIMEZONE_INVALID")
    return value.astimezone(timezone.utc)


def assess(target, records, *, now):
    now = now.astimezone(timezone.utc)
    activation = parsed(target.since)
    if now < activation or not isinstance(records, list):
        raise WatchdogBlocked("ACTIVATION_OR_RECORDS_INVALID")
    scheduled = []
    for r in records:
        if not isinstance(r, dict):
            raise WatchdogBlocked("RUN_NOT_OBJECT")
        if r.get("event") != "schedule":
            continue
        stamp = parsed(r.get("created_at"))
        if stamp > now + timedelta(minutes=5):
            raise WatchdogBlocked("FUTURE_RUN")
        if stamp >= activation:
            scheduled.append((stamp, r))
    scheduled.sort(key=lambda pair: pair[0], reverse=True)
    latest = scheduled[0] if scheduled else None
    prev_success = next((x for x in scheduled if x[1].get("conclusion") == "success"), None)
    lookback = min(timedelta(hours=6), now - activation)
    observed = sum(1 for stamp, _ in scheduled if stamp >= now - lookback)
    expected = int(lookback.total_seconds() // (target.every * 60))
    age = int((now - latest[0]).total_seconds() // 60) if latest else None
    gaps = [int((scheduled[i][0] - scheduled[i + 1][0]).total_seconds() // 60)
            for i in range(len(scheduled) - 1)]
    if not latest:
        status = "WARMUP" if now - activation < timedelta(hours=2) else "NO_SCHEDULE_RUN"
    elif latest[1].get("conclusion") in BAD:
        status = "SCHEDULE_FAILED"
    elif age > target.stale:
        status = "STALE"
    elif latest[1].get("conclusion") == "skipped":
        status = "SCHEDULE_SKIPPED"
    elif latest[1].get("status") != "completed":
        status = "RUNNING"
    elif lookback >= timedelta(hours=2) and expected >= 6 and observed * 4 < expected:
        status = "SPARSE"
    else:
        status = "OK"
    run_id = latest[1].get("id") if latest else None
    if run_id is not None and (type(run_id) is not int or run_id <= 0):
        raise WatchdogBlocked("RUN_ID_UNVERIFIED")
    return {
        "file": target.file, "label": target.label, "status": status,
        "last_run": latest[0].isoformat() if latest else None,
        "last_success": prev_success[0].isoformat() if prev_success else None,
        "run_id": run_id, "age_minutes": age,
        "seen_6h": observed, "expected_6h": expected,
        "maximum_gap_minutes": max(gaps, default=0),
    }


def signature(rows):
    key = sorted((r["file"], r["status"],
                  r["run_id"] if r["status"] == "SCHEDULE_FAILED" else None)
                 for r in rows)
    return sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()


def report(rows, mark, *, now):
    lines = [
        "# Сервис ИТ — состояние облачной автоматизации",
        "",
        "Автоматическая сводка только по метаданным GitHub Actions. "
        "CRM, контакты, звонки, клиенты и секреты не читаются.",
        "",
        "| Процесс | Статус | Последний cron | За 6 часов, было / ожидалось | Макс. интервал |",
        "|---|---|---|---:|---:|",
    ]
    for r in rows:
        href = ("https://github.com/" + REPO + "/actions/runs/" + str(r["run_id"])
                if r["run_id"] else None)
        when = r["last_run"] or "нет"
        if href:
            when = "[" + when + "](" + href + ")"
        lines.append(
            "| " + r["label"] + " | **" + r["status"] + "** | " + when
            + " | " + str(r["seen_6h"]) + " / " + str(r["expected_6h"])
            + " | " + str(r["maximum_gap_minutes"]) + " мин |"
        )
    lines += [
        "",
        "Состояние на " + now.isoformat() + ". Число плановых интервалов — "
        "ориентировочное, отсутствие события в API не доказывает, что именно оно потеряно.",
        "",
        "STALE = давно нет зарегистрированного cron, SPARSE = меньше 25% "
        "ожидаемых запусков, SCHEDULE_FAILED = последний cron завершился ошибкой, "
        "WARMUP = недавно включённый процесс.",
        "",
        "**Действия при ошибке:** проверить GitHub run и журнал идемпотентности, "
        "сверить точную CRM-карточку. НЕЛЬЗЯ вслепую повторять Bitrix "
        "crm.item.update, tasks.task.add, crm.status.add и отменять/сбрасывать "
        "зашифрованные одноразовые квитанции. Watchdog не вызывает Bitrix REST.",
        "",
        "**Оповещение:** при смене состояния Issue обновляется и отправляется "
        "одно сообщение назначенному владельцу; email/push зависит от настроек GitHub.",
        "",
        "**Ограничение:** watchdog также работает в GitHub; при полном отказе "
        "GitHub Actions независимый внешний heartbeat необходим для гарантированного SLA.",
        "",
        STATE_MARKER + mark + " -->",
    ]
    return "\n".join(lines)


def notice(rows):
    bad = [r for r in rows if r["status"] not in ("OK", "WARMUP", "RUNNING")]
    if not bad:
        return "@Ramzes2023 ✅ Все отслеживаемые расписания сейчас в допустимых пределах."
    names = ", ".join(r["label"] + " (" + r["status"] + ")" for r in bad)
    return ("@Ramzes2023 ⚠️ Сервис ИТ: обнаружены сбои/задержки: " + names
            + ". Подробности в таблице Issue. Записей в Битрикс24 не выполнялось.")


def authorize(env, event):
    if env.get("GITHUB_REPOSITORY") != REPO or env.get("GITHUB_REF") != "refs/heads/main":
        raise WatchdogBlocked("REPOSITORY_NOT_ALLOWED")
    kind = env.get("GITHUB_EVENT_NAME")
    if kind == "schedule":
        return
    if kind == "workflow_dispatch" and env.get("GITHUB_ACTOR") == "Ramzes2023":
        return
    if kind == "issue_comment":
        if (env.get("GITHUB_ACTOR") == "Ramzes2023"
                and event.get("action") == "created"
                and (event.get("repository") or {}).get("full_name") == REPO
                and (event.get("issue") or {}).get("number") == ISSUE
                and not (event.get("issue") or {}).get("pull_request")
                and (event.get("comment") or {}).get("body") == OWNER_MARKER
                and ((event.get("comment") or {}).get("user") or {}).get("login")
                == "Ramzes2023"):
            return
        raise WatchdogBlocked("OWNER_COMMENT_NOT_APPROVED")
    if kind == "workflow_run":
        r = event.get("workflow_run") or {}
        if (event.get("action") == "completed"
                and r.get("event") in ("schedule", "repository_dispatch", "workflow_dispatch")
                and r.get("name") in WATCHED_NAMES
                and r.get("head_branch") == "main"
                and (r.get("head_repository") or {}).get("full_name") == REPO):
            return
        raise WatchdogBlocked("WORKFLOW_RUN_UNTRUSTED")
    raise WatchdogBlocked("EVENT_NOT_APPROVED")


def github(token, method, url, payload=None):
    if not isinstance(token, str) or len(token) < 12 or not url.startswith(API + "/"):
        raise WatchdogBlocked("GITHUB_API_SCOPE_OR_TOKEN")
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=18) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise WatchdogBlocked("GITHUB_API_OVERSIZED")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise WatchdogBlocked("GITHUB_API_SHAPE")
            return value
        except HTTPError as exc:
            if attempt + 1 < attempts and exc.code in (429, 500, 502, 503, 504):
                time.sleep(min(4, 2 ** attempt))
                continue
            raise WatchdogBlocked("GITHUB_API_HTTP_" + str(exc.code)) from None
        except (URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 < attempts:
                time.sleep(min(4, 2 ** attempt))
                continue
            raise WatchdogBlocked("GITHUB_API_TRANSPORT_UNVERIFIED") from None
    raise WatchdogBlocked("GITHUB_API_UNVERIFIED")


def collect(token, now):
    rows = []
    for t in TARGETS:
        url = API + "/actions/workflows/" + quote(t.file, safe="") + "/runs?event=schedule&per_page=100"
        data = github(token, "GET", url)
        if not isinstance(data.get("workflow_runs"), list):
            raise WatchdogBlocked("WORKFLOW_RUNS_UNVERIFIED")
        rows.append(assess(t, data["workflow_runs"], now=now))
    return rows


def publish(token, rows, now):
    url = API + "/issues/" + str(ISSUE)
    issue = github(token, "GET", url)
    if (str(issue.get("number")) != str(ISSUE)
            or not isinstance(issue.get("body"), str)
            or issue.get("state") not in ("open", "closed")):
        raise WatchdogBlocked("INCIDENT_ISSUE_UNVERIFIED")
    mark = signature(rows)
    existing = re.search(re.escape(STATE_MARKER) + r"([0-9a-f]{64}) -->", issue["body"])
    previous = existing.group(1) if existing else None
    degraded = any(r["status"] not in ("OK", "WARMUP", "RUNNING") for r in rows)
    new_state = "open" if degraded else "closed"
    if previous == mark and issue["state"] == new_state:
        return {"status": "WATCHDOG_UNCHANGED", "incident_open": degraded, "issue_writes": 0}
    # No retries for PATCH/POST: ambiguous GitHub writes are not repeated.
    github(token, "PATCH", url, {"body": report(rows, mark, now=now), "state": new_state})
    github(token, "POST", url + "/comments", {"body": notice(rows)})
    return {"status": "WATCHDOG_INCIDENT_UPDATED", "incident_open": degraded,
            "issue_writes": 2}


def main():
    env = dict(os.environ)
    try:
        event = {}
        if env.get("GITHUB_EVENT_NAME") != "schedule":
            with open(env.get("GITHUB_EVENT_PATH", ""), encoding="utf-8") as f:
                event = json.load(f)
        authorize(env, event)
        now = datetime.now(timezone.utc)
        token = env.get("GH_WATCHDOG_TOKEN", "")
        rows = collect(token, now)
        out = publish(token, rows, now)
        stats = {}
        for row in rows:
            stats[row["status"]] = stats.get(row["status"], 0) + 1
        out.update({"status_counts": stats, "monitored_workflows": len(rows),
                    "crm_writes": 0, "workflow_retries": 0})
        print(json.dumps(out, sort_keys=True), flush=True)
        return 0
    except (WatchdogBlocked, KeyError, OSError, ValueError):
        print(json.dumps({"status": "WATCHDOG_FAIL_CLOSED",
                          "crm_writes": 0, "workflow_retries": 0}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
