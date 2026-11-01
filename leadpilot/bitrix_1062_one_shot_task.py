"""Exactly one human-confirmed synthetic linked Bitrix24 task for DEMO 1062/#3.

Creates NO CRM cards, contacts or outreach. A real task to an internal employee
may trigger a notification. Every ambiguous POST stays UNCERTAIN in a durable
local SQLite ledger; it is never replayed automatically.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_task_dedupe_readonly import (
    CRM_BINDING, TASK_TITLE, discover_demo_manager_task,
    TaskDiscoveryBlocked, _positive_id,
)

CONFIRM = "AUTHORIZE_ONE_SERVICEIT_DEMO_TASK_1062_3"
TASK_KEY = "serviceit-demo-1062-item3-linkedtask-v1"
DESCRIPTION = (
    "ДЕМОНСТРАЦИЯ ДЛЯ МИХАИЛА. ЭТО НЕ РЕАЛЬНЫЙ ЛИД.\n"
    "НЕ ЗВОНИТЬ КЛИЕНТУ. НЕ ПИСАТЬ КЛИЕНТУ. НЕ ОТПРАВЛЯТЬ КП.\n"
    "Учебный сигнал DEMO-ITREQ-001: возможная потребность в 1С:Розница, "
    "кассовом оборудовании и интеграции для сети из трёх магазинов. "
    "Оценка 86/100 учебная, не вероятность покупки. "
    "Проверить источник, потребность, наличие ЛПР и допустимость контакта. "
    "Если появится подтверждённый настоящий запрос, отдельно согласовать следующий шаг.\n"
    "Карточка: https://serviseit.bitrix24.ru/crm/type/1062/details/3/"
)


class OneShotTaskBlocked(ValueError):
    """A safe operation stopped without claiming creation."""


def _stop(code: str) -> None:
    raise OneShotTaskBlocked(code)


def _read_response(client: httpx.Client, url: str, params: dict) -> dict:
    try:
        r = client.post(url, json=params, timeout=18.0, follow_redirects=False)
    except (httpx.HTTPError, ValueError, TypeError):
        _stop("TASK_REST_TRANSPORT_OR_TIMEOUT")
    if r.status_code != 200 or len(r.content) > 256_000:
        _stop("TASK_REST_HTTP_OR_SIZE_BLOCK")
    try:
        body = r.json()
    except (ValueError, TypeError):
        _stop("TASK_REST_JSON_INVALID")
    if not isinstance(body, dict) or "error" in body or not isinstance(body.get("result"), dict):
        _stop("TASK_REST_RESPONSE_INVALID")
    return body["result"]


def _ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    con.execute("PRAGMA synchronous=FULL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS one_demo_task ("
        "idempotency_key TEXT PRIMARY KEY,"
        "state TEXT NOT NULL,"
        "responsible_id INTEGER NOT NULL,"
        "remote_task_id INTEGER)"
    )
    return con


def _reserve(con: sqlite3.Connection, responsible_id: int) -> None:
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state FROM one_demo_task WHERE idempotency_key=?", (TASK_KEY,)
        ).fetchone()
        if row is not None:
            con.execute("ROLLBACK")
            _stop("LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY")
        con.execute(
            "INSERT INTO one_demo_task(idempotency_key,state,responsible_id) "
            "VALUES(?,?,?)", (TASK_KEY, "UNCERTAIN_POST_RESERVED", responsible_id),
        )
        con.execute("COMMIT")
    except sqlite3.Error:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        _stop("LOCAL_LEDGER_UNAVAILABLE")


def _mark(con: sqlite3.Connection, state: str, task_id: int | None = None) -> None:
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE one_demo_task SET state=?,remote_task_id=? WHERE idempotency_key=?",
            (state, task_id, TASK_KEY),
        )
        con.execute("COMMIT")
    except sqlite3.Error:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        _stop("LOCAL_LEDGER_POST_STATUS_UNCERTAIN")


def execute_one_demo_task(
    webhook: str, *,
    responsible_id: int,
    local_ledger: str | Path,
    authorization: str,
    client: httpx.Client | None = None,
) -> dict:
    """Perform one explicitly approved internal demo task add with readback.

    The responsible id must have been independently checked in the corporate
    employee profile by the operator. It is NOT inferred to be Mikhail.
    No local/remote retry after an uncertain add.
    """
    if authorization != CONFIRM:
        _stop("EXPLICIT_ONE_TASK_CONFIRMATION_REQUIRED")
    try:
        rid = _positive_id(responsible_id)
    except TaskDiscoveryBlocked:
        _stop("RESPONSIBLE_ID_INVALID")
    if rid > 999_999_999:
        _stop("RESPONSIBLE_ID_OUT_OF_RANGE")
    base = validate_webhook(webhook)
    owner = client is None
    if owner:
        client = httpx.Client(timeout=18.0, follow_redirects=False, trust_env=False)
    try:
        try:
            check = discover_demo_manager_task(webhook, client=client)
        except TaskDiscoveryBlocked as exc:
            # Only project-defined constants are allowed out; do not print
            # arbitrary REST error descriptions or webhook URLs.
            known_preflight = {
                "EXACT_DEMO_METADATA_NOT_READY", "TASK_LIST_UNAVAILABLE",
                "TASK_LIST_HTTP_400", "TASK_LIST_HTTP_401",
                "TASK_LIST_HTTP_403", "TASK_LIST_HTTP_429",
                "TASK_LIST_HTTP_OTHER", "TASK_LIST_RESPONSE_TOO_LARGE",
                "TASK_LIST_API_ERROR", "TASK_LIST_MALFORMED",
                "TASK_LIST_SCHEMA_CHANGED", "TASK_ID_INVALID",
                "TASK_PAGINATION_DUPLICATE", "TASK_TITLE_MISSING",
                "CRM_TASK_LINK_UNEXPECTED_SHAPE",
                "TASK_TITLE_FILTER_NOT_EXACT", "TASK_CRM_FILTER_IGNORED",
                "TASK_PAGINATION_UNVERIFIED", "TASK_RESULT_WINDOW_TOO_LARGE",
                "UNBOUNDED_TASK_SEARCH_FORBIDDEN",
            }
            code = str(exc)
            _stop("REMOTE_PREFLIGHT_" + (code if code in known_preflight else "BLOCKED_UNSPECIFIED"))
        except (ValueError, httpx.HTTPError):
            _stop("REMOTE_DEMO_TASK_PREFLIGHT_BLOCKED")
        if check["status"] != "NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF":
            _stop("REMOTE_VISIBLE_DEMO_TASK_ALREADY_EXISTS")
        with closing(_ledger(Path(local_ledger))) as con:
            _reserve(con, rid)  # hard durable fence BEFORE the first POST
            # Exactly one tasks.task.add, never retried even on HTTP errors.
            result = _read_response(
                client, base + "/tasks.task.add.json",
                {"fields": {
                    "TITLE": TASK_TITLE,
                    "DESCRIPTION": DESCRIPTION,
                    "RESPONSIBLE_ID": rid,
                    "UF_CRM_TASK": [CRM_BINDING],
                }},
            )
            task = result.get("task")
            if not isinstance(task, dict):
                _stop("TASK_POST_ACK_UNCERTAIN")
            try:
                task_id = _positive_id(task.get("id"))
            except TaskDiscoveryBlocked:
                _stop("TASK_POST_ID_UNCERTAIN")
            _mark(con, "POST_ACK_NEEDS_GET", task_id)
            result = _read_response(
                client, base + "/tasks.task.get.json",
                {"taskId": task_id, "select": [
                    "ID", "TITLE", "RESPONSIBLE_ID", "UF_CRM_TASK",
                ]},
            )
            got = result.get("task")
            if not isinstance(got, dict):
                _stop("TASK_READBACK_UNCERTAIN")
            try:
                actual_id = _positive_id(got.get("id"))
                actual_responsible = _positive_id(got.get("responsibleId"))
            except TaskDiscoveryBlocked:
                _stop("TASK_READBACK_ID_MISMATCH")
            link = got.get("ufCrmTask")
            if (actual_id != task_id or got.get("title") != TASK_TITLE
                    or actual_responsible != rid or link != [CRM_BINDING]):
                _stop("TASK_READBACK_MISMATCH")
            _mark(con, "VERIFIED", task_id)
            return {
                "status": "ONE_DEMO_MANAGER_TASK_VERIFIED",
                "task_id": task_id,
                "crm_item_id": 3,
                "crm_binding": CRM_BINDING,
                "responsible_id_from_operator": rid,
                "recipient_is_mikhail_verified_by_api": False,
                "local_ledger_state": "VERIFIED",
                "crm_writes": "NO",
                "external_customer_contact": "NO",
                "task_created": True,
            }
    finally:
        if owner:
            client.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--responsible-id", required=True, type=int)
    parser.add_argument("--confirm-once", required=True)
    args = parser.parse_args()
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ServiceIT" / "One-Demo-Manager-Task"
    if not os.environ.get("LOCALAPPDATA"):
        print("SERVICEIT_ONE_DEMO_TASK=BLOCKED_NO_WINDOWS_LOCALAPPDATA")
        raise SystemExit(2)
    try:
        out = execute_one_demo_task(
            os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", ""),
            responsible_id=args.responsible_id,
            authorization=args.confirm_once,
            local_ledger=root / "one-demo-task.sqlite3",
        )
    except (OneShotTaskBlocked, TaskDiscoveryBlocked, ValueError, sqlite3.Error) as e:
        known = {
            "EXPLICIT_ONE_TASK_CONFIRMATION_REQUIRED",
            "RESPONSIBLE_ID_INVALID", "RESPONSIBLE_ID_OUT_OF_RANGE",
            "REMOTE_DEMO_TASK_PREFLIGHT_BLOCKED",
            *( "REMOTE_PREFLIGHT_" + x for x in (
                "EXACT_DEMO_METADATA_NOT_READY", "TASK_LIST_UNAVAILABLE",
                "TASK_LIST_HTTP_400", "TASK_LIST_HTTP_401",
                "TASK_LIST_HTTP_403", "TASK_LIST_HTTP_429",
                "TASK_LIST_HTTP_OTHER", "TASK_LIST_RESPONSE_TOO_LARGE",
                "TASK_LIST_API_ERROR", "TASK_LIST_MALFORMED",
                "TASK_LIST_SCHEMA_CHANGED", "TASK_ID_INVALID",
                "TASK_PAGINATION_DUPLICATE", "TASK_TITLE_MISSING",
                "CRM_TASK_LINK_UNEXPECTED_SHAPE",
                "TASK_TITLE_FILTER_NOT_EXACT", "TASK_CRM_FILTER_IGNORED",
                "TASK_PAGINATION_UNVERIFIED", "TASK_RESULT_WINDOW_TOO_LARGE",
                "UNBOUNDED_TASK_SEARCH_FORBIDDEN", "BLOCKED_UNSPECIFIED",
            )),
            "REMOTE_VISIBLE_DEMO_TASK_ALREADY_EXISTS",
            "LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY",
            "LOCAL_LEDGER_UNAVAILABLE",
            "LOCAL_LEDGER_POST_STATUS_UNCERTAIN",
            "TASK_REST_TRANSPORT_OR_TIMEOUT", "TASK_REST_HTTP_OR_SIZE_BLOCK",
            "TASK_REST_JSON_INVALID", "TASK_REST_RESPONSE_INVALID",
            "TASK_POST_ACK_UNCERTAIN", "TASK_POST_ID_UNCERTAIN",
            "TASK_READBACK_UNCERTAIN", "TASK_READBACK_ID_MISMATCH",
            "TASK_READBACK_MISMATCH", "TASK_ID_INVALID",
        }
        code = str(e) if str(e) in known else "BLOCKED_UNSPECIFIED"
        out = {"status": "BLOCKED_OR_UNCERTAIN", "reason_code": code,
               "crm_writes": "NO", "never_retry_uncertain_post": True}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("SERVICEIT_ONE_DEMO_TASK=" + out["status"])
    raise SystemExit(0 if out["status"] == "ONE_DEMO_MANAGER_TASK_VERIFIED" else 2)


if __name__ == "__main__":
    main()
