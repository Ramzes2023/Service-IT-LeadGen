"""Synthetic FNS MSP XML tests: no live firm is asserted eligible."""
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from leadpilot.serviceit_v2_fns_qualification import (
    ACCEPTED_REGIONS, EvidenceRejected, IndependentEvidence,
    checked_inn, decide, extract_msp_record, official_fns_source,
)

INN_77 = "7700000129"  # checksum-valid SYNTHETIC identifier
INN_50 = "5000001236"  # checksum-valid SYNTHETIC identifier


def synthetic_xml(*, inn=INN_77, category="1", region="77",
                  snapshot="10.10.2026", other=False, root_type="РЕЕСТРМСП"):
    doc = ('<Документ ДатаСост="' + snapshot + '" '
           'ДатаВклМСП="10.08.2023" ВидСубМСП="1" '
           'КатСубМСП="' + category + '">'
           '<ОргВклМСП ИННЮЛ="' + inn + '" НаимОрг="ООО ТЕСТОВОЕ"/>'
           '<СведМН КодРегион="' + region + '"/></Документ>')
    second = ('<Документ ДатаСост="' + snapshot + '" '
              'ВидСубМСП="1" КатСубМСП="1">'
              '<ОргВклМСП ИННЮЛ="' + INN_50 + '" НаимОрг="ООО ДРУГОЕ"/>'
              '<СведМН КодРегион="50"/></Документ>') if other else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Файл ТипИнф="' + root_type + '" ВерсФорм="4.06" '
            'КолДок="' + str(2 if other else 1) + '">' +
            doc + second + '</Файл>').encode()


def extract(data, *, target=INN_77, **kwargs):
    return extract_msp_record(
        data, target_inn=target,
        expected_xml_sha256=sha256(data).hexdigest(), **kwargs)


@pytest.mark.parametrize("value", [INN_77, INN_50])
def test_checksum_valid_synthetic_inn(value):
    assert checked_inn(value) == value


@pytest.mark.parametrize("bad", ["7700000128", "5000001237", "123", "",
                                  "770000012", "7700000129\n", "770000012A"])
def test_checksum_rejects_bad_identifiers(bad):
    with pytest.raises(EvidenceRejected):
        checked_inn(bad)


def test_bounded_fns_member_extracts_only_exact_inn():
    row = extract(synthetic_xml(other=True))
    assert row.inn == INN_77
    assert row.legal_type == "legal_entity"
    assert row.category == "MICRO"
    assert row.registry_region == "77"
    assert row.snapshot_date == date(2026, 10, 10)
    assert row.organization == "ООО ТЕСТОВОЕ"
    assert row.xml_sha256 == sha256(synthetic_xml(other=True)).hexdigest()


def test_second_region_and_size_category():
    row = extract(synthetic_xml(inn=INN_50,region="50",category="2"), target=INN_50)
    assert row.category == "SMALL" and row.registry_region == "50"


@pytest.mark.parametrize("category", ["0","4","MICRO",""])
def test_unknown_fns_category_fails_closed(category):
    with pytest.raises(EvidenceRejected, match="FNS_CATEGORY_UNKNOWN"):
        extract(synthetic_xml(category=category))


@pytest.mark.parametrize("region", ["", "7", "AA", "007"])
def test_invalid_fns_region_fails_closed(region):
    with pytest.raises(EvidenceRejected, match="FNS_REGION_MISSING"):
        extract(synthetic_xml(region=region))


def test_official_dataset_reference_is_not_automatic_file_authenticity():
    assert official_fns_source("https://www.nalog.gov.ru/opendata/7707329152-rsmp/")
    assert not official_fns_source("http://www.nalog.gov.ru/opendata/7707329152-rsmp/")
    assert not official_fns_source("https://www.nalog.gov.ru.evil.example/opendata/7707329152-rsmp/")
    assert not official_fns_source("https://www.nalog.gov.ru/opendata/7707329152-rsmp/?token=SECRET")
    with pytest.raises(EvidenceRejected, match="SOURCE_DIGEST_MISSING|FNS_XML_DIGEST_MISMATCH"):
        extract_msp_record(synthetic_xml(), target_inn=INN_77,
                           expected_xml_sha256="0"*64)
    with pytest.raises(EvidenceRejected, match="FNS_PASSPORT_NOT_OFFICIAL"):
        extract(synthetic_xml(),passport_url="https://other.example/xml")


def test_deceptive_document_not_registry_rejected():
    with pytest.raises(EvidenceRejected, match="FNS_XML_NOT_MSP"):
        extract(synthetic_xml(root_type="РЕЕСТРМСП-ПП"))


