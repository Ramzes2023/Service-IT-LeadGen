"""STOP preflight only reports sanitized configuration status."""
import json

from leadpilot.serviceit_corporate_stop_inn import (
    ENABLED, make_approved_manifest,
)
from leadpilot.serviceit_corporate_stop_preflight import check, TRIGGER

def owner_env(tmp_path, **extra):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({
        "action": "created",
        "repository": {"full_name": "Ramzes2023/Service-IT-LeadGen"},
        "issue": {"number": 200},
        "sender": {"login": "Ramzes2023"},
        "comment": {"body": TRIGGER},
    }), encoding="utf8")
    return {
        "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_EVENT_PATH": str(p),
        **extra,
    }

def test_disabled_does_not_claim_clear(tmp_path):
    result = check(owner_env(tmp_path))
    assert result["status"] == "STOP_NOT_ENABLED"
    assert result["all_channel_stop_clearance_proven"] is False

def test_enabled_missing_secrets_fails_closed(tmp_path):
    result = check(owner_env(tmp_path,
        SERVICEIT_CORPORATE_STOP_INN_ENABLED=ENABLED))
    assert result["status"] == "STOP_CONFIG_INVALID_HOLD"
    assert result["all_channel_stop_clearance_proven"] is False

def test_valid_nonempty_manifest_status_exposes_no_inn(tmp_path):
    key = "a" * 48
    manifest = make_approved_manifest(["7707083893"], key)
    result = check(owner_env(tmp_path,
        SERVICEIT_CORPORATE_STOP_INN_ENABLED=ENABLED,
        SERVICEIT_CORPORATE_STOP_INN_MANIFEST=json.dumps(manifest),
        SERVICEIT_CORPORATE_STOP_INN_KEY=key))
    assert result["status"] == "STOP_HMAC_MANIFEST_CONFIGURED"
    assert result["nonempty_manifest"] is True
    assert result["real_card_stop_match_proven"] is False
    assert "7707083893" not in json.dumps(result)
    assert key not in json.dumps(result)

def test_empty_manifest_does_not_mean_configured(tmp_path):
    key = "b" * 48
    manifest = make_approved_manifest([], key)
    result = check(owner_env(tmp_path,
        SERVICEIT_CORPORATE_STOP_INN_ENABLED=ENABLED,
        SERVICEIT_CORPORATE_STOP_INN_MANIFEST=json.dumps(manifest),
        SERVICEIT_CORPORATE_STOP_INN_KEY=key))
    assert result["status"] == "STOP_ENABLED_BUT_EMPTY_MANIFEST"
    assert result["all_channel_stop_clearance_proven"] is False
