"""HMAC-authenticated Apps Script -> GitHub Actions -> Bitrix B2B HOLD ingress.

The Apps Script sends only a minimal public tender notice. This adapter
validates a short-lived HMAC envelope, then delegates to the existing guarded
B2B HOLD writer. It never logs the subject, organizer, URL, Gmail ID, card ID
or secrets. No source email body, contacts, phone, buyer INN or attachment
is received by GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import time

import httpx

from .b2b_github_to_bitrix import (
    BEGIN, BridgeBlocked, OWNER, REPO, run_once,
)

EXPECTED_MAILBOX = "ramzesisayev444@gmail.com"
EVENT_TYPE = "serviceit_b2b_notice"
MAX_EVENT_BYTES = 100_000
MAX_PAYLOAD_BYTES = 5_000
MAX_AGE_SECONDS = 900


class IngestBlocked(ValueError):
    pass


def require(ok: bool, code: str):
    if not ok:
        raise IngestBlocked(code)


def validate_dispatch(event: dict, secret: str, *, now: float | None = None) -> tuple[dict, str]:
    require(type(event) is dict, "EVENT_INVALID")
    repository = event.get("repository") or {}
    require(type(repository) is dict and repository.get("full_name") == REPO,
            "REPOSITORY_SCOPE_BLOCKED")
    require(event.get("action") == EVENT_TYPE, "EVENT_TYPE_BLOCKED")
    payload = event.get("client_payload")
    require(type(payload) is dict and set(payload) == {"timestamp", "payload_json", "signature"},
            "CLIENT_PAYLOAD_SCHEMA_INVALID")
    require(type(secret) is str and len(secret) >= 32, "HMAC_SECRET_MISSING")
    timestamp = payload.get("timestamp")
    payload_json = payload.get("payload_json")
    signature = payload.get("signature")
    require(type(timestamp) is str and timestamp.isascii() and timestamp.isdecimal(),
            "TIMESTAMP_INVALID")
    require(type(payload_json) is str and 0 < len(payload_json.encode("utf-8")) <= MAX_PAYLOAD_BYTES,
            "PAYLOAD_SIZE_INVALID")
    require(type(signature) is str and re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is not None,
            "SIGNATURE_FORMAT_INVALID")
    current = time.time() if now is None else now
    age = abs(current - int(timestamp))
    require(age <= MAX_AGE_SECONDS, "DISPATCH_EXPIRED")
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        (timestamp + "." + payload_json).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(hmac.compare_digest(expected, signature), "HMAC_SIGNATURE_INVALID")
    try:
        body = json.loads(payload_json)
    except (ValueError, TypeError):
        raise IngestBlocked("PAYLOAD_JSON_INVALID") from None
    require(type(body) is dict and set(body) == {
        "version", "mailbox", "email_message_id", "dispatch_id", "notice"
    }, "PAYLOAD_BODY_SCHEMA_INVALID")
    require(body.get("version") == 1 and body.get("mailbox") == EXPECTED_MAILBOX,
            "MAILBOX_OR_VERSION_BLOCKED")
    email_id = body.get("email_message_id")
    require(type(email_id) is str and re.fullmatch(r"[0-9a-f]{16,24}", email_id) is not None,
            "GMAIL_MESSAGE_ID_INVALID")
    dispatch_id = body.get("dispatch_id")
    require(type(dispatch_id) is str and re.fullmatch(r"[A-Za-z0-9-]{20,64}", dispatch_id) is not None,
            "DISPATCH_ID_INVALID")
    notice = body.get("notice")
    require(type(notice) is dict and notice.get("email_message_id") == email_id,
            "NOTICE_EMAIL_BINDING_INVALID")
    # Reuse the existing strict schema/URL/subject/service/qualification checks.
    synthetic = {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": 66},
        "sender": {"login": OWNER},
        "comment": {
            "user": {"login": OWNER},
            "body": BEGIN + json.dumps(notice, ensure_ascii=False, separators=(",", ":")),
        },
    }
    return synthetic, dispatch_id


def execute(event: dict, env: dict, *, client: httpx.Client) -> dict:
    synthetic, dispatch_id = validate_dispatch(
        event, env.get("SERVICEIT_B2B_INGEST_HMAC_KEY", "")
    )
    webhook = env.get("SERVICEIT_BITRIX_WEBHOOK", "")
    token = env.get("GITHUB_TOKEN", "")
    require(type(webhook) is str and bool(webhook), "BITRIX_WEBHOOK_MISSING")
    require(type(token) is str and len(token) >= 12, "GITHUB_TOKEN_MISSING")
    result = run_once(
        synthetic, client=client, github_token=token, webhook=webhook, live=True
    )
    status = result.get("status")
    allowed = {
        "ONE_TENDER_HOLD_VERIFIED", "ALREADY_RESERVED_DO_NOT_RETRY",
        "ALREADY_EXISTS_IN_CRM",
    }
    require(status in allowed, "B2B_WRITER_RESULT_UNEXPECTED")
    writes = 1 if status == "ONE_TENDER_HOLD_VERIFIED" else 0
    ai_status = result.get("ai_status") if writes else None
    return {
        "status": "SERVICEIT_B2B_INGEST_COMPLETE",
        "result": status,
        "dispatch_id": dispatch_id,
        "crm_writes": writes,
        "qualified_leads": 0,
        "gigachat_attempted": bool(result.get("gigachat_attempted")) if writes else False,
        "ai_status": ai_status if ai_status in (
            "NOT_RUN_SOURCE_UNVERIFIED",
            "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED",
        ) else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    safe = {
        "status": "SERVICEIT_B2B_INGEST_BLOCKED_OR_UNCERTAIN",
        "dispatch_id": None,
        "crm_writes": "UNVERIFIED",
        "qualified_leads": 0,
        "gigachat_attempted": "UNVERIFIED",
    }
    try:
        event_path = Path(os.environ.get("GITHUB_EVENT_PATH", ""))
        require(
            os.environ.get("GITHUB_REPOSITORY") == REPO
            and os.environ.get("GITHUB_EVENT_NAME") == "repository_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and event_path.is_file()
            and event_path.stat().st_size <= MAX_EVENT_BYTES,
            "WORKFLOW_SCOPE_BLOCKED",
        )
        event = json.loads(event_path.read_text(encoding="utf-8"))
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            safe = execute(event, dict(os.environ), client=client)
        args.result.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(safe, ensure_ascii=False), flush=True)
        return 0
    except Exception:
        # If the CRM POST was attempted but readback failed, the result remains
        # explicitly UNVERIFIED; the upstream source lock forbids a blind retry.
        try:
            args.result.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        print(json.dumps(safe, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