def test_xml_entity_attack_rejected():
    raw = (b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
           +synthetic_xml().replace(b'<?xml version="1.0" encoding="UTF-8"?>',b""))
    with pytest.raises(Exception):
        extract(raw)


def test_missing_or_ambiguous_target_inn_rejected():
    with pytest.raises(EvidenceRejected, match="FNS_MATCH_NOT_UNIQUE_OR_NOT_FOUND"):
        extract(synthetic_xml(),target=INN_50)
    two = synthetic_xml()
    two = two.replace(b'KolDok=',b'KolDok=') # no semantic change
    duplicate = ('<Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="4.06" КолДок="2">'
                 '<Документ ДатаСост="10.10.2026" ВидСубМСП="1" КатСубМСП="1">'
                 '<ОргВклМСП ИННЮЛ="' + INN_77 + '"/><СведМН КодРегион="77"/>'
                 '</Документ>'
                 '<Документ ДатаСост="10.10.2026" ВидСубМСП="1" КатСубМСП="1">'
                 '<ОргВклМСП ИННЮЛ="' + INN_77 + '"/><СведМН КодРегион="77"/>'
                 '</Документ></Файл>').encode()
    with pytest.raises(EvidenceRejected, match="FNS_MATCH_NOT_UNIQUE_OR_NOT_FOUND"):
        extract(duplicate)


def approved_evidence():
    # Test fixture only: no source authentication or permission is inferred.
    return IndependentEvidence(
        official_zip_provenance_verified=True,
        official_egrul_provenance_verified=True,
        egrul_inn=INN_77,
        egrul_legal_region="77",
        egrul_active_legal_entity=True,
        buyer_is_same_legal_entity=True,
        original_notice_verified_active=True,
        original_it_demand_verified=True,
        authorized_corporate_contact_verified=True,
        bitrix_existing_customer_check_clear=True,
        bitrix_global_duplicate_check_clear=True,
        bitrix_stoplist_check_clear=True,
        manager_approval_verified=True,
    )


def test_every_qualification_gate_is_explicit():
    msp = extract(synthetic_xml())
    assert decide(msp, IndependentEvidence(),as_of=date(2026,11,2)).status == "REVIEW_REQUIRED"
    assert len(decide(msp, IndependentEvidence(),as_of=date(2026,11,2)).reasons) >= 8
    yes = decide(msp, approved_evidence(),as_of=date(2026,11,2))
    assert yes.status == "ELIGIBLE_FOR_SEPARATELY_APPROVED_LEAD_DELIVERY"
    assert yes.verified_lead is True
    assert yes.eligible_region and yes.eligible_small_business
    checks = (
        "official_zip_provenance_verified",
        "official_egrul_provenance_verified", "egrul_active_legal_entity",
        "buyer_is_same_legal_entity", "original_notice_verified_active",
        "original_it_demand_verified", "authorized_corporate_contact_verified",
        "bitrix_existing_customer_check_clear",
        "bitrix_global_duplicate_check_clear",
        "bitrix_stoplist_check_clear", "manager_approval_verified"
    )
    for check in checks:
        changed = replace(approved_evidence(), **{check: False})
        assert decide(msp,changed,as_of=date(2026,11,2)).verified_lead is False


def test_stale_snapshot_future_and_wrong_region_dont_qualify():
    msp = extract(synthetic_xml())
    good = approved_evidence()
    assert "FNS_SNAPSHOT_STALE_OR_FUTURE" in decide(msp,good,as_of=date(2027,1,10)).reasons
    assert "FNS_SNAPSHOT_STALE_OR_FUTURE" in decide(msp,good,as_of=date(2026,10,1)).reasons
    assert "LEGAL_REGION_77_50_NOT_VERIFIED" in decide(
        msp, replace(good,egrul_legal_region="06"),as_of=date(2026,11,2)).reasons


def test_medium_business_stays_on_review_not_a_lead():
    msp = extract(synthetic_xml(category="3"))
    decision = decide(msp,approved_evidence(),as_of=date(2026,11,2))
    assert "NOT_MICRO_OR_SMALL" in decision.reasons
    assert not decision.verified_lead


def test_no_fns_evidence_is_never_a_lead():
    d = decide(None,approved_evidence(),as_of=date(2026,11,2))
    assert d.status == "REVIEW_REQUIRED"
    assert d.reasons == ("FNS_MSP_UNKNOWN",)
    assert not d.verified_lead
