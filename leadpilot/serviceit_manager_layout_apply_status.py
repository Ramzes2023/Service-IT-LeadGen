"""Post a strictly redacted receipt for the owner's one-shot shared manager layout apply.

Only fixed status tokens and a write count are posted. No layout, hashes,
Bitrix response, secret, exception text or field names leave the runner.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import httpx

REPOSITORY = "Ramzes2023/Service-IT-LeadGen"
ISSUE = 74
TRIGGER = "SERVICEIT_MANAGER_LAYOUT_APPLY_V1"
PREFIX = "SERVICEIT_MANAGER_LAYOUT_APPLY_AUDIT_V1"
STATUSES = frozenset({
    "MANAGER_LAYOUT_APPLIED_EXACT", "ALREADY_PRESENT",
    "OWNER_LAYOUT_OPT_IN_MISSING", "ADMIN_SHARED_SCOPE_NOT_APPROVED",
    "BITRIX_WEBHOOK_MISSING", "ENCRYPTED_BACKUP_CREDENTIALS_MISSING",
    "REPOSITORY_SCOPE_BLOCKED", "LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED",
})

class ApplyAuditBlocked(ValueError):
    pass

def safe_summary(raw: str) -> str:
    fallback = PREFIX + " STATUS=LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED CRM_WRITES=UNVERIFIED"
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= 2000:
        return fallback
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return fallback
    if type(data) is not dict:
        return fallback
    status = data.get("status")
    if type(status) is not str or status not in STATUSES:
        status = "LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED"
    if status == "MANAGER_LAYOUT_APPLIED_EXACT":
        writes = "1"
    elif status == "ALREADY_PRESENT" or status in {
        "OWNER_LAYOUT_OPT_IN_MISSING", "ADMIN_SHARED_SCOPE_NOT_APPROVED",
        "BITRIX_WEBHOOK_MISSING", "ENCRYPTED_BACKUP_CREDENTIALS_MISSING",
        "REPOSITORY_SCOPE_BLOCKED",
    }:
        writes = "0"
    else:
        writes = "UNVERIFIED"
    return PREFIX + " STATUS=" + status + " CRM_WRITES=" + writes

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
        raise ApplyAuditBlocked("APPLY_TRIGGER_NOT_AUTHORIZED")
    if type(token) is not str or len(token) < 12:
        raise ApplyAuditBlocked("GITHUB_TOKEN_MISSING")
    message = safe_summary(output)
    try:
        response = client.post(
            "https://api.github.com/repos/" + REPOSITORY + "/issues/" + str(ISSUE) + "/comments",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"body": message}, timeout=20, follow_redirects=False,
        )
        if response.status_code != 201 or len(response.content) > 12000:
            raise ApplyAuditBlocked("GITHUB_AUDIT_WRITE_UNVERIFIED")
        reply = response.json()
        if (type(reply) is not dict or reply.get("body") != message
                or (reply.get("user") or {}).get("login") != "github-actions[bot]"):
            raise ApplyAuditBlocked("GITHUB_AUDIT_READBACK_UNVERIFIED")
    except httpx.HTTPError:
        raise ApplyAuditBlocked("GITHUB_AUDIT_TRANSPORT_UNKNOWN") from None
    return {"status": "AUDIT_ACKED"}

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    token = os.environ.pop("GITHUB_TOKEN", "")
    try:
        if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
                or os.environ.get("GITHUB_EVENT_NAME") != "issue_comment"
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or not args.result.is_file() or args.result.stat().st_size > 2000):
            raise ApplyAuditBlocked("APPLY_JOB_SCOPE_INVALID")
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        if not event_path.is_file() or event_path.stat().st_size > 100000:
            raise ApplyAuditBlocked("APPLY_EVENT_MISSING")
        event = json.loads(event_path.read_text(encoding="utf-8"))
        output = args.result.read_text(encoding="utf-8")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            post_summary(event, output, token, client=client)
        print('{"status":"AUDIT_ACKED"}')
        return 0
    except Exception:
        print('{"status":"AUDIT_REQUIRES_REVIEW"}')
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
