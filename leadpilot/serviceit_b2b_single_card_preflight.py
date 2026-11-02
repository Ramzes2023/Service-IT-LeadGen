"""Read-only one-card B2B→Bitrix readiness from a tightly scoped GitHub event.

Only safe REST methods: crm.type.getByEntityTypeId, crm.category.list,
crm.item.fields, crm.status.list, crm.item.list. No add/update/delete,
no token disclosure, no contact/customer lookup, no Model API.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import httpx
from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .b2b_github_to_bitrix import exact_duplicate, GitHubLedger

REPO = "Ramzes2023/Service-IT-LeadGen"
MARKER = "SERVICEIT_B2B_PILOT_PREFLIGHT_V1"
# Every ID already appeared in the owner-created source-review queue.
CANDIDATES = ("4661617", "4666206", "4664763", "4665690", "4665948")


def check_event(event: dict) -> bool:
    return (
        isinstance(event, dict)
        and event.get("action") == "created"
        and (event.get("repository") or {}).get("full_name") == REPO
        and (event.get("issue") or {}).get("number") == 83
        and not (event.get("issue") or {}).get("pull_request")
        and (event.get("sender") or {}).get("login") == "Ramzes2023"
        and (event.get("comment") or {}).get("body") == MARKER
        and ((event.get("comment") or {}).get("user") or {}).get("login") == "Ramzes2023"
    )


def run(event: dict, *, hook: str, gh_token: str, client: httpx.Client) -> dict:
    if not check_event(event):
        return {"status": "UNTRUSTED_EVENT", "crm_writes": 0}
    if not hook:
        return {"status": "WEBHOOK_NOT_CONFIGURED", "crm_writes": 0}
    if not gh_token:
        return {"status": "GITHUB_LEDGER_NOT_AVAILABLE", "crm_writes": 0}
    try:
        rest = ReadOnlyBitrix(hook, client=client)
        preflight(rest)
    except Exception:
        return {"status": "BITRIX_READONLY_SCHEMA_BLOCKED", "crm_writes": 0}
    try:
        existing_ids = set()
        for tid in CANDIDATES:
            items = exact_duplicate(client, rest.base, "B2B-CENTER-" + tid)
            if items:
                existing_ids.add("B2B-CENTER-" + tid)
        ledger = GitHubLedger(client, gh_token)
        locks = set(ledger.markers())
        locked_count = sum(("B2B-CENTER-" + tid) in locks for tid in CANDIDATES)
    except Exception:
        return {"status": "CRM_OR_LEDGER_READONLY_BLOCKED", "crm_writes": 0}
    return {
        "status": "READONLY_PREFLIGHT_PASS",
        "examined_source_ids": len(CANDIDATES),
        "existing_crm_count": len(existing_ids),
        "locked_source_count": locked_count,
        "unlocked_not_in_crm_count": sum(("B2B-CENTER-" + tid) not in locks and
                ("B2B-CENTER-" + tid) not in existing_ids for tid in CANDIDATES),
        "crm_writes": 0,
        "gigachat_calls": 0,
    }


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('{"status":"EXPECTED_EVENT_PATH","crm_writes":0}')
        return 2
    try:
        path = Path(args[0])
        if not path.is_file() or path.stat().st_size > 100000:
            raise ValueError("EVENT_FILE_INVALID")
        event = json.loads(path.read_text(encoding="utf-8"))
        hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
        github = os.environ.pop("GITHUB_TOKEN", "")
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            response = run(event, hook=hook, gh_token=github, client=client)
        print(json.dumps(response, sort_keys=True))
        return 0 if response["status"] == "READONLY_PREFLIGHT_PASS" else 2
    except Exception:
        print('{"status":"PILOT_PREFLIGHT_UNCERTAIN","crm_writes":0}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
