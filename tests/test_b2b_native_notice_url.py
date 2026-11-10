"""Original native B2B-Center market-next links are accepted; outsiders are not."""
from leadpilot.procurement_notice_inbox import _notice_url


def test_native_market_next_and_legacy_urls():
    url = "https://www.b2b-center.ru/app/market-next/maintenance-of-1c/tender-4662389/"
    assert _notice_url(url, "b2b-center") == url
    assert _notice_url("https://www.b2b-center.ru/search/number/l4662389-4-12-1/", "b2b-center")


def test_foreign_and_query_bearing_urls_rejected():
    assert _notice_url("https://attacker.test/app/market-next/a/tender-4662389/", "b2b-center") is None
    assert _notice_url("https://www.b2b-center.ru/app/market-next/a/tender-4662389/?token=secret", "b2b-center") is None
    assert _notice_url("https://www.b2b-center.ru/app/market-next/a/tender-4662389/", "onlinecontract") is None
