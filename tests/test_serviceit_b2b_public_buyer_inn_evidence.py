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


def test_public_buyer_inn_is_source_cited_in_nine_ai_fields_not_claimed_fns():
    from leadpilot import serviceit_b2b_routerai_deep as deep
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    source = findings("ИНН: 1234567894. ИТ-услуги 1С и DevOps.")
    source["checked_at"] = "2026-11-10T12:00:00+00:00"
    item = {field: "Previous AI content" for field in AI_FIELDS}
    item[PREFIX + "SIGNAL_KIND"] = "tender"
    report = deep.source_only_investigation(source)
    card = b2b.cited_manager_fields(item, source, report)
    assert set(card) == AI_FIELDS
    assert "ИНН-кандидат (контрольная сумма корректна): 1234567894" in (
        card[PREFIX + "AI_FULL_REPORT"])
    assert "ФНС/право контакта ещё не подтверждены" in (
        card[PREFIX + "AI_MANAGER_BRIEF"])
    assert "не разрешать исходящий контакт" in card[PREFIX + "AI_FULL_REPORT"]
    assert all(len(value) <= (12000 if key == PREFIX + "AI_FULL_REPORT" else 4096)
               for key, value in card.items())


def test_management_company_and_nested_legal_names():
    source = {"status": "PUBLIC_PAGES_INSPECTED", "pages": [{
        "url": "https://zakupki.kontur.ru/1234567_40",
        "excerpt": "Заказчик Общество с ограниченной ответственностью "
                   "Управляющая компания «Учебный холдинг» ИНН-КПП 1234567894-770701001",
    }]}
    assert b2b.public_buyer_candidate(source) == "ООО УК «Учебный холдинг»"
    assert b2b.published_buyer_inn_evidence(source)["inn"] == "1234567894"
    source["pages"][0]["excerpt"] = (
        "Заказчик Акционерное общество «Издательство «Учебник» ИНН 1234567894"
    )
    assert b2b.public_buyer_candidate(source) == "АО «Издательство „Учебник“»"


def test_only_explicit_first_party_self_purchase_uses_linked_organizer(monkeypatch):
    import httpx

    url = "https://www.b2b-center.ru/app/market-next/servers/tender-1234567/"
    excerpt = '№ 1234567 Организатор АО "Учебник" Процедура проводится Для собственных нужд'
    findings = {"status": "PUBLIC_PAGES_INSPECTED", "pages": [{
        "url": url, "excerpt": excerpt,
        "profile_links": [("/firms/ao-uchebnik/1234/", 'АО "Учебник"')],
    }]}

    def company_page(client, target, hosts):
        assert target == "https://www.b2b-center.ru/firms/ao-uchebnik/1234/"
        return {"text": 'Краткое наименование АО "Учебник" Полное наименование '
                        'Акционерное общество "Учебник" ИНН 1234567894'}

    monkeypatch.setattr(b2b, "_page", company_page)
    with httpx.Client(trust_env=False) as client:
        enriched = b2b.followup_public_buyer_research(
            findings, {"SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES"}, client, "")
    assert b2b.public_buyer_candidate(enriched) == "АО «Учебник»"
    assert b2b.published_buyer_inn_evidence(enriched)["inn"] == "1234567894"
    for wrong in ("№ 1234568", "Для нужд третьего лица"):
        old = "№ 1234567" if "№" in wrong else "Для собственных нужд"
        findings["pages"][0]["excerpt"] = excerpt.replace(old, wrong)
        assert b2b.public_buyer_candidate(findings) == ""
