"""The inbound subscription channel must never manufacture a qualified lead."""
from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest

from leadpilot.procurement_notice_inbox import NoticeError, audit, read_email


def sample(path, sender="alerts@b2b-center.ru", body=None, html=False):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "operator@example.org"
    m["Date"] = "Sat, 31 Oct 2026 12:00:00 +0300"
    m["Subject"] = "Сопровождение 1С"
    body = body if body is not None else "Закупка 1С https://www.b2b-center.ru/search/number/l4620189-4-12-1/"
    m.set_content(body, subtype="html" if html else "plain")
    path.write_bytes(m.as_bytes())
    return path


class TestVendorNoticeInbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_b2b_notice_is_research_only(self):
        row = read_email(sample(self.root / "x.eml"))
        self.assertEqual(row["source"], "b2b-center")
        self.assertEqual(len(row["original_notice_urls"]), 1)
        self.assertIn("1C", row["service_categories"])
        self.assertEqual(row["qualified_real_leads"], 0)

    def test_sender_spoofed_suffix_rejected(self):
        with self.assertRaisesRegex(NoticeError, "UNRECOGNIZED_SENDER_DOMAIN"):
            read_email(sample(self.root / "x.eml", sender="alerts@b2b-center.ru.attacker.test"))

    def test_wrong_domain_notice_is_not_ingested(self):
        row = read_email(sample(self.root / "x.eml", body="1С https://other.test/lot/26063/"))
        self.assertEqual(row["original_notice_urls"], [])

    def test_suppman(self):
        row = read_email(sample(self.root / "x.eml", sender="notice@suppman.ru",
                                body="Интеграция 1С https://suppman.ru/lot/26063/"))
        self.assertEqual(row["original_notice_urls"], ["https://suppman.ru/lot/26063/"])

    def test_onlinecontract(self):
        row = read_email(sample(self.root / "x.eml", sender="notice@onlc.ru",
                                body="Внедрение CRM https://onlinecontract.ru/tenders/588369"))
        self.assertEqual(len(row["original_notice_urls"]), 1)

    def test_html(self):
        row = read_email(sample(self.root / "x.eml", sender="notice@suppman.team",
                                body='<a href="https://suppman.ru/lot/26063/">1С</a>', html=True))
        self.assertEqual(row["original_notice_urls"], ["https://suppman.ru/lot/26063/"])

    def test_redirect_tracking_link_not_relabelled_as_original(self):
        row = read_email(sample(self.root / "x.eml",
                                body="1С https://www.b2b-center.ru/search/number/l4620189-4-12-1/?utm=ad"))
        self.assertEqual(row["original_notice_urls"], [])

    def test_duplicate_email(self):
        a = sample(self.root / "a.eml")
        b = sample(self.root / "b.eml")
        report = audit([a, b])
        self.assertEqual(report["unique_notifications"], 1)
        self.assertEqual(report["qualified_real_leads"], 0)

    def test_buyer_inn_in_unverified_mail_is_never_qualified(self):
        row = read_email(sample(self.root / "x.eml",
                                body="ИНН 7700000000 1С https://www.b2b-center.ru/search/number/l4620189-4-12-1/"))
        self.assertIn("BUYER_INN_NOT_VERIFIED", row["hold_reasons"])
        self.assertEqual(row["qualified_real_leads"], 0)

    def test_empty_batch(self):
        with self.assertRaises(NoticeError):
            audit([])


if __name__ == "__main__":
    unittest.main()
