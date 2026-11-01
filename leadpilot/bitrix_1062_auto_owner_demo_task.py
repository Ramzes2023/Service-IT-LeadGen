"""Auto-assign a single synthetic Bitrix task to the webhook's own user.

Strictly no Mikhail ID, no external contacts, and no automatic retry.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_one_shot_task import execute_one_demo_task, OneShotTaskBlocked, CONFIRM

def owner_id(webhook: str, client: httpx.Client) -> int:
    base = validate_webhook(webhook)
    m = re.fullmatch(r"/rest/([1-9][0-9]*)/[A-Za-z0-9_-]{6,}", urlsplit(base).path)
    if not m or int(m.group(1)) > 999999999:
        raise OneShotTaskBlocked("OWNER_URL_INVALID")
    try:
        r = client.post(base + "/profile.json", json={}, timeout=12, follow_redirects=False)
        if r.status_code != 200 or len(r.content) > 16384:
            raise ValueError()
        payload = r.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError()
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, TypeError, httpx.HTTPError):
        raise OneShotTaskBlocked("OWNER_PROFILE_UNAVAILABLE") from None
    user_id = result.get("ID")
    if (isinstance(user_id, bool) or not isinstance(user_id, (str, int))
            or not re.fullmatch(r"[1-9][0-9]{0,8}", str(user_id))
            or int(user_id) != int(m.group(1))):
        raise OneShotTaskBlocked("OWNER_PROFILE_ID_MISMATCH")
    first, last = result.get("NAME"), result.get("LAST_NAME")
    # Bitrix24 profiles can have an empty optional surname. A verified
    # authenticated webhook owner ID plus a nonempty first name is sufficient
    # for this single synthetic internal demonstration. Absent/ambiguous
    # first names remain blocked; never guess an employee from the Windows PC.
    if not isinstance(first, str) or not 2 <= len(first.strip()) <= 80:
        raise OneShotTaskBlocked("OWNER_FIRST_NAME_MISSING")
    if last is not None and (not isinstance(last, str) or len(last.strip()) > 80):
        raise OneShotTaskBlocked("OWNER_SURNAME_INVALID")
    full = (first + " " + (last or "")).casefold().replace("ё", "е")
    if any(x in full for x in (
        "михаил", "микаил", "миша", "mikhail", "michail",
        "mikail", "michael", "misha", "mikael",
    )):
        raise OneShotTaskBlocked("MANAGER_RECIPIENT_FORBIDDEN")
    return int(user_id)

def run(webhook: str, local_ledger: Path, client: httpx.Client | None = None) -> dict:
    base = validate_webhook(webhook)
    own = client is None
    if own:
        client = httpx.Client(timeout=18, trust_env=False, follow_redirects=False)
    try:
        rid = owner_id(base, client)
        out = execute_one_demo_task(base, responsible_id=rid,
                                    local_ledger=local_ledger,
                                    authorization=CONFIRM, client=client)
        out["recipient_selection"] = "VERIFIED_WEBHOOK_OWNER_NOT_MANAGER"
        return out
    finally:
        if own:
            client.close()

def main() -> None:
    env = os.environ.get("LOCALAPPDATA")
    if not env:
        print("SERVICEIT_AUTO_TASK=BLOCKED_NO_LOCALAPPDATA")
        raise SystemExit(2)
    path = Path(env) / "ServiceIT" / "One-Demo-Manager-Task" / "one-demo-task.sqlite3"
    try:
        out = run(os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", ""), path)
    except (OneShotTaskBlocked, ValueError, httpx.HTTPError) as ex:
        code = str(ex)
        approved_preflight = {
            "REMOTE_PREFLIGHT_" + x for x in (
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
                "TASK_LIST_UNAVAILABLE",
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
            )
        }
        if code not in approved_preflight and code not in {"OWNER_URL_INVALID", "OWNER_PROFILE_UNAVAILABLE",
                        "OWNER_PROFILE_ID_MISMATCH", "OWNER_IDENTITY_UNVERIFIABLE",
                        "OWNER_FIRST_NAME_MISSING", "OWNER_SURNAME_INVALID",
                        "MANAGER_RECIPIENT_FORBIDDEN", "REMOTE_DEMO_TASK_PREFLIGHT_BLOCKED",
                        "REMOTE_VISIBLE_DEMO_TASK_ALREADY_EXISTS",
                        "LOCAL_DEMO_TASK_ALREADY_RESERVED_DO_NOT_RETRY",
                        "LOCAL_LEDGER_UNAVAILABLE", "LOCAL_LEDGER_POST_STATUS_UNCERTAIN",
                        "TASK_REST_TRANSPORT_OR_TIMEOUT", "TASK_REST_HTTP_OR_SIZE_BLOCK",
                        "TASK_REST_JSON_INVALID", "TASK_REST_RESPONSE_INVALID",
                        "TASK_POST_ACK_UNCERTAIN", "TASK_POST_ID_UNCERTAIN",
                        "TASK_READBACK_UNCERTAIN", "TASK_READBACK_ID_MISMATCH",
                        "TASK_READBACK_MISMATCH"}:
            code = "BLOCKED_UNSPECIFIED"
        print("SERVICEIT_AUTO_TASK=BLOCKED_OR_UNCERTAIN;" + code)
        raise SystemExit(2) from None
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("SERVICEIT_AUTO_TASK=VERIFIED")

if __name__ == "__main__":
    main()
