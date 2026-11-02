"""Owner-operated scheduled Bitrix24 1062/21 card auto-fill, with no VPS.

Never touches historical cards 3/7, sources, contacts, stages, tasks or layouts.
Pending items without independently verified approvals get *nine honest review
fields* (AI not run; no Score). Verified, time-bound approved items are handed
to the existing universal GigaChat + deterministic Score worker. Neither the
scheduler nor this module forges verification or exports customer data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .ai import AIProvider, config_from_env
from .serviceit_enrichment_store import Store
from .serviceit_enrichment_worker import (
    AI_FIELDS, FIELDS, PREFIX, Rest, Worker, approval_check,
    empty_analysis, fingerprint, load_approval, normalized, poll,
    schema_limits, validate_identity,
)
from .serviceit_bitrix_event_queue_readonly_worker import classify_item

FIRST_ID = 8
TERMINAL = {"HOLD", "VERIFIED", "UNCERTAIN", "NEEDS_REVIEW"}
SKIP = {"synthetic", "out_of_scope"}


class AutoFillBlocked(ValueError):
    pass


def _must(condition, reason):
    if not condition:
        raise AutoFillBlocked(reason)


def preliminary_fields(item):
    """Fixed, source-neutral review report; never misrepresent AI or qualification."""
    _must(isinstance(item, dict), "ITEM_REQUIRED")
    # No untrusted title, URL, personal data or raw company fields copied into
    # generated prose or transmitted to an LLM.
    status = "NOT_RUN_SOURCE_UNVERIFIED"
    summary = ("Карточка автоматически зарегистрирована для проверки. "
               "Подтверждённой потребности и разрешения контакта пока нет. "
               "GigaChat не вызывался, Lead Score не рассчитывался.")
    quote = "Подтверждённая дословная цитата из первоисточника отсутствует."
    hypothesis = ("Направление услуг и возможная потребность пока не подтверждены. "
                  "Это предварительная запись, не AI-заключение.")
    limitations = ("Не подтверждены первоисточник, дата, компания/ИНН, ФНС, "
                   "наличие действующего клиента, дубли во всей CRM, "
                   "стоп-лист, правовое основание и допуск менеджера.")
    recommendation = "Проверить применимость услуг «Сервис ИТ» по первоисточнику."
    action = ("Менеджеру проверить первоисточник и заказчика; после независимых "
              "проверок разрешить обогащение GigaChat. Не контактировать автоматически.")
    checklist = ("1. Сверить оригинальное объявление, дату и актуальность.\n"
                 "2. Проверить ИНН/ФНС, регион и существующего клиента.\n"
                 "3. Проверить CRM-дубли, стоп-лист и разрешение контакта.\n"
                 "4. При допуске — запустить проверенную оценку и GigaChat.\n"
                 "5. Утвердить следующее действие менеджером.")
    full = ("СЕРВИС ИТ — АВТОМАТИЧЕСКАЯ ПРЕДВАРИТЕЛЬНАЯ КАРТОЧКА\n"
            "СТАТУС: HOLD / ПРОВЕРКА ИСТОЧНИКА\n"
            "GigaChat: NOT_RUN (не отправлялись корпоративные данные)\n"
            "Lead Score: НЕ РАССЧИТАН; цветной приоритет не назначен.\n"
            "Контакт с компанией: НЕ РАЗРЕШЁН\n\n"
            "Краткий вывод\n" + summary + "\n\n"
            "Доказательства\n" + quote + "\n\n"
            "Гипотеза\n" + hypothesis + "\n\n"
            "Ограничения\n" + limitations + "\n\n"
            "Рекомендуемое направление\n" + recommendation + "\n\n"
            "Памятка менеджеру\n" + checklist + "\n\n"
            "Следующий шаг\n" + action)
    values = (status, summary, quote, hypothesis, limitations,
              recommendation, action, checklist, full)
    fields = {PREFIX + name: value for name, value in zip((
        "AI_STATUS", "AI_SUMMARY", "AI_QUOTE", "AI_HYPOTHESIS",
        "AI_LIMITATIONS", "AI_RECOMMENDATION", "AI_NEXT_ACTION",
        "AI_MANAGER_BRIEF", "AI_FULL_REPORT"), values)}
    _must(set(fields) == AI_FIELDS and all(type(x) is str and 0 < len(x) <= 12000
                                          for x in fields.values()),
          "REVIEW_FIELDS_INVALID")
    return fields


def preliminary_once(store, rest, requested_id):
    """One fenced CRM update; ambiguous result never retried."""
    if type(requested_id) is not int or requested_id < FIRST_ID:
        raise AutoFillBlocked("HISTORICAL_ITEMS_EXCLUDED")
    job = store.claim(requested_id)
    if job is None:
        return "IDLE"
    item_id, owner = job
    posted = False
    write_reserved = False
    state, reason = "HOLD", "INSUFFICIENT_VERIFIED_EVIDENCE"
    try:
        item = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": item_id, "useOriginalUfNames": "Y"})["item"])
        validate_identity(item, item_id)
        decision = classify_item(item, requested_id=item_id)
        _must(decision.get("source_kind") not in SKIP, "SYNTHETIC_OR_OUT_OF_SCOPE")
        empty_analysis(item)
        fields = preliminary_fields(item)
        limits = schema_limits(rest)
        _must(all(len(value) <= limits[name] for name, value in fields.items()),
              "FIELD_LENGTH_BLOCKED")
        current = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": item_id, "useOriginalUfNames": "Y"})["item"])
        validate_identity(current, item_id)
        _must(fingerprint(current) == fingerprint(item), "MANAGER_EDIT_CONFLICT")
        store.reserve(item_id, owner, "write", expected=fields,
                      before_hash=fingerprint(item))
        write_reserved = True
        posted = True
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": item_id,
            "useOriginalUfNames": "Y", "fields": fields})
        after = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": item_id, "useOriginalUfNames": "Y"})["item"])
        validate_identity(after, item_id)
        _must(all(after.get(k) == v for k, v in fields.items()),
              "AI_FIELD_READBACK_UNCERTAIN")
        _must(all(after.get(k) == v for k, v in item.items()
                  if k not in FIELDS and k not in (
                      "updatedTime", "updatedBy", "lastActivityTime", "lastActivityBy")),
              "NON_TARGET_FIELD_CHANGED")
        reason = "NINE_REVIEW_FIELDS_EXACT_READBACK"
    except Exception:
        # A reservation or HTTP timeout could have caused a write. Preserve
        # uncertainty; don't re-submit or print remote/customer data.
        state = "UNCERTAIN" if (posted or write_reserved) else "HOLD"
        reason = "CRM_WRITE_OR_READBACK_UNVERIFIED" if state == "UNCERTAIN" else "REVIEW_NOT_APPLIED"
    if not store.finish(item_id, owner, state, reason):
        return "LEASE_LOST"
    return "HOLD_9_WRITTEN" if reason == "NINE_REVIEW_FIELDS_EXACT_READBACK" else state


def run_cycle(store, rest, *, approvals_dir, model=None, max_items=10):
    """Poll new IDs; preserve durable state; no retry of ambiguous operations."""
    _must(type(max_items) is int and 1 <= max_items <= 10, "BATCH_LIMIT")
    new = poll(store, rest, FIRST_ID)
    handled = []
    # Poll enqueues exact new IDs. No history is imported into this ledger.
    with store.db() as db:
        rows = db.execute(
            "SELECT item_id FROM new_cards WHERE entity_type_id=1062 "
            "AND status='PENDING_SOURCE_CHECK' AND item_id>=8 "
            "ORDER BY item_id LIMIT ?", (max_items,)).fetchall()
    worker = Worker(store, rest,
                    lambda number: load_approval(approvals_dir, number),
                    model, enabled=True)
    for row in rows:
        item_id = int(row[0])
        approved = load_approval(approvals_dir, item_id)
        if approved is None:
            status = preliminary_once(store, rest, item_id)
        else:
            status = worker.once(item_id)
        handled.append({"id": item_id, "status": status})
    return {"status": "SERVICEIT_LOCAL_AUTOFILL_CYCLE_COMPLETE",
            "new_items_discovered": new,
            "processed": handled,
            "historical_cards_touched": False}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not args.once or os.environ.get("SERVICEIT_WINDOWS_AUTOFILL_ENABLED") != "YES_1062_21":
        print(json.dumps({"status": "BLOCKED_OFFLINE", "crm_writes": 0}))
        return 2
    secret = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    directory = Path(os.environ.get("SERVICEIT_AUTOFILL_STATE_DIR", ""))
    approvals = Path(os.environ.get("SERVICEIT_APPROVAL_DIR", ""))
    if (not secret or not directory.is_absolute() or
            not approvals.is_absolute() or not approvals.is_dir()):
        print(json.dumps({"status": "CONFIGURATION_BLOCKED", "crm_writes": 0}))
        return 2
    provider = None
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
            rest = Rest(secret, client)
            store = Store(directory / "new-card-events.sqlite3")
            model = None
            if os.environ.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES":
                config = config_from_env()
                _must(config.provider == "gigachat", "MODEL_PROVIDER_INVALID")
                provider = AIProvider(config, retry_unauthorized=False)
                model = provider.classify
            result = run_cycle(store, rest, approvals_dir=approvals, model=model)
            # Only card IDs and fixed worker statuses, never customer or model text.
            print(json.dumps(result, ensure_ascii=False))
            return 0
    except Exception:
        print(json.dumps({"status": "AUTOFILL_BLOCKED_REVIEW_LOG", "secret_exposed": False}))
        return 2
    finally:
        if provider:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
