"""Post ONLY whitelisted manager-layout read-only acceptance codes to Issue #74.

No raw Bitrix layout, hashes, webhook, token, private metadata or exceptions.
This exists so a remote Google/GitHub-only project acceptance can be checked
without asking the owner for another Windows script or screen share.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

REPOSITORY = "Ramzes2023/Service-IT-LeadGen"
ISSUE = 74
TRIGGER = "SERVICEIT_MANAGER_LAYOUT_READONLY_V1"
PREFIX = "SERVICEIT_MANAGER_LAYOUT_AUDIT_V1"
STATUSES = frozenset({
    "PREVIEW_ONLY", "ALREADY_PRESENT",
    "BITRIX_WEBHOOK_MISSING", "REPOSITORY_SCOPE_BLOCKED",
    "LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED",
})


class AuditBlocked(ValueError):
    pass


def safe_summary(raw: str) -> str:
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= 2000:
        return PREFIX + " STATUS=BLOCKED_REVIEW ADDED=UNKNOWN CRM_WRITES=0"
    try:
        report = json.loads(raw)
    except (ValueError, TypeError):
        return PREFIX + " STATUS=BLOCKED_REVIEW ADDED=UNKNOWN CRM_WRITES=0"
    if type(report) is not dict:
        return PREFIX + " STATUS=BLOCKED_REVIEW ADDED=UNKNOWN CRM_WRITES=0"
    status = report.get("status")
    if type(status) is not str or status not in STATUSES:
        status = "BLOCKED_REVIEW"
    added = report.get("added_fields")
    added = str(added) if type(added) is int and 0 <= added <= 100 else "UNKNOWN"
    # A failure may have produced an ambiguous write in a different mode.
    # This function is *only* called on the read-only workflow step.
    return PREFIX + " STATUS=" + status + " ADDED=" + added + " CRM_WRITES=0"


def allowed_event(event):
    return (
        type(event) is dict and event.get("action") == "created"
        and (event.get("repository") or {}).get("full_name") == REPOSITORY
        and (event.get("issue") or {}).get("number") == ISSUE
        and not (event.get("issue") or {}).get("pull_request")
        and (event.get("sender") or {}).get("login") == "Ramzes2023"
        and (event.get("comment") or {}).get("body") == TRIGGER
        and ((event.get("comment") or {}).get("user") or {}).get("login") == "Ramzes2023"
    )


def post_summary(event, output, token, *, client):
    if not allowed_event(event):
        raise AuditBlocked("READONLY_TRIGGER_NOT_AUTHORIZED")
    if type(token) is not str or len(token) < 12:
        raise AuditBlocked("GITHUB_TOKEN_MISSING")
    message = safe_summary(output)
    try:
        response = client.post(
            "https://api.github.com/repos/" + REPOSITORY
            + "/issues/" + str(ISSUE) + "/comments",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"body": message}, timeout=20,
            follow_redirects=False,
        )
        if response.status_code != 201 or len(response.content) > 12000:
            raise AuditBlocked("GITHUB_AUDIT_WRITE_UNVERIFIED")
        reply = response.json()
        if (not isinstance(reply, dict) or reply.get("body") != message
                or (reply.get("user") or {}).get("login") != "github-actions[bot]"):
            raise AuditBlocked("GITHUB_AUDIT_READBACK_UNVERIFIED")
    except httpx.HTTPError:
        raise AuditBlocked("GITHUB_AUDIT_TRANSPORT_UNKNOWN") from None
    return {"status": "AUDIT_ACKED", "crm_writes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    token = os.environ.pop("GITHUB_TOKEN", "")
    try:
        if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
                or os.environ.get("GITHUB_EVENT_NAME") != "issue_comment"
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or not args.result.is_file()
                or args.result.stat().st_size > 2000):
            raise AuditBlocked("READONLY_JOB_SCOPE_INVALID")
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        if not event_path.is_file() or event_path.stat().st_size > 100000:
            raise AuditBlocked("READONLY_EVENT_MISSING")
        event = json.loads(event_path.read_text(encoding="utf-8"))
        output = args.result.read_text(encoding="utf-8")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = post_summary(event, output, token, client=client)
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"status":"AUDIT_REQUIRES_REVIEW","crm_writes":0}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
