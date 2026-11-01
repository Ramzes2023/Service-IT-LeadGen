"""Bounded, read-only Bitrix24 task de-duplication for CRM 1062 / item 3.

Searches only the exact synthetic CRM binding and unique DEMO task title.
No tasks are created, no contacts or employee profiles are read, and no
remote response text, webhook secrets, or task descriptions are displayed.
"""
from __future__ import annotations

import json
import os

import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_task_metadata import audit_synthetic_task_link, TaskMetadataReadError
from .bitrix_1062_manager_task_preview import DemoTaskGateBlocked

TASK_TITLE = "[DEMO / НЕ ЗВОНИТЬ] Проверить сигнал 1С по карточке ServiceIT №3"
CRM_BINDING = "T426_3"
FIELDS = ("ID", "TITLE", "RESPONSIBLE_ID", "UF_CRM_TASK")
MAX_PAGES = 2
PAGE_SIZE = 50


class TaskDiscoveryBlocked(ValueError):
    """Read-only duplicate check is insufficient to safely plan a task."""


def _deny(code: str) -> None:
    raise TaskDiscoveryBlocked(code)


def _positive_id(value: object) -> int:
    if type(value) is int and 0 < value < 2_147_483_648:
        return value
    if (isinstance(value, str) and value.isascii()
            and value.isdecimal() and value[0] != "0" and len(value) <= 10
            and int(value) < 2_147_483_648):
        return int(value)
    _deny("TASK_ID_INVALID")


