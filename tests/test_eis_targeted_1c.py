"""Bounded search for 1C service demand; synthetic feeds, zero CRM writes."""
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from leadpilot import eis_targeted_1c as t
from leadpilot.eis_rss import SOURCE_NAME

ORIGINAL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1%D0%A1&fz44=on"
NOTICE = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber="


def fixture(ordinal=1, count=1):
    items = [{
        "source_url": NOTICE + str(10000000000000000000 + ordinal),
        "buyer_inn": None, "crm_ready": False, "outbound_authorized": False,
        "title": "Synthetic eligible 1C service notice",
    }] if count == 1 else []
    return {
        "source": SOURCE_NAME, "mode": "EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        "crm_writes": "NO", "outbound_messages": "NO",
        "counts": {"items_seen": count,
                   "candidates_for_identity_review": len(items),
                   "qualified_small_business_leads": 0, "crm_ready": 0},
        "items": items,
    }


def test_only_changes_original_search_string():
    for _, phrase in t.SEARCH_PHRASES:
        url = t.targeted_url(ORIGINAL, phrase)
        params = parse_qs(urlsplit(url).query)
        assert params["searchString"] == [phrase]
        assert params["fz44"] == ["on"]
        assert urlsplit(url).hostname == "zakupki.gov.ru"
    assert len(t.SEARCH_PHRASES) == 4


@pytest.mark.parametrize("url", [
    "https://evil.example/epz/order/extendedsearch/rss.html?searchString=1C",
    "http://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?fz44=on",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C&searchString=1C",
])
def test_forbidden_or_ambiguous_sources(url):
    with pytest.raises(ValueError):
        t.targeted_url(url, t.SEARCH_PHRASES[0][1])


def test_unapproved_query_is_not_accepted():
    with pytest.raises(ValueError, match="UNAPPROVED"):
        t.targeted_url(ORIGINAL, "anything")


def test_four_queries_one_request_each_and_de_duplicate():
    visits = []
    def mock_download(*, url, today):
        visits.append(url)
        if len(visits) < 3:
            return fixture(ordinal=1)
        return fixture(ordinal=len(visits))
    output = t.run_profiles(ORIGINAL, today=date(2026, 10, 31),
                            downloader=mock_download)
    assert len(visits) == 4
    assert len(set(visits)) == 4
    assert output["counts"]["unique_research_notices"] == 3
    assert output["counts"]["qualified_leads"] == 0
    assert output["crm_writes"] == "NO" and output["outbound_messages"] == "NO"
    assert output["buyer_fns_status"] == "MANUAL_EXACT_INN_REQUIRED"
    assert [x["new_unique_notices"] for x in output["profiles"]] == [1, 0, 1, 1]


def test_empty_source_report_does_not_raise():
    result = t.run_profiles(ORIGINAL, downloader=lambda **kwargs: fixture(count=0))
    assert result["counts"]["unique_research_notices"] == 0
    assert all(p["items_seen"] == 0 for p in result["profiles"])


@pytest.mark.parametrize("key,value", [
    ("crm_writes", "YES"), ("outbound_messages", "YES"),
    ("source", "FAKE"), ("mode", "AUTO_LEAD_APPROVED")
])
def test_untrusted_report_rejected(key, value):
    def fake(**kwargs):
        obj = fixture(count=0)
        obj[key] = value
        return obj
    with pytest.raises(ValueError, match="UNTRUSTED"):
        t.run_profiles(ORIGINAL, downloader=fake)


def test_unsupported_buyer_approval_is_blocked():
    def fake(**kwargs):
        obj = fixture()
        obj["items"][0]["crm_ready"] = True
        return obj
    with pytest.raises(ValueError, match="PREAPPROVED"):
        t.run_profiles(ORIGINAL, downloader=fake)


def test_44_223_not_assumed_moscow_or_small_business():
    result = t.run_profiles(ORIGINAL, downloader=lambda **kwargs: fixture())
    assert result["regions_covered"] == "UNVERIFIED_FROM_RSS_LINK"
    assert result["counts"]["crm_ready"] == 0
    assert result["counts"]["unique_research_notices"] == 1


def test_429_stops_search_immediately_without_retry():
    requests = []
    def unavailable(*, url, today):
        requests.append(url)
        raise RuntimeError("EIS_HTTP_RATE_LIMIT_429")
    with pytest.raises(RuntimeError, match="RATE_LIMIT"):
        t.run_profiles(ORIGINAL, downloader=unavailable)
    assert len(requests) == 1
