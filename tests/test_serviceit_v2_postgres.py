"""REAL ephemeral PostgreSQL integration tests; never touch a production DB."""
from email.message import EmailMessage
import os

import pytest

from leadpilot.serviceit_v2_store import (
    V2StoreBlocked, contains_message, ingest_message, init_schema, read_status,
)
from leadpilot.serviceit_v2_worker import poll_once


def example_mail(title="Сопровождение 1С:ЗУП", include_non_it=True):
    msg = EmailMessage()
    msg["From"] = "B2B Center <marketplace@b2b-center.ru>"
    msg["To"] = "buyer@example.invalid"
    msg["Date"] = "Mon, 02 Nov 2026 10:52:07 +0300"
    msg["Subject"] = "Приглашение к участию в актуальных закупках"
    text = (
        "Запрос предложений № 4661617\n"
        "Организатор: ООО Ромашка\n"
        "Предмет договора: " + title + "\n"
        "https://www.b2b-center.ru/market/soprovozhdenie-1s/tender-4661617/\n"
    )
    if include_non_it:
        text += (
            "Запрос предложений № 4665676\n"
            "Организатор: ООО Тест\n"
            "Предмет договора: Возведение бетонного фундамента\n"
            "https://www.b2b-center.ru/market/betonniy-fundament/tender-4665676/\n"
        )
    msg.set_content(text)
    return msg.as_bytes()


@pytest.fixture
def db():
    if os.environ.get("SERVICEIT_V2_TEST_ENABLE") != "YES":
        pytest.skip("No approved ephemeral PG test")
    dsn = os.environ.get("SERVICEIT_V2_TEST_POSTGRES_DSN", "")
    if "host=127.0.0.1" not in dsn or "dbname=serviceitv2_test" not in dsn:
        pytest.fail("TEST_MUST_NOT_USE_ANY_NON_EPHEMERAL_DATABASE")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as con:
        init_schema(con)
        con.execute("TRUNCATE serviceit_v2_crm_outbox, serviceit_v2_versions, serviceit_v2_opportunities, serviceit_v2_messages, serviceit_v2_runtime CASCADE")
        yield con


def test_mail_versions_replay_and_no_crm_delivery(db):
    one = ingest_message(db, "1a19b7630301bafb", example_mail())
    assert one["new_messages"] == 1 and one["new_opportunities"] == 2
    assert one["it_opportunities_new_or_revised"] == 1
    assert one["out_of_scope"] == 1 and one["crm_writes"] == 0
    assert contains_message(db, "1a19b7630301bafb")
    repeated = ingest_message(db, "1a19b7630301bafb", example_mail())
    assert repeated["duplicate_messages"] == 1
    revised = ingest_message(db, "1a19b7630301babc",
                             example_mail("Внедрение и сопровождение 1С:ЗУП",False))
    assert revised["new_messages"] == 1
    assert revised["revised_opportunities"] == 1
    stats = read_status(db)
    assert stats["total_mail_messages"] == 2
    assert stats["total_distinct_procurements"] == 2
    assert stats["total_notice_versions"] == 3
    assert stats["tender_hold"] == 1
    assert stats["total_crm_outbox_events"] == 0
    assert stats["qualified_leads"] == 0
    assert db.execute("SELECT count(*) FROM serviceit_v2_opportunities WHERE qualified_lead = TRUE").fetchone()[0] == 0


def test_same_message_id_cannot_be_silently_replaced(db):
    ingest_message(db, "1a19b7630301bafb", example_mail())
    with pytest.raises(V2StoreBlocked, match="SAME_GMAIL_ID_DIFFERENT_MIME"):
        ingest_message(db, "1a19b7630301bafb", example_mail("Внедрение 1С"))


def test_fake_sender_fail_closed(db):
    raw = example_mail().replace(b"marketplace@b2b-center.ru", b"evil@unknown-domain.example")
    with pytest.raises(V2StoreBlocked, match="UNAUTHORIZED_VENDOR_SENDER"):
        ingest_message(db, "1a19b7630301babd", raw)
    assert not contains_message(db, "1a19b7630301babd")


class NoNetworkFakeGmail:
    def __init__(self, messages):
        self.messages = messages
        self.requested = []

    def recent_messages(self, *, should_fetch, **kwargs):
        for msg_id, raw in self.messages:
            if should_fetch(msg_id):
                self.requested.append(msg_id)
                yield msg_id, raw


def test_second_poll_skips_already_persisted_body(db):
    ingest_message(db, "1a19b7630301bafb", example_mail())
    gmail = NoNetworkFakeGmail([("1a19b7630301bafb", example_mail()),
                               ("1a19b7630301babe", example_mail("Разработка ПО 1С"))])
    first = poll_once(db, gmail)
    assert first["new_messages"] == 1
    assert gmail.requested == ["1a19b7630301babe"]
    second = poll_once(db, gmail)
    assert second["new_messages"] == 0
    assert gmail.requested == ["1a19b7630301babe"]
    assert read_status(db)["total_crm_outbox_events"] == 0


def test_invalid_vendor_mime_is_persisted_as_visible_retry(db):
    bad = (b"From: B2B <marketplace@b2b-center.ru>\r\n"
           b"Subject: Notice\r\n"
           b"MIME-Version: 1.0\r\n"
           b"Content-Type: application/octet-stream\r\n\r\n"
           b"not-an-email-text-body")
    source = NoNetworkFakeGmail([("1a19b7630301bac0", bad)])
    first = poll_once(db, source)
    assert first["rejected_messages"] == 1
    assert read_status(db)["unresolved_mail_parsing_errors"] == 1
    second = poll_once(db, source)
    assert second["rejected_messages"] == 1
    assert db.execute(
        "SELECT attempts FROM serviceit_v2_message_failures WHERE gmail_message_id=%s",
        ("1a19b7630301bac0",)).fetchone()[0] == 2
    assert read_status(db)["total_crm_outbox_events"] == 0
