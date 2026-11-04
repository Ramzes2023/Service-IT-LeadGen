"""Offline acceptance for approved read-only Mail.ru notice transport."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from leadpilot.b2b_mailru_notifications import (
    MailTransportBlocked, fetch_research,
)

NOW = datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)
APP_ENV = {
    "SERVICEIT_MAILRU_READONLY_AUTHORIZED": "YES",
    "SERVICEIT_MAILRU_USER": "operator@mail.ru",
    "SERVICEIT_MAILRU_APP_PASSWORD": "made-up-local-secret",
}
URL = "https://www.b2b-center.ru/search/number/l4620189-4-12-1/"


def _message(sender, body, subject="Сопровождение 1С"):
    email = EmailMessage()
    email["From"] = sender
    email["To"] = "operator@mail.ru"
    email["Subject"] = subject
    email["Date"] = "Sun, 01 Nov 2026 10:00:00 +0300"
    email.set_content(body)
    return email.as_bytes()


class FakeIMAP:
    def __init__(self, host, port, ssl_context, timeout):
        assert host == "imap.mail.ru"
        assert port == 993
        assert timeout == 15
        self.payloads = {
            b"1": _message("alerts@b2b-center.ru", "1С " + URL + " версия 1"),
            b"2": _message("alerts@b2b-center.ru", "1С " + URL + " версия 2"),
            b"3": _message("outsider@example.org", "1С " + URL),
        }
        self.calls = []
        self.closed = False

    def login(self, user, password):
        self.calls.append("LOGIN")
        assert user == APP_ENV["SERVICEIT_MAILRU_USER"]
        assert password == APP_ENV["SERVICEIT_MAILRU_APP_PASSWORD"]
        return "OK", [b""]

    def select(self, mailbox, readonly=False):
        assert mailbox == "INBOX"
        assert readonly is True
        self.calls.append("READONLY")
        return "OK", [b"3"]

    def uid(self, action, *args):
        self.calls.append((action, args))
        if action == "SEARCH":
            assert args[0] is None and args[1] == "SINCE"
            return "OK", [b"1 2 3"]
        if action == "FETCH":
            assert args[1] == "(BODY.PEEK[])"
            return "OK", [(b"1 FETCH (BODY[] {100})", self.payloads[args[0]]), b")"]
        raise AssertionError(action)

    def logout(self):
        self.closed = True


def test_no_mailbox_authorization_never_connects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("MUST_NOT_CONNECT")
    with pytest.raises(MailTransportBlocked, match="MAILBOX_OWNER_AUTHORIZATION_REQUIRED"):
        fetch_research(env={}, imap_factory=must_not_be_called)


def test_real_readonly_imap_contract_and_distinct_updates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    instances = []
    def factory(*args, **kwargs):
        obj = FakeIMAP(*args, **kwargs)
        instances.append(obj)
        return obj
    result = fetch_research(env=APP_ENV, imap_factory=factory, now=NOW)
    assert result["message_count_checked"] == 3
    assert result["new_candidate_observations"] == 2
    assert all(x["original_notice_urls"] == [URL] for x in result["observations"])
    assert result["qualified_real_leads"] == 0
    assert result["crm_writes"] == "NO"
    assert instances[0].closed
    assert ("FETCH", (b"1", "(BODY.PEEK[])")) in instances[0].calls
    # A new polling run must not re-create the same procurement observations.
    again = fetch_research(env=APP_ENV, imap_factory=factory, now=NOW + timedelta(seconds=1))
    assert again["new_candidate_observations"] == 0
    assert again["qualified_real_leads"] == 0


def test_user_and_special_app_password_required(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MailTransportBlocked, match="MAILRU_ACCOUNT_UNCONFIGURED"):
        fetch_research(env={
            "SERVICEIT_MAILRU_READONLY_AUTHORIZED": "YES",
            "SERVICEIT_MAILRU_USER": "operator@gmail.com",
            "SERVICEIT_MAILRU_APP_PASSWORD": "temporary-secret",
        })
    with pytest.raises(MailTransportBlocked, match="MAILRU_EXTERNAL_APP_PASSWORD_REQUIRED"):
        fetch_research(env={
            "SERVICEIT_MAILRU_READONLY_AUTHORIZED": "YES",
            "SERVICEIT_MAILRU_USER": "operator@mail.ru",
        })


def test_does_not_accept_unbounded_output_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MailTransportBlocked, match="OUTPUT_MUST_BE_LOCAL_EXPORTS"):
        fetch_research(env=APP_ENV, output_dir="not-exports")
