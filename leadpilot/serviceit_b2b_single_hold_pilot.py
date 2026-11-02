"""One explicit, existing-queue B2B HOLD pilot. NO background fanout.

Only after the owner creates exactly one marker on Issue 83, read an existing
canonical verified-owner Issue 66 comment by fixed ID and invoke the existing
read-before-write, reserve-before-POST, exact-readback code ONCE.
The pilot never sends Gmail to public GitHub, never calls a model, and never
promotes a tender to a qualified lead or sends outreach.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import httpx

from .b2b_github_to_bitrix import REPO, OWNER, BEGIN, run_once, notice_from_event

MARKER = "SERVICEIT_B2B_SINGLE_HOLD_PILOT_V1"
APPROVAL_ISSUE = 83
SOURCE_ISSUE = 66
SOURCE_COMMENT = 6582181613
TENDER_ID = "4665948"
SOURCE_API = (
    "https://api.github.com/repos/" + REPO
    + "/issues/comments/" + str(SOURCE_COMMENT)
)
SOURCE_ISSUE_API = (
    "https://api.github.com/repos/" + REPO
    + "/issues/" + str(SOURCE_ISSUE)
)


class OneCardPilotBlocked(ValueError):
    pass


def is_owner_pilot_event(event):
    return (
        isinstance(event, dict)
        and event.get("action") == "created"
        and (event.get("repository") or {}).get("full_name") == REPO
        and (event.get("issue") or {}).get("number") == APPROVAL_ISSUE
        and not (event.get("issue") or {}).get("pull_request")
        and (event.get("sender") or {}).get("login") == OWNER
        and (event.get("comment") or {}).get("body") == MARKER
        and ((event.get("comment") or {}).get("user") or {}).get("login") == OWNER
    )


def load_existing_review(client, github_token):
    if not isinstance(github_token, str) or len(github_token) < 12:
        raise OneCardPilotBlocked("GITHUB_TOKEN_REQUIRED")
    response = client.get(
        SOURCE_API,
        headers={
            "Authorization": "Bearer " + github_token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        follow_redirects=False,
        timeout=15,
    )
    # GitHub's metadata envelope exceeds 4096 UTF-8 bytes even when the
    # owner-supplied notice body is only a few hundred characters. Keep
    # a bounded 12 KiB API envelope; notice_from_event separately caps the
    # owner-authored notice to 2500 characters and validates every field.
    if response.status_code != 200 or len(response.content) > 12000:
        raise OneCardPilotBlocked("ORIGINAL_COMMENT_NOT_AVAILABLE")
    comment = response.json()
    if not isinstance(comment, dict) or (
        comment.get("id") != SOURCE_COMMENT
        or comment.get("issue_url") != SOURCE_ISSUE_API
        or (comment.get("user") or {}).get("login") != OWNER
        or not isinstance(comment.get("body"), str)
        or not comment["body"].startswith(BEGIN)
    ):
        raise OneCardPilotBlocked("ORIGINAL_COMMENT_NOT_TRUSTED")
    # Reconstruct only the existing, owner-issued review notice, never an
    # event containing an arbitrary public comment or external text.
    event = {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": SOURCE_ISSUE},
        "sender": {"login": OWNER},
        "comment": {
            "user": {"login": OWNER},
            "body": comment["body"],
        },
    }
    notice, _services = notice_from_event(event)
    if notice["tender_id"] != TENDER_ID or notice["qualified_lead"] is not False:
        raise OneCardPilotBlocked("WRONG_REVIEW_OR_LEAD")
    return event


def run(event, *, client, webhook, github_token):
    if not is_owner_pilot_event(event):
        return {"status": "PILOT_OWNER_EVENT_DENIED", "crm_writes": 0}
    if not webhook:
        return {"status": "CORPORATE_WEBHOOK_MISSING", "crm_writes": 0}
    source_event = load_existing_review(client, github_token)
    # The existing routine will block if:
    # - a GitHub bot already reserved source key
    # - Bitrix 1062/21 schema or stage is not exact
    # - an exact card already exists, even with another title
    # - a CRM or readback result is ambiguous.
    # No repeated POST is attempted and live=False never used here.
    result = run_once(source_event, client=client, github_token=github_token,
                      webhook=webhook, live=True)
    status = result.get("status")
    if status == "ONE_TENDER_HOLD_VERIFIED":
        return {
            "status": "ONE_HOLD_VERIFIED_EXACT_READBACK",
            "tender_id": TENDER_ID, "crm_writes": 1,
            "qualified_real_leads": 0, "gigachat_calls": 0,
        }
    if status in {"ALREADY_RESERVED_DO_NOT_RETRY", "ALREADY_EXISTS_IN_CRM"}:
        return {
            "status": status,
            "tender_id": TENDER_ID,
            "crm_writes": 0, "qualified_real_leads": 0,
            "gigachat_calls": 0,
        }
    raise OneCardPilotBlocked("PILOT_UNEXPECTED_RESULT")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print('{"status":"PILOT_EVENT_REQUIRED","crm_writes":0}')
        return 2
    try:
        path = Path(argv[0])
        if not path.is_file() or path.stat().st_size > 100000:
            raise OneCardPilotBlocked("PILOT_EVENT_INVALID")
        owner_event = json.loads(path.read_text(encoding="utf-8"))
        webhook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
        token = os.environ.pop("GITHUB_TOKEN", "")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            output = run(owner_event, client=client, webhook=webhook,
                         github_token=token)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0 if output["status"] in {
            "ONE_HOLD_VERIFIED_EXACT_READBACK",
            "ALREADY_RESERVED_DO_NOT_RETRY",
            "ALREADY_EXISTS_IN_CRM",
        } else 2
    except Exception:
        # Deliberately never print Bitrix webhook, model keys, raw B2B
        # notification, CRM record, HTTP response body or stack trace.
        print('{"status":"PILOT_STOPPED_OR_UNCERTAIN_NO_RETRY","crm_writes":"UNVERIFIED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
