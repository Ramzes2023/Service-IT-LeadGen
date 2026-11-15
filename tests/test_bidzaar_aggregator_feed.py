"""Offline-only checks for documented Bidzaar aggregator purchases GET.
No real ID, service access, or buyer data used in these tests.
"""
import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from leadpilot.bidzaar_aggregator_feed import (
    FeedBlocked, _identifier, _since_utc, build_request, probe, _read_publications,
)

ID = "c0e90758-0f16-4a72-aa83-ea114d103e50"  # fictional local UUID
NOW = datetime(2026, 10, 31, 14, tzinfo=timezone.utc)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body=b"[]"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, length):
        return self.body[:length]


class FakeOpener:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.response


class TestAggregatorAPI(unittest.TestCase):
    def test_documented_endpoint(self):
        request = build_request(ID, now=NOW, page_size=12)
        parts = urlsplit(request.full_url)
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.hostname, "bidzaar.com")
        self.assertEqual(parts.path, f"/api/aggregator_integrator/{ID}/procedures/purchases")
        params = parse_qs(parts.query)
        self.assertEqual(params["PageSize"], ["12"])
        self.assertEqual(params["PageNumber"], ["0"])
        self.assertEqual(params["FromDate"], ["2026-10-30T14:00:00.000Z"])
        self.assertEqual(request.get_method(), "GET")

    def test_old_date(self):
        with self.assertRaisesRegex(FeedBlocked, "FROM_DATE_OUT_OF_SAFE_WINDOW"):
            _since_utc("2026-10-01T00:00:00Z", NOW)

    def test_future_date(self):
        with self.assertRaises(FeedBlocked):
            _since_utc("2026-11-01T00:00:00Z", NOW)

    def test_naive_date(self):
        with self.assertRaisesRegex(FeedBlocked, "FROM_DATE_NEEDS_TIMEZONE"):
            _since_utc("2026-10-30T00:00:00", NOW)

    def test_bad_identifiers(self):
        for candidate in ("", "http://example.com", "/abc", "a" * 36):
            with self.subTest(candidate=candidate), self.assertRaises(FeedBlocked):
                _identifier(candidate)

    def test_page_limits(self):
        for number in (0, 101, True, -4):
            with self.subTest(number=number), self.assertRaises(FeedBlocked):
                build_request(ID, page_size=number, now=NOW)

    def test_no_entitlement_no_http(self):
        opener = FakeOpener()
        with self.assertRaisesRegex(FeedBlocked, "ENTITLEMENT_NOT_CONFIRMED"):
            probe(integrator_id=ID, permission=None, now=NOW, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_missing_id_no_http(self):
        opener = FakeOpener()
        with self.assertRaisesRegex(FeedBlocked, "INTEGRATOR_ID_NOT_ISSUED"):
            probe(integrator_id=None, permission="YES", now=NOW, opener=opener)
        self.assertEqual(opener.requests, [])

    def test_no_raw_tender_or_secret_in_report(self):
        opener = FakeOpener(FakeResponse(
            b'[{"number":123,"secret":"abc"},{"number":124,"secret":"def"}]'
        ))
        report = probe(integrator_id=ID, permission="YES", now=NOW, opener=opener)
        self.assertEqual(report["raw_records_received"], 2)
        self.assertEqual(report["qualified_real_leads"], 0)
        self.assertNotIn("abc", json.dumps(report))
        self.assertNotIn(ID, json.dumps(report))

    def test_html_login_rejected(self):
        response = FakeResponse(b"<html>login</html>")
        response.headers = {"Content-Type": "text/html"}
        with self.assertRaisesRegex(FeedBlocked, "NON_JSON_RESPONSE"):
            _read_publications(build_request(ID, now=NOW), opener=FakeOpener(response))

    def test_wrong_schema_rejected(self):
        with self.assertRaisesRegex(FeedBlocked, "UNVERIFIED_RESPONSE_SCHEMA"):
            _read_publications(build_request(ID, now=NOW),
                               opener=FakeOpener(FakeResponse(b'{"data":[]}')))

    def test_oversize_rejected(self):
        with self.assertRaisesRegex(FeedBlocked, "RESPONSE_TOO_LARGE"):
            _read_publications(build_request(ID, now=NOW),
                               opener=FakeOpener(FakeResponse(b" " * (1024 * 1024 + 1))))

    def test_http_error_redacted(self):
        class ErrorOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 403, "forbidden", {}, None)

        with self.assertRaisesRegex(FeedBlocked, "^ACCESS_OR_RATE_BLOCKED_HTTP_403$") as captured:
            _read_publications(build_request(ID, now=NOW), opener=ErrorOpener())
        self.assertNotIn(ID, str(captured.exception))


if __name__ == "__main__":
    unittest.main()
