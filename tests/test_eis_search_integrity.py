"""Deterministic EIS negative-control search verification, no live network."""
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from leadpilot import eis_search_integrity as s
from leadpilot.eis_rss import SOURCE_NAME


PRIMARY = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1%D0%A1&fz44=on"


def report(names):
    digest = hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest() if names else None
    return dict(
        mode="EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        source=SOURCE_NAME,
        crm_writes="NO",
        outbound_messages="NO",
        counts={"items_seen": len(names), "candidates_for_identity_review": 0},
        all_notices_fingerprint_sha256=digest,
        all_notices_fingerprint_count=len(set(names)),
    )


def test_negative_query_changes_only_search_string():
    control = s.negative_control_url(PRIMARY)
    a = parse_qs(urlsplit(PRIMARY).query)
    b = parse_qs(urlsplit(control).query)
    assert a["fz44"] == b["fz44"] == ["on"]
    assert b["searchString"] == [s.CONTROL]
    assert b["searchString"] != a["searchString"]
    assert urlsplit(control).hostname == "zakupki.gov.ru"


@pytest.mark.parametrize("url", [
    "https://evil.com/epz/order/extendedsearch/rss.html?searchString=1C",
    "http://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?fz44=on",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=&fz44=on",
    "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C&searchString=other",
])
def test_no_bad_or_ambiguous_negative_control_url(url):
    with pytest.raises(ValueError):
        s.negative_control_url(url)


def test_identical_two_hundred_notices_flag_untrusted_search():
    notices = ["https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=" + str(10000000+i) for i in range(200)]
    result = s.compare_reports(report(notices), report(notices))
    assert result["status"] == "NEGATIVE_CONTROL_IDENTICAL_NOTICE_SET_SEARCH_UNTRUSTED"
    assert result["original_items_seen"] == result["control_items_seen"] == 200
    assert result["qualified_leads"] == 0 and result["crm_writes"] == "NO"


def test_negative_control_empty_is_evidence_query_changes_results():
    result = s.compare_reports(report(["official"]), report([]))
    assert result["status"] == "NEGATIVE_CONTROL_EMPTY_SEARCH_MAY_BE_APPLIED"
    assert result["qualified_leads"] == 0


def test_changed_notice_set_is_not_proof_of_targeted_service():
    result = s.compare_reports(report(["a", "b"]), report(["b", "c"]))
    assert result["status"] == "NEGATIVE_CONTROL_DIFFERENT_NOTICE_SETS_NOT_PROOF_OF_SCOPE"
    assert result["verified_small_business_buyers"] == 0


def test_both_empty_inconclusive():
    assert s.compare_reports(report([]), report([]))["status"] == "INCONCLUSIVE_BOTH_EMPTY"


def test_probe_calls_only_official_domain_twice(monkeypatch):
    visited = []
    def mock_fetch(*, url, today=None):
        visited.append(url)
        return report(["first"] if len(visited) == 1 else [])
    monkeypatch.setattr(s, "fetch", mock_fetch)
    state = s.probe(PRIMARY)
    assert state["status"] == "NEGATIVE_CONTROL_EMPTY_SEARCH_MAY_BE_APPLIED"
    assert len(visited) == 2
    assert [urlsplit(v).hostname for v in visited] == ["zakupki.gov.ru", "zakupki.gov.ru"]


def test_untrusted_approval_not_accepted():
    a = report(["x"])
    b = report([])
    for key, value in [("crm_writes", "YES"), ("outbound_messages", "YES"), ("source", "fake")]:
        changed = {**a, key: value}
        with pytest.raises(ValueError):
            s.compare_reports(changed, b)


def test_malformed_fingerprint_rejected():
    a = report(["x"])
    a["all_notices_fingerprint_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="FINGERPRINT"):
        s.compare_reports(a, report([]))