def _list_tasks(client: httpx.Client, base: str, field: str, match: str) -> list[dict]:
    if field not in ("TITLE", "UF_CRM_TASK") or match not in (TASK_TITLE, CRM_BINDING):
        _deny("UNBOUNDED_TASK_SEARCH_FORBIDDEN")
    seen: set[int] = set()
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        params = {
            "filter": {field: match},
            "select": list(FIELDS),
            "start": page * PAGE_SIZE,
        }
        try:
            response = client.post(
                base + "/tasks.task.list.json", json=params,
                timeout=12.0, follow_redirects=False,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            _deny("TASK_LIST_UNAVAILABLE")
        if len(response.content) > 128_000:
            _deny("TASK_LIST_RESPONSE_TOO_LARGE")
        if response.status_code != 200:
            if response.status_code in (400, 401, 403, 429):
                _deny("TASK_LIST_HTTP_" + str(response.status_code))
            _deny("TASK_LIST_HTTP_OTHER")
        try:
            raw = response.json()
        except (ValueError, TypeError):
            _deny("TASK_LIST_MALFORMED")
        if not isinstance(raw, dict):
            _deny("TASK_LIST_MALFORMED")
        if "error" in raw:
            _deny("TASK_LIST_API_ERROR")
        if not isinstance(raw.get("result"), dict):
            _deny("TASK_LIST_SCHEMA_CHANGED")
        data = raw["result"].get("tasks")
        if not isinstance(data, list) or len(data) > PAGE_SIZE:
            _deny("TASK_LIST_SCHEMA_CHANGED")
        for task in data:
            if not isinstance(task, dict):
                _deny("TASK_LIST_SCHEMA_CHANGED")
            tid = _positive_id(task.get("id", task.get("ID")))
            if tid in seen:
                _deny("TASK_PAGINATION_DUPLICATE")
            seen.add(tid)
            title = task.get("title", task.get("TITLE"))
            if not isinstance(title, str):
                _deny("TASK_TITLE_MISSING")
            bind = task.get("ufCrmTask", task.get("UF_CRM_TASK", None))
            if bind is None:
                normalized = None
            elif isinstance(bind, list) and all(isinstance(v, str) for v in bind):
                normalized = bind
            elif isinstance(bind, str):
                normalized = [bind]
            else:
                _deny("CRM_TASK_LINK_UNEXPECTED_SHAPE")
            # Both scans are intentionally fail-closed on servers that ignore
            # filters, rather than silently ignoring rows from unrelated tasks.
            if field == "TITLE" and title != TASK_TITLE:
                _deny("TASK_TITLE_FILTER_NOT_EXACT")
            if field == "UF_CRM_TASK" and (normalized is None or CRM_BINDING not in normalized):
                _deny("TASK_CRM_FILTER_IGNORED")
            rows.append({"id": tid, "title_exact": title == TASK_TITLE,
                         "bound_to_demo": normalized is not None and CRM_BINDING in normalized})
        if len(data) < PAGE_SIZE:
            return rows
        # A full page requires an explicit server pagination cursor.
        next_page = raw.get("next")
        if type(next_page) is not int or next_page != (page + 1) * PAGE_SIZE:
            _deny("TASK_PAGINATION_UNVERIFIED")
    _deny("TASK_RESULT_WINDOW_TOO_LARGE")


def discover_demo_manager_task(
    webhook: str, *, client: httpx.Client | None = None,
) -> dict:
    """Strict metadata + two read-only task searches; no write authorization."""
    base = validate_webhook(webhook)
    own = client is None
    if own:
        client = httpx.Client(timeout=12.0, follow_redirects=False, trust_env=False)
    try:
        # Metadata check revalidates the exact DEMO card, process link, and
        # that robots/business processes remain switched off.
        try:
            meta = audit_synthetic_task_link(webhook, client=client)
        except TaskMetadataReadError as exc:
            # The metadata reader uses only known project-generated reason
            # codes, not remote Bitrix error text. Never reveal a response body.
            code = str(exc)
            if code in {
                "READ_HTTP_UNAVAILABLE", "READ_RESPONSE_UNAVAILABLE",
                "READ_FAILED", "READ_METHOD_FORBIDDEN", "SCHEMA_UNEXPECTED",
            }:
                _deny("EXACT_DEMO_METADATA_" + code)
            _deny("EXACT_DEMO_METADATA_READ_UNCLASSIFIED")
        except (DemoTaskGateBlocked, ValueError, httpx.HTTPError):
            _deny("EXACT_DEMO_METADATA_READ_UNCLASSIFIED")
        if meta["status"] != "LINKED_TASK_METADATA_READY":
            gate_codes = {
                "owner_symbol_verified": "OWNER_SYMBOL",
                "task_link_enabled": "TASK_LINK",
                "automation_confirmed_off": "AUTOMATION_OFF",
                "task_fields_ready": "TASK_FIELDS",
                "exact_demo_card_readback": "EXACT_CARD",
            }
            failed = [code for name, code in gate_codes.items()
                      if meta.get(name) is not True]
            if len(failed) == 1:
                _deny("EXACT_DEMO_GATE_" + failed[0] + "_FAILED")
            _deny("EXACT_DEMO_MULTIPLE_OR_UNKNOWN_GATES_FAILED")
        linked = _list_tasks(client, base, "UF_CRM_TASK", CRM_BINDING)
        titled = _list_tasks(client, base, "TITLE", TASK_TITLE)
        unique = {record["id"]: record for record in [*linked, *titled]}
        already = len(unique) != 0
        return {
            "status": "EXISTING_VISIBLE_TASK_FOUND" if already else "NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF",
            "portal": "serviseit.bitrix24.ru",
            "crm_item_id": 3,
            "crm_binding_verified": CRM_BINDING,
            "scans_completed": 2,
            "visible_linked_count": len(linked),
            "visible_demo_title_count": len(titled),
            "visible_unique_task_count": len(unique),
            "existing_visible_task_ids": sorted(unique),
            "identity_of_manager_confirmed": False,
            "visibility_complete_for_all_company_tasks": False,
            "safe_to_create_task_now": False,
            "task_created": False,
            "crm_writes": "NO",
            "customer_contact": "NO",
        }
    finally:
        if own:
            client.close()


def main() -> None:
    try:
        outcome = discover_demo_manager_task(os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", ""))
    except (TaskDiscoveryBlocked, ValueError, httpx.HTTPError) as exc:
        code = str(exc)
        if code not in {
            "EXACT_DEMO_METADATA_NOT_READY",
            "EXACT_DEMO_METADATA_READ_HTTP_UNAVAILABLE",
            "EXACT_DEMO_METADATA_READ_RESPONSE_UNAVAILABLE",
            "EXACT_DEMO_METADATA_READ_FAILED",
            "EXACT_DEMO_METADATA_READ_METHOD_FORBIDDEN",
            "EXACT_DEMO_METADATA_SCHEMA_UNEXPECTED",
            "EXACT_DEMO_METADATA_READ_UNCLASSIFIED",
            "EXACT_DEMO_GATE_OWNER_SYMBOL_FAILED",
            "EXACT_DEMO_GATE_TASK_LINK_FAILED",
            "EXACT_DEMO_GATE_AUTOMATION_OFF_FAILED",
            "EXACT_DEMO_GATE_TASK_FIELDS_FAILED",
            "EXACT_DEMO_GATE_EXACT_CARD_FAILED",
            "EXACT_DEMO_MULTIPLE_OR_UNKNOWN_GATES_FAILED",
            "TASK_LIST_UNAVAILABLE", "TASK_LIST_MALFORMED",
            "TASK_LIST_SCHEMA_CHANGED", "TASK_LIST_HTTP_400", "TASK_LIST_HTTP_401",
            "TASK_LIST_HTTP_403", "TASK_LIST_HTTP_429", "TASK_LIST_HTTP_OTHER",
            "TASK_LIST_RESPONSE_TOO_LARGE", "TASK_LIST_API_ERROR",
            "TASK_ID_INVALID", "TASK_PAGINATION_DUPLICATE",
            "TASK_TITLE_MISSING", "CRM_TASK_LINK_UNEXPECTED_SHAPE",
            "TASK_TITLE_FILTER_NOT_EXACT", "TASK_CRM_FILTER_IGNORED",
            "TASK_PAGINATION_UNVERIFIED", "TASK_RESULT_WINDOW_TOO_LARGE",
            "UNBOUNDED_TASK_SEARCH_FORBIDDEN",
        }:
            code = "BLOCKED_UNSPECIFIED"
        outcome = {"status": "BLOCKED_READ_ONLY", "reason_code": code,
                   "task_created": False, "crm_writes": "NO"}
        if code.startswith("EXACT_DEMO_"):
            # A blocked metadata gate is not a task-list error. Collect only
            # independently sanitized REST method states and boolean gates,
            # with no raw CRM item, error descriptions or webhook in output.
            try:
                from .bitrix_1062_task_diagnostics import diagnose_task_metadata
                diag = diagnose_task_metadata(
                    os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", "")
                )
                outcome["metadata_diagnostics"] = {
                    "status": diag["status"],
                    "methods": diag["methods"],
                    "metadata_gates": diag["metadata_gates"],
                }
                if code == "EXACT_DEMO_GATE_EXACT_CARD_FAILED":
                    outcome["metadata_diagnostics"]["exact_demo_card_checks"] = (
                        diag.get("exact_demo_card_checks", "NOT_AVAILABLE")
                    )
            except (ValueError, TypeError, httpx.HTTPError, KeyError):
                outcome["metadata_diagnostics"] = "INDEPENDENT_READ_FAILED"
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["status"] in
                     ("EXISTING_VISIBLE_TASK_FOUND", "NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF") else 2)


if __name__ == "__main__":
    main()
