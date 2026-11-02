"""No-network Gmail OAuth and ServiceIT v2 worker safety tests."""
import base64
from email.message import EmailMessage
import json

import httpx
import pytest

from leadpilot.serviceit_v2_gmail_api import (
    GmailReadOnly, GmailSourceBlocked, from_environment, GMAIL_API, GOOGLE_TOKEN,
)
from leadpilot.serviceit_v2_store import _observed
from leadpilot.serviceit_v2_worker import main


def fake_mail(subject="Сопровождение 1С:Бухгалтерия"):
    msg = EmailMessage()
    msg["From"] = "B2B-Center <marketplace@b2b-center.ru>"
    msg["To"] = "user@example.org"
    msg["Subject"] = "Приглашение к участию в актуальных закупках"
    msg["Date"] = "Mon, 02 Nov 2026 10:52:07 +0300"
    msg.set_content(
        "Запрос предложений № 4661617\n"
        "Организатор: ООО Ромашка\n"
        "Предмет договора: " + subject + "\n"
        "https://www.b2b-center.ru/market/servis-1s/tender-4661617/\n")
    return msg.as_bytes()


class Transport:
    def __init__(self):
        self.requests = []
        self.mail = fake_mail()

    def __call__(self, request):
        self.requests.append((request.method, str(request.url)))
        if str(request.url).startswith(GOOGLE_TOKEN):
            return httpx.Response(200, json={"access_token": "0123456789ACCESS_TOKEN"})
        if str(request.url).startswith(GMAIL_API):
            if str(request.url).split("?")[0].endswith("/1a19b7630301bafb"):
                return httpx.Response(200, json={
                    "id": "1a19b7630301bafb",
                    "raw": base64.urlsafe_b64encode(self.mail).decode().rstrip("=")})
            if "maxResults" in str(request.url):
                return httpx.Response(200, json={
                    "messages": [{"id": "1a19b7630301bafb"}]})
        pytest.fail("UNEXPECTED_REQUEST")


def source(client):
    return GmailReadOnly(client,
        client_id="google-client-id-long",
        client_secret="google-client-secret-long",
        refresh_token="google-refresh-token-long")


def test_gmail_only_uses_readonly_get_and_refresh_token():
    transport = Transport()
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        emails = list(source(client).recent_messages())
    assert len(emails) == 1
    assert emails[0][0] == "1a19b7630301bafb"
    assert emails[0][1] == transport.mail
    assert [method for method, _ in transport.requests] == ["POST", "GET", "GET"]
    assert "from%3Amarketplace%40b2b-center.ru" in transport.requests[1][1]
    assert "in%3Ainbox" not in transport.requests[1][1]


def test_durable_database_filter_skips_existing_gmail_bodies():
    transport = Transport()
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        out = list(source(client).recent_messages(
            should_fetch=lambda _: False))
    assert out == []
    assert [method for method, _ in transport.requests] == ["POST", "GET"]


def test_credentials_not_printed_and_no_hidden_oauth_guess(monkeypatch, capsys):
    for key in ("SERVICEIT_V2_GMAIL_CLIENT_ID",
                "SERVICEIT_V2_GMAIL_CLIENT_SECRET",
                "SERVICEIT_V2_GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))) as client:
        with pytest.raises(GmailSourceBlocked, match="OAUTH_CREDENTIAL_MISSING"):
            from_environment(client)
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED", "NO")
    assert main(["--once"]) == 2
    assert "SERVICEIT_V2_CORE_BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [
    {"source": "Other"},
    {"tender_id": "1;drop table"},
    {"source_url": "https://evil.example/tender-4661617/"},
])
def test_store_refuses_untrusted_raw_fields(extra):
    row = {
        "source": "B2B-Center", "tender_id": "4661617",
        "source_url": "https://www.b2b-center.ru/market/servis-1s/tender-4661617/",
        "subject": "Сопровождение 1С", "organizer_as_named_in_email": "ООО Ромашка",
        "deadline_as_stated_in_email": None, "source_service_groups": ["1C"],
    }
    row.update(extra)
    with pytest.raises(ValueError):
        _observed(row)


