"""B2B public INN evidence cannot become fake FNS verification."""
from leadpilot import serviceit_vps_b2b_deep_research as b2b


def findings(extra):
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://tendem.ru/tenders/synthetic-inn",
                   "excerpt": "Закупка 4673595. Заказчик: ООО «Учебная фирма». " + extra}],
    }


def test_checksum_valid_inn_next_to_labelled_buyer_yields_only_source_evidence():
    evidence = b2b.published_buyer_inn_evidence(
        findings("ИНН: 1234567894. ИТ-услуги 1С."))
    assert evidence["inn"] == "1234567894"
    assert evidence["buyer_name"] == "ООО «Учебная фирма»"
    assert evidence["fns_verified"] is False
    assert evidence["public_source"] == "https://tendem.ru/tenders/synthetic-inn"


def test_invalid_checksum_and_organizer_identifiers_not_promoted():
    assert b2b.published_buyer_inn_evidence(
        findings("ИНН: 1234567890. DevOps услуги")) is None
    assert b2b.published_buyer_inn_evidence(
        findings("Организатор: ООО «Площадка». ИНН: 1234567894")) is None
    assert b2b.published_buyer_inn_evidence(
        {"status": "PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
         "pages": [{"url": "https://tendem.ru/tenders/other",
                    "excerpt": "Заказчик: ООО «Учебная фирма». ИНН 1234567894"}]}
    ) is None


def test_conflicting_public_buyer_inns_not_promoted():
    source = findings("ИНН: 1234567894.")
    source["pages"].append({
        "url": "https://tendem.ru/tenders/other",
        "excerpt": "Заказчик: ООО «Учебная фирма». ИНН: 7707083893.",
    })
    assert b2b.published_buyer_inn_evidence(source) is None
