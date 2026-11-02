"""No-data, no-network tests for guarded Render boot."""
import base64
import os
from pathlib import Path

import pytest
from leadpilot.serviceit_v2_render_entrypoint import configure, MAILBOX


def test_default_off_never_requires_credentials(monkeypatch):
    for name in (
        "SERVICEIT_V2_APPS_RECEIVER_ENABLED",
        "SERVICEIT_V2_DB_DSN",
        "DATABASE_URL",
        "SERVICEIT_V2_APPS_HMAC_SECRET_B64",
        "SERVICEIT_V2_APPS_HMAC_SECRET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    assert configure() == "OFF"
    assert os.environ["SERVICEIT_V2_APPS_RECEIVER_ENABLED"] == "NO"


def test_invalid_switch_fails_closed(monkeypatch):
    monkeypatch.setenv("SERVICEIT_V2_APPS_RECEIVER_ENABLED", "TRUE")
    with pytest.raises(ValueError, match="INVALID_SWITCH"):
        configure()


def test_missing_permissions_fails_before_secret_write(monkeypatch):
    monkeypatch.setenv("SERVICEIT_V2_APPS_RECEIVER_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL", "someone@example.org")
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN", "postgresql://example/db")
    monkeypatch.setenv("SERVICEIT_V2_APPS_HMAC_SECRET_B64",
                       base64.b64encode(b"Z" * 48).decode())
    with pytest.raises(ValueError, match="INCORRECT_MAILBOX"):
        configure()


def test_private_owner_hmac_in_memory_to_0600_file(monkeypatch):
    monkeypatch.setenv("SERVICEIT_V2_APPS_RECEIVER_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL", MAILBOX)
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN", "postgresql://synthetic/db")
    monkeypatch.delenv("SERVICEIT_V2_APPS_HMAC_SECRET_FILE", raising=False)
    private = b"Z" * 48
    monkeypatch.setenv("SERVICEIT_V2_APPS_HMAC_SECRET_B64",
                       base64.b64encode(private).decode("ascii"))
    assert configure() == "CONFIGURED"
    target = Path(os.environ["SERVICEIT_V2_APPS_HMAC_SECRET_FILE"])
    try:
        assert target.read_bytes() == private
        if os.name == "posix":
            assert target.stat().st_mode & 0o077 == 0
            assert target.stat().st_uid == os.geteuid()
        assert "SERVICEIT_V2_APPS_HMAC_SECRET_B64" not in os.environ
    finally:
        target.unlink()


def test_wrong_hmac_or_two_sources_fails_closed(monkeypatch):
    monkeypatch.setenv("SERVICEIT_V2_APPS_RECEIVER_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL", MAILBOX)
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN", "postgresql://synthetic/db")
    monkeypatch.setenv("SERVICEIT_V2_APPS_HMAC_SECRET_B64", "@@not-b64@@")
    monkeypatch.delenv("SERVICEIT_V2_APPS_HMAC_SECRET_FILE", raising=False)
    with pytest.raises(ValueError, match="INVALID_HMAC_ENCODING"):
        configure()
    monkeypatch.setenv("SERVICEIT_V2_APPS_HMAC_SECRET_FILE", "/some/path")
    with pytest.raises(ValueError, match="HMAC_SOURCE_NOT_UNIQUE"):
        configure()