def test_unverified_it_opportunity_never_auto_qualified():
    row = {
        "source": "B2B-Center", "tender_id": "4661617",
        "source_url": "https://www.b2b-center.ru/market/servis-1s/tender-4661617/",
        "subject": "Сопровождение 1С", "organizer_as_named_in_email": "ООО Ромашка",
        "deadline_as_stated_in_email": None, "source_service_groups": ["1C"],
    }
    *_, status, digest = _observed(row)
    assert status == "TENDER_OPPORTUNITY_HOLD"
    assert len(digest) == 64


def test_invalid_gmail_message_budget_rejected():
    transport = Transport()
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(GmailSourceBlocked, match="MESSAGE_LIMIT_INVALID"):
            list(source(client).recent_messages(max_messages=1000))
    assert transport.requests == []


def test_owner_private_bundle_account_checked_before_list(tmp_path, monkeypatch):
    from leadpilot.serviceit_v2_oauth_local import save_owner_secret
    from leadpilot.serviceit_v2_gmail_api import PROFILE_URL
    private=tmp_path/"private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    creds=private/"gmail.json"
    save_owner_secret(creds,{
        "client_id":"google-client-id-long",
        "client_secret":"google-client-secret-long"
    },"google-refresh-token-long","owner@example.org")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_OAUTH_FILE",str(creds))
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL","owner@example.org")
    for name in ("SERVICEIT_V2_GMAIL_CLIENT_ID",
                 "SERVICEIT_V2_GMAIL_CLIENT_SECRET",
                 "SERVICEIT_V2_GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(name,raising=False)
    original=Transport()
    def handler(req):
        if str(req.url)==PROFILE_URL:
            return httpx.Response(200,json={"emailAddress":"owner@example.org"})
        return original(req)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        emails=list(from_environment(client).recent_messages())
    assert len(emails)==1
    assert [method for method,_ in original.requests]==["POST","GET","GET"]


def test_owner_wrong_mailbox_aborts_before_vendor_search(tmp_path,monkeypatch):
    from leadpilot.serviceit_v2_oauth_local import save_owner_secret
    from leadpilot.serviceit_v2_gmail_api import PROFILE_URL
    private=tmp_path/"private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    creds=private/"gmail.json"
    save_owner_secret(creds,{
        "client_id":"google-client-id-long",
        "client_secret":"google-client-secret-long"
    },"google-refresh-token-long","owner@example.org")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_OAUTH_FILE",str(creds))
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL","owner@example.org")
    for name in ("SERVICEIT_V2_GMAIL_CLIENT_ID",
                 "SERVICEIT_V2_GMAIL_CLIENT_SECRET",
                 "SERVICEIT_V2_GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(name,raising=False)
    calls=[]
    def handler(req):
        calls.append(req.method+" "+str(req.url).split("?")[0])
        if str(req.url)==GOOGLE_TOKEN:
            return httpx.Response(200,json={"access_token":"0123456789ACCESS_TOKEN"})
        if str(req.url)==PROFILE_URL:
            return httpx.Response(200,json={"emailAddress":"wrong@example.org"})
        pytest.fail("WRONG_MAILBOX_MUST_NOT_FETCH_MESSAGES")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GmailSourceBlocked,match="GMAIL_WRONG_ACCOUNT_BLOCKED"):
            list(from_environment(client).recent_messages())
    assert len(calls)==2 and calls[0].startswith("POST ") and calls[1].startswith("GET ")


def test_mixed_bundle_and_environment_secrets_denied(tmp_path,monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network"))) as client:
        monkeypatch.setenv("SERVICEIT_V2_GMAIL_OAUTH_FILE",str(tmp_path/"private.json"))
        monkeypatch.setenv("SERVICEIT_V2_GMAIL_REFRESH_TOKEN","unauthorized")
        with pytest.raises(GmailSourceBlocked,match="MIXED_OAUTH"):
            from_environment(client)
