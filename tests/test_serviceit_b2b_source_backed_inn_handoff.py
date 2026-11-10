"""Public B2B INN handoff is source-backed and never asserts FNS identity."""
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_public_supplement import source_backed_company_inn_update


def item():
    obj={name: "Существующие подтверждённые сведения" for name in AI_FIELDS}
    obj[PREFIX + "COMPANY_INN"] = ""
    return obj


def findings(inn="5260365605", provenance="PUBLIC_CORPORATE_SELF_DECLARATION"):
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://tendem.ru/tenders/16548471",
                   "excerpt": 'Заказчик Общество с ограниченной ответственностью "ГЛОБУС-ИТ"'}],
        "independent_company_research": {
            "public_inn": {
                "inn": inn, "buyer_name": "ООО «ГЛОБУС-ИТ»",
                "public_source": "https://globus-ltd.ru/contacts",
                "provenance": provenance, "fns_verified": False,
            },
            "contacts": [{"type": "email", "value": "contact@globus-ltd.com",
                          "url": "https://globus-ltd.ru/contacts"}],
        },
    }


def test_two_separately_fetched_sources_generate_three_field_candidate():
    before=item()
    fields=source_backed_company_inn_update(before,findings())
    assert set(fields) == {PREFIX+"COMPANY_INN", PREFIX+"AI_FULL_REPORT",
                           PREFIX+"AI_MANAGER_BRIEF"}
    assert fields[PREFIX+"COMPANY_INN"] == "5260365605"
    assert "contact@globus-ltd.com" in fields[PREFIX+"AI_FULL_REPORT"]
    assert "НЕ ФНС" in fields[PREFIX+"AI_MANAGER_BRIEF"]
    assert before[PREFIX+"COMPANY_INN"] == ""


def test_source_conflicts_or_existing_inn_are_protected():
    assert source_backed_company_inn_update(item(),findings("1234567890")) is None
    assert source_backed_company_inn_update(item(),findings(provenance="MODEL_INFERRED")) is None
    data=findings()
    data["independent_company_research"]["public_inn"]["buyer_name"]="ООО «ДРУГАЯ КОМПАНИЯ»"
    assert source_backed_company_inn_update(item(),data) is None
    data=findings()
    data["independent_company_research"]["public_inn"]["public_source"]="https://tendem.ru/tenders/16548471"
    assert source_backed_company_inn_update(item(),data) is None
    existing=item()
    existing[PREFIX+"COMPANY_INN"]="5260365605"
    assert source_backed_company_inn_update(existing,findings()) is None


def test_no_public_company_document_does_not_write():
    data=findings()
    del data["independent_company_research"]["public_inn"]
    assert source_backed_company_inn_update(item(),data) is None


def test_tender_and_independent_website_same_inn_different_urls_is_valid():
    # Earlier implementation incorrectly required the public tender's URL
    # to equal the independently fetched corporate website URL.
    data = findings()
    data["pages"][0]["excerpt"] += " ИНН: 5260365605."
    fields = source_backed_company_inn_update(item(), data)
    assert fields is not None
    assert fields[PREFIX + "COMPANY_INN"] == "5260365605"
    assert "https://globus-ltd.ru/contacts" in fields[PREFIX + "AI_FULL_REPORT"]


def test_conflicting_tender_and_company_inns_cannot_write():
    data = findings()
    data["pages"][0]["excerpt"] += " ИНН: 7707083893."
    assert source_backed_company_inn_update(item(), data) is None
