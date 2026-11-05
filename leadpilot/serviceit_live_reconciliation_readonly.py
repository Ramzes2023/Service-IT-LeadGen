"""Hourly production read-only reconciliation for all NEW ServiceIT 1062/21 cards.

The existing batch AI, Stage3 task and provisional Score workers ALREADY run
automatically. This independent observer verifies actual readbacks after those
workers, without mutating any card, creating tasks, contacting customers,
calculating unapproved payroll, or importing sources.

No customer values, IDs, financial figures, email, webhook or task contents in
logs or GitHub Step Summary. Missing visible objects do NOT prove global CRM
absence; unavailable API access is UNKNOWN, never counted as zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, discover,
)
from .serviceit_stage34_signed_readonly_sidecar import (
    owner_type, visible_tasks, positive,
)
from .serviceit_enrichment_worker import normalized, validate_identity
from .serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
from .serviceit_kanban_presentation import (
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD,
)
from .serviceit_postcall_funnel_scope_readonly import probe

REPOSITORY = "Ramzes2023/Service-IT-LeadGen"
PORTAL = "https://serviseit.bitrix24.ru/rest/"
OWNER = "Ramzes2023"
ISSUE = 148
ONCE = "SERVICEIT_RECONCILE_ALL_NEW_READONLY_ONCE_V1"
MAX_INSPECT = 12


class ReconciliationBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ReconciliationBlocked(code)


def allow_context(env):
    need(env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main",
         "RECONCILE_REPOSITORY_CONTEXT_MISSING")
    event = env.get("GITHUB_EVENT_NAME")
    if event == "schedule":
        return
    need(event == "issue_comment"
         and env.get("GITHUB_ACTOR") == OWNER,
         "RECONCILE_OWNER_ACTION_REQUIRED")
    path = Path(env.get("GITHUB_EVENT_PATH") or "/nonexistent")
    need(path.is_file() and 0 < path.stat().st_size <= 100_000,
         "RECONCILE_EVENT_NOT_VERIFIABLE")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, TypeError):
        raise ReconciliationBlocked("RECONCILE_EVENT_MALFORMED") from None
    need(type(record) is dict and record.get("action") == "created"
         and (record.get("repository") or {}).get("full_name") == REPOSITORY
         and (record.get("issue") or {}).get("number") == ISSUE
         and not (record.get("issue") or {}).get("pull_request")
         and (record.get("sender") or {}).get("login") == OWNER
         and (record.get("comment") or {}).get("body") == ONCE
         and ((record.get("comment") or {}).get("user") or {}).get("login") == OWNER,
         "RECONCILE_OWNER_MARKER_INVALID")


def detail(client, base, card):
    card = positive(card)
    need(card >= 8 and card not in (3, 7),
         "RECONCILE_HISTORICAL_CARD_FORBIDDEN")
    response = client.post(
        base + "/crm.item.get.json",
        json={"entityTypeId": 1062, "id": card,
              "useOriginalUfNames": "Y"},
        timeout=20, follow_redirects=False,
    )
    need(response.status_code == 200 and 0 < len(response.content) <= 500_000,
         "RECONCILE_DETAIL_UNAVAILABLE")
    try:
        raw = response.json()
    except (TypeError, ValueError):
        raise ReconciliationBlocked("RECONCILE_DETAIL_INVALID_JSON") from None
    need(type(raw) is dict and "error" not in raw
         and type(raw.get("result")) is dict
         and type(raw["result"].get("item")) is dict,
         "RECONCILE_DETAIL_UNTRUSTED")
    item = normalized(raw["result"]["item"])
    validate_identity(item, card)
    return item


def _present(value):
    return type(value) is str and bool(value.strip())


def inspect_card(item, *, task_count=None, task_read_ok=False):
    """Only aggregate evidence: task visibility and field presence, NO values."""
    need(type(item) is dict, "RECONCILE_CARD_NOT_DICT")
    ai = sum(_present(item.get(key)) for key in AI_FIELDS)
    need(0 <= ai <= 9, "RECONCILE_AI_COUNT_INVALID")
    version = item.get(VERSION_FIELD)
    label = item.get(BADGE_FIELD)
    score = item.get(SCORE_FIELD)
    # A visible score is NOT a formally signed nine-gate qualification.
    score_observed = (
        type(score) in (str, int) and str(score).isdigit()
        and 0 <= int(score) <= 100
        and _present(version) and _present(label)
    )
    assignee = item.get("assignedById")
    needs_owner = assignee in (0, "0", "", None)
    if task_read_ok:
        need(type(task_count) is int and task_count >= 0,
             "RECONCILE_TASK_COUNT_UNVERIFIED")
    return {
        "ai": ai,
        "score_observed": score_observed,
        "no_assignee": needs_owner,
        "task_state": (
            "VISIBLE_LINKED_TASK" if task_read_ok and task_count > 0
            else "NO_VISIBLE_TASK_NOT_GLOBAL_ABSENCE"
            if task_read_ok else "TASK_VISIBILITY_UNKNOWN"
        ),
    }


def check(client, env, *, now=None):
    now = now or datetime.now(timezone.utc)
    need(isinstance(now, datetime) and now.tzinfo is not None,
         "RECONCILE_UTC_CLOCK_REQUIRED")
    now = now.astimezone(timezone.utc)
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(PORTAL), "RECONCILE_WRONG_PORTAL")
    crm_read = lambda method, args: list_only(client, base, method, args)
    calibrate_known_card(crm_read)
    cards = discover(crm_read, after_activation(env, now), now)

    out = {
        "status": "SERVICEIT_ALL_NEW_REAL_READBACK_RECONCILED",
        "scope": "NEW_1062_21_ONLY",
        "new_cards_visible": len(cards),
        "inspected": 0,
        "pending_next_run": max(0, len(cards)-MAX_INSPECT),
        "detail_unverified": 0,
        "ai_nine_complete": 0,
        "ai_partial": 0,
        "ai_absent": 0,
        "provisional_or_other_score_fields_visible": 0,
        "formal_verified_score_concluded": False,
        "assignee_missing": 0,
        "linked_task_visible": 0,
        "no_linked_task_visible_not_global_absence": 0,
        "task_visibility_unknown": 0,
        "automatic_profit_payouts_authorized": False,
        "outbound_contacts": 0,
        "crm_writes": 0,
        "tasks_created": 0,
        "old_cards_modified": 0,
        "source_ingestion": 0,
    }

    # Absence of task-list permission != no task. Even an empty successful
    # listing cannot prove global visibility for a non-admin Bitrix webhook.
    task_prefix = None
    try:
        task_prefix = owner_type(client, base)
    except Exception:
        pass

    for card in cards[:MAX_INSPECT]:
        try:
            item = detail(client,base,card)
        except Exception:
            out["detail_unverified"] += 1
            continue
        count, task_ok = None, False
        if task_prefix is not None:
            try:
                count = visible_tasks(client, base, task_prefix+"_"+str(card))
                task_ok = True
            except Exception:
                pass
        result = inspect_card(item,task_count=count,task_read_ok=task_ok)
        out["inspected"] += 1
        if result["ai"] == 9:
            out["ai_nine_complete"] += 1
        elif result["ai"] == 0:
            out["ai_absent"] += 1
        else:
            out["ai_partial"] += 1
        if result["score_observed"]:
            out["provisional_or_other_score_fields_visible"] += 1
        if result["no_assignee"]:
            out["assignee_missing"] += 1
        if result["task_state"] == "VISIBLE_LINKED_TASK":
            out["linked_task_visible"] += 1
        elif result["task_state"] == "NO_VISIBLE_TASK_NOT_GLOBAL_ABSENCE":
            out["no_linked_task_visible_not_global_absence"] += 1
        else:
            out["task_visibility_unknown"] += 1

    # Corporate scope is reported independently and may be partially visible.
    try:
        scope = probe(client, env["SERVICEIT_BITRIX_WEBHOOK"])
        out["postcall_manager_fields_ready"] = scope["manager_fields_ready"]
        out["separate_sales_funnel_visible_to_webhook"] = scope[
            "separate_sales_funnel_visible"]
        out["commercial_funnel_scope"] = scope[
            "commercial_funnel_found_or_absent"]
    except Exception:
        out["postcall_manager_fields_ready"] = None
        out["separate_sales_funnel_visible_to_webhook"] = None
        out["commercial_funnel_scope"] = "UNVERIFIED"

    if not cards:
        out["status"] = "SERVICEIT_RECONCILE_NO_REAL_NEW_CARDS"
    elif out["detail_unverified"] or out["pending_next_run"]:
        out["status"] = "SERVICEIT_RECONCILE_PARTIAL_VISIBILITY"
    elif out["ai_partial"] or out["ai_absent"] or out["task_visibility_unknown"]:
        out["status"] = "SERVICEIT_RECONCILE_NEEDS_REVIEW"
    return out


def main():
    env = dict(os.environ)
    result = {"status": "SERVICEIT_RECONCILE_BLOCKED",
              "crm_writes": 0,"tasks_created": 0}
    try:
        allow_context(env)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = check(client, env)
    except Exception:
        print(json.dumps(result,sort_keys=True),flush=True)
        return 2
    print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        # Aggregate only: no card identifiers, contacts, customer data or money.
        text = (
            "## Сервис ИТ — автоматический контроль реальных новых карточек\n\n"
            f"- Найдено новых 1062/21: {result['new_cards_visible']}; "
            f"проверено: {result['inspected']}; "
            f"ожидают следующего запуска: {result['pending_next_run']}.\n"
            f"- Девять AI-полей: {result['ai_nine_complete']}; "
            f"частично: {result['ai_partial']}; "
            f"отсутствуют: {result['ai_absent']}; "
            f"чтение не подтверждено: {result['detail_unverified']}.\n"
            f"- Внешне видимые связанные задачи: {result['linked_task_visible']}; "
            f"нет видимых (НЕ глобальное отсутствие): "
            f"{result['no_linked_task_visible_not_global_absence']}; "
            f"проверка задач недоступна: {result['task_visibility_unknown']}.\n"
            f"- Score-поля видимы, но формальная квалификация этим НЕ доказана: "
            f"{result['provisional_or_other_score_fields_visible']}.\n"
            f"- Отдельная воронка продаж: {result['commercial_funnel_scope']}.\n"
            "- Записей CRM / создания задач / контактов / выплат: 0.\n"
        )
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
