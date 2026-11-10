"""Public corporate telephone provenance must survive B2B V11 audit.

The corporate contact is a company switchboard, never the procurement officer.
No real network, CRM, secrets, PDFs, or outreach in these tests.
"""
from leadpilot import serviceit_public_evidence_research as pub
from leadpilot.serviceit_vps_b2b_deep_research import (
    _public_company_self_declaration,
)
from leadpilot.serviceit_b2b_dossier_v11 import checked_public_contacts

BUYER="ООО «Глобус-ИТ»"
INN="5260365605"
URL="https://globus-ltd.ru/contacts"


def official_page(with_inn=True):
    requisites = (
        "ООО «ГЛОБУС-ИТ» ИНН 5260365605 ОГРН 1135260012337 "
        if with_inn else "ООО «ГЛОБУС-ИТ» ИНН отсутствует "
    )
    return {
        "url": URL,
        "text": requisites +
                "Наши контакты: 8 800 775 13 96. "
                "Общая почта contact@globus-ltd.com. ",
        "emails": ["contact@globus-ltd.com"],
        "phones": ["8 800 775 13 96"],
    }


def test_public_same_legal_name_and_inn_preserves_phone_and_corporate_mail(monkeypatch):
    monkeypatch.setattr(pub,"_page",lambda client,url,allow: official_page())
    data=_public_company_self_declaration(
        client=None,buyer=BUYER,
        known_pages=[{"url":URL}],token=None,allow_extra_discovery=False)
    assert data is not None
    assert data["inn"]==INN
    assert data["fns_verified"] is False
    assert data["public_source"]==URL
    ind={"public_inn":data,"contacts":data["contacts"]}
    findings={"independent_company_research":ind}
    selected=checked_public_contacts(
        findings,BUYER,{"buyer_name":BUYER,"inn":INN})
    assert ("телефон","+78007751396",URL) in selected
    assert ("email","contact@globus-ltd.com",URL) in selected
    assert all(row["origin"]=="LEGAL_NAME_AND_INN_ON_PUBLIC_PAGE_NOT_FNS"
               for row in data["contacts"])
    phones=[row for row in data["contacts"] if row["type"]=="phone"]
    assert phones and "тендерный ответственный не подтверждён" in phones[0]["role"]


def test_company_contacts_never_pass_without_co_identified_inn(monkeypatch):
    monkeypatch.setattr(pub,"_page",lambda client,url,allow: official_page(False))
    data=_public_company_self_declaration(
        client=None,buyer=BUYER,
        known_pages=[{"url":URL}],token=None,allow_extra_discovery=False)
    assert data is None


def test_identity_conflict_never_yields_wrong_company_phone(monkeypatch):
    monkeypatch.setattr(pub,"_page",lambda client,url,allow: official_page())
    data=_public_company_self_declaration(
        client=None,buyer=BUYER,
        known_pages=[{"url":URL}],token=None,allow_extra_discovery=False)
    findings={"independent_company_research":
                {"public_inn":data,"contacts":data["contacts"]}}
    assert checked_public_contacts(
        findings,"ООО «ДРУГАЯ»",{"buyer_name":"ООО «ДРУГАЯ»","inn":INN})==[]
    assert checked_public_contacts(
        findings,BUYER,{"buyer_name":BUYER,"inn":"7702077840"})==[]


def test_marketplace_support_number_is_not_company_phone(monkeypatch):
    monkeypatch.setattr(pub,"_page",lambda client,url,allow: official_page())
    # A marketplace URL is explicitly filtered from corporate website search.
    data=_public_company_self_declaration(
        client=None,buyer=BUYER,
        known_pages=[{"url":"https://zakupki.kontur.ru/4673595_4"}],
        token=None,allow_extra_discovery=False)
    assert data is None
