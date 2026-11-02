"""HMAC authenticated Apps Script → real disposable PostgreSQL, no CRM effects."""
from datetime import datetime
from email.message import EmailMessage
from hashlib import sha256
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi.testclient import TestClient
import pytest

from leadpilot.serviceit_v2_apps_receiver import (
    app, _auth, _payload, process_signed_message, IngressBlocked,
)
from leadpilot.serviceit_v2_store import init_schema

MID = "1a19b7630301bafb"
EMAIL = "ramzesisayev444@gmail.com"
SECRET = "SYNTHETIC-APPS-HMAC-NO-REAL-CREDENTIAL-123456789-ABC"


def mime(*, sender="marketplace@b2b-center.ru", subject="Сопровождение 1С"):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = EMAIL
    m["Subject"] = "Актуальные торги по вашему профилю"
    m["Date"] = "Mon, 02 Nov 2026 10:52:07 +0300"
    m.set_content(
        "Запрос предложений № 4661617\n"
        "Организатор: АО Техническая инспекция ЕЭС\n"
        "Предмет договора: " + subject + "\n"
        "https://www.b2b-center.ru/market/soprovozhdenie-1s/tender-4661617/\n"
    )
    return m.as_bytes()


def packet(*, mid=MID, source=None, email=EMAIL):
    raw = source if source is not None else mime()
    return {
        "version": "serviceit-appsscript-v1",
        "mailbox": email,
        "gmail_message_id": mid,
        "raw_b64url": base64.urlsafe_b64encode(raw).decode().rstrip("="),
    }


def signed(payload, *, secret=SECRET, when=None, tamper=False):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = str(int(time.time()) if when is None else when)
    sig = hmac.new(secret.encode(), ts.encode()+b"."+raw, hashlib.sha256).hexdigest()
    if tamper:
        sig = "f"*64
    return raw, {"X-ServiceIT-Timestamp": ts,
                 "X-ServiceIT-Signature": "sha256="+sig,
                 "Content-Type":"application/json"}


@pytest.fixture
def configured(monkeypatch,tmp_path):
    target=tmp_path/"receiver-hmac"
    target.write_text(SECRET,encoding="ascii")
    target.chmod(0o600)
    monkeypatch.setenv("SERVICEIT_V2_APPS_HMAC_SECRET_FILE",str(target))
    monkeypatch.setenv("SERVICEIT_V2_APPS_RECEIVER_ENABLED","YES")
    monkeypatch.setenv("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL",EMAIL)
    return target


def test_hmac_validates_unchanged_bytes(configured):
    body,headers=signed(packet())
    _auth({k.lower():v for k,v in headers.items()},body)
    mid,decoded=_payload(body)
    assert mid == MID
    assert decoded == mime()


@pytest.mark.parametrize("change", ["tamper","expired","bad-header"])
def test_authentication_denies_tampering_and_expired_dates(configured,change):
    body,headers=signed(packet(),tamper=(change=="tamper"),
                         when=(time.time()-601 if change=="expired" else None))
    if change=="bad-header":
        headers["X-ServiceIT-Signature"]="sha256=bad"
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code == 403
    assert r.json()["status"] == "INGRESS_REJECTED"


def test_missing_secret_fails_closed(configured,monkeypatch):
    body,headers=signed(packet())
    monkeypatch.delenv("SERVICEIT_V2_APPS_HMAC_SECRET_FILE")
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code == 503


def test_wrong_mailbox_never_reaches_db(configured):
    body,headers=signed(packet(email="somebody-else@gmail.com"))
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code == 403


@pytest.mark.parametrize("bad", [
    {"raw_b64url":"!bad!"},
    {"gmail_message_id":"../secret"},
    {"version":"serviceit-appsscript-v999"},
])
def test_invalid_packet_rejected_without_db(configured,bad):
    p=packet()
    p.update(bad)
    body,headers=signed(p)
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code == 403


def test_private_key_file_must_be_private(configured):
    configured.chmod(0o644)
    body,headers=signed(packet())
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code == 403


@pytest.fixture
def disposable_db(configured,monkeypatch):
    if os.environ.get("SERVICEIT_V2_TEST_ENABLE") != "YES":
        pytest.skip("No ephemeral PostgreSQL configured")
    dsn=os.environ.get("SERVICEIT_V2_TEST_POSTGRES_DSN","")
    if "host=127.0.0.1" not in dsn or "dbname=serviceitv2_test" not in dsn:
        pytest.fail("PRODUCTION_DB_FORBIDDEN")
    psycopg=pytest.importorskip("psycopg")
    monkeypatch.setenv("SERVICEIT_V2_DB_DSN",dsn)
    with psycopg.connect(dsn,autocommit=True) as db:
        init_schema(db)
        db.execute("""TRUNCATE serviceit_v2_buyer_reviews,serviceit_v2_crm_outbox,
                       serviceit_v2_versions,serviceit_v2_opportunities,
                       serviceit_v2_messages,serviceit_v2_message_failures,
                       serviceit_v2_runtime CASCADE""")
        yield db


def test_ingress_commits_once_and_reports_duplicate(disposable_db):
    body,headers=signed(packet())
    with TestClient(app) as client:
        first=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
        second=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert first.status_code==202,first.text
    assert first.json()["status"]=="INGESTED"
    assert first.json()["new_opportunities"]==1
    assert second.status_code==200
    assert second.json()["status"]=="ALREADY_INGESTED"
    for result in (first,second):
        assert result.json()["gmail_message_id"]==MID
        assert result.json()["crm_writes"]==result.json()["qualified_leads"]==0
    assert disposable_db.execute("SELECT count(*) FROM serviceit_v2_messages").fetchone()[0]==1
    assert disposable_db.execute("SELECT count(*) FROM serviceit_v2_opportunities").fetchone()[0]==1
    assert disposable_db.execute("SELECT count(*) FROM serviceit_v2_crm_outbox").fetchone()[0]==0


def test_spoofed_from_is_not_accepted_even_with_signed_body(disposable_db):
    body,headers=signed(packet(source=mime(sender="hacker@evil.example")))
    with TestClient(app) as client:
        r=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
    assert r.status_code==403
    assert disposable_db.execute("SELECT count(*) FROM serviceit_v2_messages").fetchone()[0]==0


def test_same_gmail_id_with_changed_mime_does_not_overwrite(disposable_db):
    body,headers=signed(packet())
    changed,headers2=signed(packet(source=mime(subject="Изменённый предмет 1С")))
    with TestClient(app) as client:
        a=client.post("/api/v2/gmail/ingest",content=body,headers=headers)
        b=client.post("/api/v2/gmail/ingest",content=changed,headers=headers2)
    assert a.status_code==202
    assert b.status_code==403
    assert disposable_db.execute("SELECT count(*) FROM serviceit_v2_messages").fetchone()[0]==1
