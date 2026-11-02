"""Side-effect-free trace for the aborted one-card pilot.

No crm.item.add/update/delete, no Github POST, no model call. Read only
the pre-existing source comment, Bitrix metadata/item duplicates and bot locks.
The public CI log contains safe stage codes only, never secrets or CRM records.
"""
import json
import os
from pathlib import Path
import sys
import httpx
from .b2b_github_to_bitrix import (
    GitHubLedger, exact_duplicate, hold_payload, notice_from_event,
)
from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .serviceit_b2b_single_hold_pilot import (
    REPO, OWNER, SOURCE_COMMENT, TENDER_ID, load_existing_review,
)

MARKER = "SERVICEIT_B2B_SINGLE_HOLD_READONLY_AUDIT_V1"


def permitted(evt):
    return (
        isinstance(evt, dict)
        and evt.get("action") == "created"
        and (evt.get("repository") or {}).get("full_name") == REPO
        and (evt.get("issue") or {}).get("number") == 83
        and not (evt.get("issue") or {}).get("pull_request")
        and (evt.get("sender") or {}).get("login") == OWNER
        and (evt.get("comment") or {}).get("body") == MARKER
        and ((evt.get("comment") or {}).get("user") or {}).get("login") == OWNER
    )


def run(evt, *, github_token, hook, client):
    result = {"status": "OWNER_EVENT_BLOCKED", "crm_writes": 0}
    if not permitted(evt):
        return result
    if not hook or not github_token:
        return {"status": "CREDENTIAL_OR_TOKEN_UNAVAILABLE", "crm_writes": 0}

    try:
        original = load_existing_review(client, github_token)
        notice, services = notice_from_event(original)
        assert notice["tender_id"] == TENDER_ID
        payload = hold_payload(notice, services)
        assert payload["fields"]["UF_CRM_13_SVCIT_SIGNAL_ID"] == (
            "B2B-CENTER-" + TENDER_ID
        )
    except Exception:
        return {"status": "SOURCE_READ_OR_PAYLOAD_BLOCKED", "crm_writes": 0}

    try:
        rest = ReadOnlyBitrix(hook, client=client)
        preflight(rest)
    except Exception:
        return {"status": "CORPORATE_SCHEMA_READ_BLOCKED", "crm_writes": 0}

    try:
        items = exact_duplicate(client, rest.base, "B2B-CENTER-" + TENDER_ID)
    except Exception:
        return {"status": "DUPLICATE_READ_BLOCKED", "crm_writes": 0}
    if items:
        return {"status": "EXISTING_CRM_RECORD_NO_WRITE", "crm_writes": 0}

    try:
        locked = "B2B-CENTER-" + TENDER_ID in GitHubLedger(client, github_token).markers()
    except Exception:
        return {"status": "LEDGER_READ_BLOCKED", "crm_writes": 0}
    return {
        "status": "READONLY_SOURCE_AND_CRM_GATES_PASS",
        "source_id": TENDER_ID,
        "source_comment_id": SOURCE_COMMENT,
        "locked_before_write": bool(locked),
        "crm_writes": 0, "gigachat_calls": 0,
    }


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('{"status":"AUDIT_EVENT_REQUIRED","crm_writes":0}')
        return 2
    try:
        p = Path(args[0])
        if not p.is_file() or p.stat().st_size > 100000:
            raise ValueError("EVENT_SIZE_BLOCKED")
        evt = json.loads(p.read_text(encoding="utf-8"))
        hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
        token = os.environ.pop("GITHUB_TOKEN", "")
        with httpx.Client(trust_env=False, follow_redirects=False) as c:
            output = run(evt, github_token=token, hook=hook, client=c)
        print(json.dumps(output, sort_keys=True))
        return 0 if output["status"] in {
            "READONLY_SOURCE_AND_CRM_GATES_PASS",
            "EXISTING_CRM_RECORD_NO_WRITE",
        } else 2
    except Exception:
        print('{"status":"AUDIT_UNCERTAIN_NO_WRITES","crm_writes":0}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
