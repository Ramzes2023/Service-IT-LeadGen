"""Only synthetic feed summary counts. Never claim real buyer intent."""
import pytest
from leadpilot.eis_source_diagnostics import diagnose, _public_samples


@pytest.fixture
def base():
    return dict(mode="EIS_OFFICIAL_RSS_RESEARCH_ONLY",
                source="eis-zakupki-gov-ru-official-rss",
                search_rss_url="https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1%D0%A1&fz44=on",
                counts={"items_seen": 0, "candidates_for_identity_review": 0,
                        "qualified_small_business_leads": 0, "crm_ready": 0},
                items=[], crm_writes="NO", outbound_messages="NO")


def test_real_200_xml_zero_items_is_not_network_failure(base):
    res = diagnose(base)
    assert res["status"] == "OFFICIAL_RSS_HAS_ZERO_ITEMS"
    assert res["source_profile"]["fz44_filter_present"] is True
    assert res["source_profile"]["fz223_filter_present"] is False
    assert res["qualified_leads"] == 0


def test_all_matching_query_but_wrong_purchase_scope(base):
    base["counts"].update(items_seen=200, not_explicit_external_1c_service=200)
    res = diagnose(base)
    assert res["status"] == "ALL_ITEMS_FAILED_EXPLICIT_EXTERNAL_1C_SCOPE"
    assert res["rejection_counts"]["not_explicit_external_1c_service"] == 200


def test_all_stale(base):
    base["counts"].update(items_seen=5, stale_rejected=5)
    assert diagnose(base)["status"] == "ALL_ITEMS_OUTSIDE_30_DAY_WINDOW"


def test_link_rejections(base):
    base["counts"].update(items_seen=5, unverified_notice_link=5)
    assert diagnose(base)["status"] == "ALL_ITEMS_FAILED_NOTICE_URL_VALIDATION"


def test_mixed_rejections_are_not_empty_feed(base):
    base["counts"].update(items_seen=10, unverified_notice_link=2,
                           not_explicit_external_1c_service=8)
    assert diagnose(base)["status"] == "ZERO_AFTER_MULTIPLE_RESEARCH_GATES"


@pytest.mark.parametrize("key,value", [("crm_writes", "YES"), ("outbound_messages", "YES"),
                                        ("source", "fake"), ("mode", "FAKE")])
def test_untrusted_report_rejected(base, key, value):
    base[key] = value
    with pytest.raises(ValueError):
        diagnose(base)


def test_fake_crm_count_rejected(base):
    base["counts"]["crm_ready"] = 1
    with pytest.raises(ValueError, match="UNSAFE"):
        diagnose(base)


def test_noninteger_count_rejected(base):
    base["counts"]["items_seen"] = True
    with pytest.raises(ValueError, match="BAD_COUNTER"):
        diagnose(base)


def test_fake_report_rejected_if_items_do_not_match_counts(base):
    base["counts"].update(items_seen=1, candidates_for_identity_review=1)
    with pytest.raises(ValueError, match="UNSAFE"):
        diagnose(base)


def test_potential_candidates_do_not_mean_qualified_leads(base):
    base["counts"].update(items_seen=1, candidates_for_identity_review=1)
    base["items"] = [{"title": "Synthetic", "crm_ready": False}]
    res = diagnose(base)
    assert res["status"] == "RESEARCH_ITEMS_FOUND_BUYER_STILL_UNVERIFIED"
    assert res["qualified_leads"] == 0


def test_detailed_reason_counters_are_reported_without_promoting_leads(base):
    base["counts"].update(
        items_seen=200,
        not_explicit_external_1c_service=200,
        scope_subject_label_missing=150,
        scope_no_1c_in_purchase_subject=30,
        scope_no_external_service_in_purchase_subject=20,
    )
    base["scope_rejection_samples"] = [{"reason": "SUBJECT_LABEL_MISSING"}]
    result = diagnose(base)
    assert result["status"] == "ALL_ITEMS_FAILED_EXPLICIT_EXTERNAL_1C_SCOPE"
    assert result["scope_breakdown"]["subject_label_missing"] == 150
    assert result["scope_breakdown"]["no_external_service_in_purchase_subject"] == 20
    assert result["scope_sample_count"] == 1
    assert result["crm_ready"] is False



def test_public_samples_ascii_escape_preserves_cyrillic(tmp_path):
    import json
    data = {
        "scope_rejection_samples": [
            {"reason": "NO_1C_IN_PURCHASE_SUBJECT",
             "rss_title": "Электронный аукцион №1",
             "procurement_subject": "Оказание медицинских услуг",
             "notice_link_present": True,
             "unsafe_extra": "https://private.example/secret"}
        ]
    }
    records = _public_samples(data)
    assert len(records) == 1
    text = json.dumps(records, ensure_ascii=True)
    assert r"\u042d" in text
    assert "\\u041e" in text
    assert "secret" not in text and "https://" not in text
    assert "????" not in text


def test_public_sample_max_eight_and_reject_unsafe_fields():
    valid = {"reason": "SUBJECT_LABEL_MISSING", "rss_title": "Procurement",
             "procurement_subject": None}
    result = _public_samples({"scope_rejection_samples": [dict(valid) for _ in range(12)]})
    assert len(result) == 8
    with pytest.raises(ValueError):
        _public_samples({"scope_rejection_samples": [
            {"reason": "PRODUCTION_READY", "rss_title": "X", "procurement_subject": None}
        ]})
