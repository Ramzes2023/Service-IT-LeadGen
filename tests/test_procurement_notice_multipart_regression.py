"""Regression: multipart vendor notification must retain HTML-only official links."""
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from leadpilot.procurement_notice_inbox import read_email


def test_multipart_official_link_is_not_lost():
    mail = EmailMessage()
    mail["From"] = "alerts@b2b-center.ru"
    mail["To"] = "test@example.org"
    mail["Subject"] = "1С"
    mail.set_content("Пришла новая закупка по сопровождению 1С.")
    mail.add_alternative(
        '<a href="https://www.b2b-center.ru/search/number/l4620189-4-12-1/">Открыть закупку</a>',
        subtype="html",
    )
    with TemporaryDirectory() as temp:
        path = Path(temp) / "notice.eml"
        path.write_bytes(mail.as_bytes())
        result = read_email(path)
    assert len(result["original_notice_urls"]) == 1
    assert result["status"] == "HOLD_UNVERIFIED_EMAIL_NOTICE"
    assert result["qualified_real_leads"] == 0
