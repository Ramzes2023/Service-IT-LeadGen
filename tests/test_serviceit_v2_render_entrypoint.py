"""No-data, no-network tests for guarded Render boot."""
import base64
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from leadpilot.serviceit_v2_render_entrypoint import configure, probe_database, MAILBOX


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


def test_db_probe_off_by_default_does_not_connect(monkeypatch):
    monkeypatch.delenv("SERVICEIT_V2_DB_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SERVICEIT_V2_DB_EXPECTED_NAME", raising=False)
    assert probe_database() == "NOT_CONFIGURED"


def test_db_probe_synthetic_readonly_and_name_match(monkeypatch):
    queries = []
    class FakeConnection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql):
            queries.append(sql)
            return SimpleNamespace(fetchone=lambda: ("serviceit_v2_gmail_canary_db",))
    def connect(dsn, *, connect_timeout):
        assert dsn == "postgresql://synthetic-credentials-placeholder/db"
        assert connect_timeout == 5
        return FakeConnection()
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN",
                       "postgresql://synthetic-credentials-placeholder/db")
    monkeypatch.setenv("SERVICEIT_V2_DB_EXPECTED_NAME",
                       "serviceit_v2_gmail_canary_db")
    assert probe_database() == "PASS"
    assert queries == ["SELECT current_database()"]


def test_db_probe_wrong_database_fail_closed(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql):
            assert sql == "SELECT current_database()"
            return SimpleNamespace(fetchone=lambda: ("other_company",))
    monkeypatch.setitem(sys.modules, "psycopg",
                        SimpleNamespace(connect=lambda *a, **k: FakeConnection()))
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN", "postgresql://synthetic/db")
    monkeypatch.setenv("SERVICEIT_V2_DB_EXPECTED_NAME",
                       "serviceit_v2_gmail_canary_db")
    assert probe_database() == "WRONG_DATABASE"


def test_db_probe_rejects_invalid_expected_name_and_redacts_errors(monkeypatch):
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN", "postgresql://private:fake-secret@invalid/db")
    monkeypatch.setenv("SERVICEIT_V2_DB_EXPECTED_NAME", "../../invalid")
    assert probe_database() == "INVALID_EXPECTED_NAME"
    monkeypatch.setenv("SERVICEIT_V2_DB_EXPECTED_NAME",
                       "serviceit_v2_gmail_canary_db")
    def failure(*args, **kwargs):
        raise RuntimeError("do not expose postgresql://private:fake-secret@invalid/db")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=failure))
    assert probe_database() == "CONNECTION_FAILED"
