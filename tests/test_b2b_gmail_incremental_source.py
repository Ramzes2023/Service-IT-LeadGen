"""Real Gmail IMAP transport contract mocked: no mailbox or CRM writes."""
from email.message import EmailMessage
import json
from pathlib import Path
import sqlite3

import pytest

from leadpilot.b2b_gmail_incremental_source import (
    SourceBlocked, _read_only_mail, ingest_one, review_queue,
    run_poll, _schema,
)


def message(tender_id="4661617", title="Сопровождение 1С:Бухгалтерия"):
    m = EmailMessage()
    m["From"] = "marketplace@b2b-center.ru"
    m["To"] = "owner@example.invalid"
    m["Subject"] = "Приглашение к участию в актуальных закупках"
    m["Date"] = "Mon, 02 Nov 2026 13:15:40 +0300"
    html = (f"<div>Организатор закупки: ООО Тестовый покупатель"
            f"<p>Предмет закупки: {title}</p><p>Актуально до: 16.11.2026 09:00</p>"
            f'<a href="https://www.b2b-center.ru/market/test-source/tender-{tender_id}/'
            f'?message_info=SECRET_TOKEN">Посмотреть процедуру №{tender_id}</a></div>')
    m.set_content(html, subtype="html")
    return m.as_bytes()


class FakeMailbox:
    def __init__(self, messages, *, search_status="OK", login_status="OK"):
        self.messages = dict(messages)
        self.calls = []
        self.search_status = search_status
        self.login_status = login_status

    def login(self, user, password):
        self.calls.append(("login", "SECRET_NOT_LOGGED"))
        assert user == "owner@gmail.com" and password == "secret-app-pass"
        return self.login_status, [b"ok"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        assert mailbox == "INBOX" and readonly is True
        return "OK", [b"50"]

    def uid(self, operation, *args):
        self.calls.append(("uid", operation, args))
        if operation == "SEARCH":
            assert args[1] == "FROM" and args[2] == '"marketplace@b2b-center.ru"'
            assert args[3] == "SINCE"
            return self.search_status, [b" ".join(self.messages)]
        if operation == "FETCH":
            assert args[1] == "(BODY.PEEK[])"
            value = self.messages[args[0]]
            return "OK", [(b"BODY[]", value), b")"]
        pytest.fail("unexpected IMAP op " + operation)

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b"done"]


def location(tmp_path):
    return tmp_path / "ServiceIT" / "B2B-Gmail-Source"


def test_gmail_incremental_two_runs_no_duplicate_and_no_remote_mutation(tmp_path):
    msg = message()
    fake = FakeMailbox({b"77": msg})
    one = run_poll("owner@gmail.com", "secret-app-pass", location(tmp_path),
                   client_factory=lambda: fake)
    assert one["new_emails"] == 1
    assert one["new_tenders"] == 1
    assert one["new_it_observations"] == 1
    assert one["qualified_leads"] == one["crm_writes"] == 0
    two = run_poll("owner@gmail.com", "secret-app-pass", location(tmp_path),
                   client_factory=lambda: fake)
    assert two["duplicate_emails"] == 1
    assert two["new_tenders"] == 0
    assert two["new_it_observations"] == 0
    assert set(x[1] for x in fake.calls if x[0] == "uid") == {"SEARCH", "FETCH"}
    assert ("select", "INBOX", True) in fake.calls
    payload = json.loads((location(tmp_path) / "b2b-review-queue.json").read_text(encoding="utf-8"))
    assert payload["total_review_it"] == 1
    card = payload["items"][0]
    assert card["classification"] == "TENDER_OPPORTUNITY_HOLD"
    assert card["qualified_lead"] is False
    assert card["buyer_inn"] is None
    assert card["fns_msp"] == "NOT_VERIFIED"
    assert "SECRET_TOKEN" not in json.dumps(payload)
    assert not list(location(tmp_path).rglob("*.eml"))


def test_new_mail_version_updates_existing_tender_but_keeps_both_mime_versions(tmp_path):
    m1 = message(title="Сопровождение 1С")
    m2 = message(title="Сопровождение 1С:ЗУП")
    folder = location(tmp_path)
    first = run_poll("owner@gmail.com", "secret-app-pass", folder,
                     client_factory=lambda: FakeMailbox({b"1": m1}))
    second = run_poll("owner@gmail.com", "secret-app-pass", folder,
                      client_factory=lambda: FakeMailbox({b"1": m1, b"2": m2}))
    assert first["new_tenders"] == 1
    assert second["new_emails"] == 1
    assert second["new_tenders"] == 0
    assert second["updated_tenders"] == 1
    with sqlite3.connect(folder/"source-observations.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tender_observations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tender_index").fetchone()[0] == 1


def test_nonit_procurement_kept_in_research_index_but_never_it_or_lead(tmp_path):
    folder = location(tmp_path)
    run_poll("owner@gmail.com", "secret-app-pass", folder,
             client_factory=lambda: FakeMailbox({
                 b"3": message("4661111","Кладка бетонных кирпичей")
             }))
    with sqlite3.connect(folder / "source-observations.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM tender_index").fetchone()[0] == 1
        assert review_queue(db) == []


def test_wrong_sender_rejected_and_never_promoted(tmp_path):
    evil = message().replace(b"marketplace@b2b-center.ru", b"marketplace@evil.invalid")
    result = run_poll("owner@gmail.com", "secret-app-pass", location(tmp_path),
                      client_factory=lambda: FakeMailbox({b"1": evil}))
    assert result["rejected_messages"] == 1
    assert result["new_tenders"] == 0
    assert result["qualified_leads"] == 0


def test_imap_failed_readonly_search_raises_not_mutates():
    fake = FakeMailbox({b"1": message()}, search_status="NO")
    with pytest.raises(SourceBlocked, match="IMAP_SEARCH_FAILED"):
        list(_read_only_mail(fake))
    assert not any(x[0] == "uid" and x[1] in ("STORE","COPY","MOVE","EXPUNGE") for x in fake.calls)


def test_limit_and_state_dir_fail_closed(tmp_path):
    with pytest.raises(SourceBlocked, match="STATE_DIR_NOT_APPROVED"):
        run_poll("owner@gmail.com", "secret-app-pass", tmp_path,
                 client_factory=lambda: pytest.fail("must not connect"))
    with pytest.raises(SourceBlocked, match="GMAIL_AUTH_SECRET_NOT_CONFIGURED"):
        run_poll("owner@gmail.com", "123", location(tmp_path),
                 client_factory=lambda: pytest.fail("must not connect"))
    with pytest.raises(SourceBlocked, match="MESSAGE_BUDGET_INVALID"):
        list(_read_only_mail(FakeMailbox({}), max_messages=501))


def test_source_has_no_crm_or_gmail_mutations():
    import inspect
    from leadpilot import b2b_gmail_incremental_source as source
    s = inspect.getsource(source)
    for forbidden in ('crm.item.add', 'crm.item.update', 'crm.lead.add',
                      'gmail.users.messages.send', 'conn.store(', 'conn.expunge(',
                      'conn.copy(', 'conn.append(', 'sendmail(', 'smtp.'):
        assert forbidden not in s
