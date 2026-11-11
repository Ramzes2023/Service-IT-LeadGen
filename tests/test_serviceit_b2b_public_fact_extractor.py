"""Free deterministic fields; no external HTTP, FNS assertion or CRM writes."""
from leadpilot.serviceit_b2b_public_fact_extractor import (
    valid_inn, valid_ogrn, extract_tender_facts, attach_corporate_profile,
)

TENDER = "https://tendem.ru/tenders/16548471"
TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
TEXT = """
Запрос котировок · B2B-Center · опубликовано 09.11.2026
# Оказание услуг по предоставлению ИТ-специалистов
Нижегородская область
**Регион****Нижегородская область**
**Заказчик****Общество с ограниченной ответственностью «ГЛОБУС-ИТ»**
**Подача заявок****до 23.11.2026 17:00 МСК**
Цена не указана
Лоты
Системный аналитик 1С, Middle — офис (Москва)
Номер у источника: 4673595
"""
COMPANY = """
СВЕДЕНИЯ ОБ АККРЕДИТОВАННОЙ ИТ-КОМПАНИИ
Полное наименование организации: Общество с ограниченной ответственностью «ГЛОБУС-ИТ»
Юридический адрес: 603024 г. Нижний Новгород, Казанская набережная, дом 5
ИНН: 5260365605
ОГРН: 1135260012337
Электронная почта: contact@globus-ltd.com
"""


def tender():
    return extract_tender_facts(
        tender_id="4673595", title=TITLE, tender_text=TEXT,
        source_url=TENDER, verified_source=True)


def test_real_world_b2b_tendem_public_fields_and_null_budget():
    facts = tender()
    assert facts["buyer"]["value"] == "Общество с ограниченной ответственностью «ГЛОБУС-ИТ»"
    assert facts["geography"]["value"] == "Нижегородская область"
    assert facts["submission_deadline"]["value"] == "23.11.2026 17:00"
    assert facts["budget_rub"] is None
    assert facts["budget_state"] == "NOT_PUBLISHED"
    assert facts["buyer"]["source_url"] == TENDER


def test_verified_company_site_inn_ogrn_contact_but_not_fns():
    got = attach_corporate_profile(
        tender(), company_url="https://globus-ltd.ru/accreditation",
        company_text=COMPANY, independently_verified_website=True)
    assert got["buyer_inn"]["value"] == "5260365605"
    assert got["buyer_ogrn"]["value"] == "1135260012337"
    assert got["fns_registry_status"] == "NOT_CHECKED"
    assert got["company_public_emails"][0]["value"] == "contact@globus-ltd.com"
    assert "NOT_FNS_VERIFIED" in got["buyer_inn"]["evidence"]


def test_company_is_only_self_published_until_independent_proof():
    assert "buyer_inn" not in attach_corporate_profile(
        tender(), company_url="https://globus-ltd.ru/accreditation",
        company_text=COMPANY, independently_verified_website=False)
    other = COMPANY.replace("«ГЛОБУС-ИТ»", "«НЕЗАВИСИМАЯ ФИРМА»")
    assert "buyer_inn" not in attach_corporate_profile(
        tender(), company_url="https://another-example.ru/contacts",
        company_text=other, independently_verified_website=True)
    a_client_mention = """
Наши клиенты: ООО ГЛОБУС-ИТ
ИНН: 5260365605
ООО Совсем другое юридическое лицо
"""
    assert "buyer_inn" not in attach_corporate_profile(
        tender(), company_url="https://other.ru/contacts",
        company_text=a_client_mention, independently_verified_website=True)


def test_checksum_catches_unrelated_or_mistyped_company_identifiers():
    assert valid_inn("5260365605") and valid_inn("7734202860")
    assert valid_ogrn("1135260012337") and valid_ogrn("1027739555282")
    assert not valid_inn("5260365606")
    assert not valid_ogrn("1135260012338")
    assert not valid_inn("526O365605")
    assert not valid_inn("123")


def test_primary_b2b_delivery_budget_and_gated_documents():
    source = """
Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года
№ 4673971
Общая сумма закупки
Не указана, ₽
Условия оплаты
В соответствии с Закупочной документацией
Адрес поставки / оказания услуг
Россия, г. Москва, 125009, Большой Кисловский пер., д. 13
Документация от организатора
Доступно зарегистрированным пользователям.
Окончание приёма заявок
17.11.2026 18:00, по МСК
С этими торгами также смотрят
Заказчик ООО ЧУЖАЯ КОМПАНИЯ
Начальная цена контракта
900 000 000 ₽
"""
    got = extract_tender_facts(
        tender_id="4673971", title="Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года",
        tender_text=source,
        source_url="https://www.b2b-center.ru/app/market-next/abc/tender-4673971/",
        verified_source=True)
    assert got["delivery_address"]["value"].startswith("Россия, г. Москва")
    assert got["submission_deadline"]["value"] == "17.11.2026 18:00"
    assert got["documents_access"] == "LOGIN_REQUIRED"
    assert got["budget_rub"] is None
    assert got["budget_state"] == "EXPLICITLY_NOT_PUBLISHED"
    assert got["buyer"] is None


def test_early_no_write_gate_and_real_public_amount():
    assert extract_tender_facts(
        tender_id="4673595", title=TITLE, tender_text=TEXT,
        source_url=TENDER, verified_source=False) == {}
    source = "Начальная цена контракта\n1 250 000 ₽\n" + TEXT
    got = extract_tender_facts(
        tender_id="4673595", title=TITLE, tender_text=source,
        source_url=TENDER, verified_source=True)
    assert got["budget_rub"]["value"] == "1250000"
    assert got["budget_state"] == "PUBLIC_AMOUNT"
