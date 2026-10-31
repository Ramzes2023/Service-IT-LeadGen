"""Synthetic offline tests: NEVER construed as live EIS availability proof."""
from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.eis_rss import _url, parse_feed, fetch, save

TODAY = date(2026, 10, 31)
FEED = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C"
NOTICE = "https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber=01234567890123456789"
TITLE = "Оказание услуг по сопровождению 1С:Предприятие"


def rss(items=None):
    if items is None:
        items = [(TITLE, NOTICE, "Fri, 30 Oct 2026 11:00:00 +0300")]
    tags = "".join(f"<item><title>{title}</title><link>{link}</link>"
                   f"<pubDate>{when}</pubDate><description>Нужен внешний подрядчик по системе 1С</description></item>"
                   for title, link, when in items)
    return ("<?xml version='1.0' encoding='utf-8'?><rss version='2.0'><channel>"
            + tags + "</channel></rss>").encode("utf-8")


def test_parses_explicit_service_but_never_qualifies():
    result = parse_feed(rss(), feed_url=FEED, today=TODAY)
    assert result["counts"]["candidates_for_identity_review"] == 1
    item = result["items"][0]
    assert item["source_url"] == NOTICE
    assert item["buyer_inn"] is None and item["lead_score"] is None
    assert item["crm_ready"] is False and item["outbound_authorized"] is False
    assert item["buyer_fns_size"] == "UNVERIFIED"
    assert result["counts"]["qualified_small_business_leads"] == 0


def test_duplicate_same_notice_keeps_one():
    one = (TITLE, NOTICE, "Fri, 30 Oct 2026 11:00:00 +0300")
    r = parse_feed(rss([one, one]), feed_url=FEED, today=TODAY)
    assert r["counts"]["duplicate_notices"] == 1
    assert len(r["items"]) == 1


def test_old_notice_is_excluded():
    r = parse_feed(rss([(TITLE, NOTICE, "Tue, 01 Sep 2026 11:00:00 +0300")]), feed_url=FEED, today=TODAY)
    assert r["counts"]["stale_rejected"] == 1
    assert r["items"] == []


def test_undated_notice_is_held_not_active():
    r = parse_feed(rss([(TITLE, NOTICE, "")]), feed_url=FEED, today=TODAY)
    assert r["items"][0]["research_status"] == "DATE_UNKNOWN_HOLD"
    assert r["items"][0]["published_on"] is None


def test_future_notice_is_excluded():
    r = parse_feed(rss([(TITLE, NOTICE, "Tue, 03 Nov 2026 11:00:00 +0300")]), feed_url=FEED, today=TODAY)
    assert r["counts"]["future_date_rejected"] == 1


@pytest.mark.parametrize("title", [
    "Поставка компьютеров, рабочих станций и мониторов",
    "Оказание услуг по сопровождению 1С-Битрикс24",
    "Найм специалиста 1С в штат",
    "Поставка лицензий без услуг сопровождения 1С",
])
def test_non_external_service_excluded(title):
    r = parse_feed(rss([(title, NOTICE, "Fri, 30 Oct 2026 11:00:00 +0300")]), feed_url=FEED, today=TODAY)
    assert len(r["items"]) == 0


@pytest.mark.parametrize("link", [
    "http://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber=01234567890123456789",
    "https://zakupki.gov.ru.evil.com/epz/order/notice/ea44/view/common-info.html?regNumber=01234567890123456789",
    "https://user:password@zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber=01234567890123456789",
    "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=1C",
])
def test_bad_notice_never_converted_to_research_candidate(link):
    r = parse_feed(rss([(TITLE, link.replace('&','&amp;'), "Fri, 30 Oct 2026 11:00:00 +0300")]), feed_url=FEED, today=TODAY)
    assert r["items"] == []
    assert r["counts"]["unverified_notice_link"] == 1


@pytest.mark.parametrize("feed", [
    "https://example.net/epz/order/extendedsearch/rss?searchString=x",
    "http://zakupki.gov.ru/epz/order/extendedsearch/rss?searchString=x",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss",
    "https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber=12345678901234567890",
])
def test_invalid_feed_rejected_before_fetch(feed):
    with pytest.raises(ValueError):
        _url(feed, feed=True)


def test_unsafe_entities_rejected():
    payload = b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item>&xxe;</item></channel></rss>'
    with pytest.raises(ValueError, match="EIS_FEED_XML_INVALID"):
        parse_feed(payload, feed_url=FEED, today=TODAY)


def test_bad_schema_fails_closed():
    with pytest.raises(ValueError, match="FORMAT_UNKNOWN"):
        parse_feed(b"<html><title>Fake</title></html>", feed_url=FEED, today=TODAY)


def test_fetch_one_bounded_get_without_redirects():
    called = []
    def handler(req):
        called.append(req.url)
        return httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=rss())
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = fetch(url=FEED, client=client, today=TODAY)
    assert len(called) == 1
    assert result["counts"]["candidates_for_identity_review"] == 1


def test_redirect_is_fail_closed():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example/out"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(RuntimeError, match="UNAVAILABLE_OR_REDIRECT"):
            fetch(url=FEED, client=client, today=TODAY)


def test_html_challenge_is_not_empty_feed():
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
                       headers={"content-type": "text/html"}, text="captcha"))) as client:
        with pytest.raises(RuntimeError, match="NOT_XML"):
            fetch(url=FEED, client=client, today=TODAY)


def test_large_body_rejected():
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,
                       headers={"content-type": "application/xml"}, content=b"A" * (2 * 1024 * 1024 + 1)))) as client:
        with pytest.raises(RuntimeError, match="TOO_LARGE"):
            fetch(url=FEED, client=client, today=TODAY)


def test_safe_json_snapshot_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = parse_feed(rss(), feed_url=FEED, today=TODAY)
    out = save(r, "exports/a.json")
    assert json.loads(out.read_text(encoding="utf-8"))["crm_writes"] == "NO"
    with pytest.raises(ValueError, match="EXPORTS"):
        save(r, "exports/a.json")
    with pytest.raises(ValueError, match="EXPORTS"):
        save(r, "data/a.json")
