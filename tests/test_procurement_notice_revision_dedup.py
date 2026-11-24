"""Do not lose updates with reused procurement subject and URL."""
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory

from leadpilot.procurement_notice_inbox import audit, read_email


def write(path, body):
    email = EmailMessage()
    email["From"] = "alerts@b2b-center.ru"
    email["To"] = "research@example.org"
    email["Subject"] = "Сопровождение 1С"
    email["Date"] = "Sun, 01 Nov 2026 10:00:00 +0300"
    email.set_content(body)
    path.write_bytes(email.as_bytes())


def test_distinct_revisions_must_not_be_deduplicated():
    with TemporaryDirectory() as root:
        first = Path(root) / "first.eml"
        second = Path(root) / "second.eml"
        url = "https://www.b2b-center.ru/search/number/l4620189-4-12-1/"
        write(first, "Сопровождение 1С " + url + " первый вариант")
        write(second, "Сопровождение 1С " + url + " обновлённый вариант")
        report = audit([first, second])
        assert report["unique_notifications"] == 2
        assert report["qualified_real_leads"] == 0
        assert read_email(first)["email_sha256"] != read_email(second)["email_sha256"]


def test_exact_duplicate_mime_is_ignored():
    with TemporaryDirectory() as root:
        first = Path(root) / "first.eml"
        copy = Path(root) / "copy.eml"
        write(first, "Сопровождение 1С https://www.b2b-center.ru/search/number/l4620189-4-12-1/")
        copy.write_bytes(first.read_bytes())
        report = audit([first, copy])
        assert report["unique_notifications"] == 1
        assert report["qualified_real_leads"] == 0
