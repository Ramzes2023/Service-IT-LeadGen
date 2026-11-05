"""Production corporate STOP configuration preflight.

No customer identifiers, raw manifests, cryptographic digests or secrets are
printed. No CRM writes; this does NOT prove completeness of the stop policy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .serviceit_corporate_stop_inn import (
    ENABLED, validated_manifest, inn_digest, CorporateStopBlocked,
)

REPO = "Ramzes2023/Service-IT-LeadGen"
TRIGGER = "SERVICEIT_CORPORATE_STOP_PREFLIGHT_ONCE_V1"


def check(env):
    assert env.get("GITHUB_REPOSITORY") == REPO, "REPOSITORY_MISMATCH"
    assert env.get("GITHUB_REF") == "refs/heads/main", "BRANCH_MISMATCH"
    assert env.get("GITHUB_EVENT_NAME") == "issue_comment", "EVENT_MISMATCH"
    path = Path(env.get("GITHUB_EVENT_PATH", "/dev/null"))
    assert path.is_file() and 0 < path.stat().st_size <= 256000, "EVENT_FILE_MISSING"
    event = json.loads(path.read_text("utf-8"))
    assert event.get("action") == "created", "ACTION_INVALID"
    assert (event.get("repository") or {}).get("full_name") == REPO, "REPOSITORY_EVENT_INVALID"
    assert (event.get("issue") or {}).get("number") == 200, "ISSUE_INVALID"
    assert not (event.get("issue") or {}).get("pull_request"), "ISSUE_IS_PR"
    assert (event.get("sender") or {}).get("login") == "Ramzes2023", "ACTOR_INVALID"
    assert (event.get("comment") or {}).get("body") == TRIGGER, "MARKER_INVALID"
    enabled = env.get("SERVICEIT_CORPORATE_STOP_INN_ENABLED", "") == ENABLED
    manifest = env.get("SERVICEIT_CORPORATE_STOP_INN_MANIFEST", "")
    secret = env.get("SERVICEIT_CORPORATE_STOP_INN_KEY", "")
    if not enabled:
        return {
            "status": "STOP_NOT_ENABLED",
            "enabled": False, "manifest_valid": False,
            "nonempty_manifest": False, "key_valid": False,
            "real_card_stop_match_proven": False,
            "all_channel_stop_clearance_proven": False, "crm_writes": 0,
        }
    try:
        items = validated_manifest(manifest)
        # Ensure key can compute the same scheme without exposing any hash.
        inn_digest("7707083893", secret)
        return {
            "status": ("STOP_HMAC_MANIFEST_CONFIGURED"
                       if items else "STOP_ENABLED_BUT_EMPTY_MANIFEST"),
            "enabled": True, "manifest_valid": True,
            "nonempty_manifest": bool(items), "key_valid": True,
            "real_card_stop_match_proven": False,
            "all_channel_stop_clearance_proven": False, "crm_writes": 0,
        }
    except CorporateStopBlocked:
        return {
            "status": "STOP_CONFIG_INVALID_HOLD",
            "enabled": True, "manifest_valid": False,
            "nonempty_manifest": False, "key_valid": False,
            "real_card_stop_match_proven": False,
            "all_channel_stop_clearance_proven": False, "crm_writes": 0,
        }


def main():
    try:
        result = check(dict(os.environ))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "STOP_PREFLIGHT_BLOCKED",
                          "all_channel_stop_clearance_proven": False,
                          "crm_writes": 0}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
