"""Regression against real public B2B/Tendem legal-name formatting and websites."""
import httpx
from leadpilot import serviceit_vps_b2b_deep_research as b2b
from leadpilot import serviceit_public_evidence_research as page_reader


def public_mirror(raw):
    return {"status": "PUBLIC_PAGES_INSPECTED",
            "pages": [{"url": "https://tendem.ru/tenders/16548471",
                       "excerpt": raw}]}


def test_real_tendem_full_name_no_colon_is_canonical_buyer():
    page = public_mirror(
        'Оказание услуг по предоставлению ИТ-специалистов. '
        'Нижегородская область. '
        'Заказчик Общество с ограниченной ответственностью "ГЛОБУС-ИТ". '
        'Подача заявок до 23.11.2026 17:00 МСК.')
    assert b2b.public_buyer_candidate(page) == "ООО «ГЛОБУС-ИТ»"


def test_organizer_is_not_a_buyer_and_colon_short_form_is_supported():
    assert b2b.public_buyer_candidate(
        public_mirror('Организатор: ООО «Электронная площадка»')) == ""
    assert b2b.public_buyer_candidate(
        public_mirror('Заказчик: ООО «Тестовая организация»')) == (
        'ООО «Тестовая организация»')


def test_public_website_name_inn_and_cross_tld_corporate_email(monkeypatch):
    source_url = "https://globus-ltd.ru/contacts"
    source = {
        "url": source_url, "emails": [], "phones": [],
        "text": ('Контакты. ООО "ГЛОБУС-ИТ". '
                 'ИНН 5260365605 ОГРН 1135260012337. '
                 'Служба консультаций contact@globus-ltd.com. '
                 'Телефон +7 831 266-75-96.'),
    }
    monkeypatch.setattr(page_reader, "_page",
                        lambda client, url, allowed: source)
    with httpx.Client() as client:
        result = b2b._public_company_self_declaration(
            client, "ООО «ГЛОБУС-ИТ»",
            [{"url": source_url}], token=None)
    assert result["inn"] == "5260365605"
    assert result["fns_verified"] is False
    assert result["public_source"] == source_url
    assert any(c["value"] == "contact@globus-ltd.com"
               for c in result["contacts"])


def test_site_with_wrong_legal_entity_cannot_steal_inn_and_email(monkeypatch):
    source_url = "https://globus-ltd.ru/contacts"
    source = {
        "url": source_url, "emails": [], "phones": [],
        "text": ('ООО "ГЛОБУС-ИНТЕГРАЦИЯ", ИНН 5260480100 '
                 'contact@globus-ltd.com.'),
    }
    monkeypatch.setattr(page_reader, "_page", lambda *args: source)
    with httpx.Client() as client:
        assert b2b._public_company_self_declaration(
            client, "ООО «ГЛОБУС-ИТ»",
            [{"url": source_url}], token=None) is None


def test_fns_not_claimed_with_valid_self_published_inn(monkeypatch):
    url = "https://globus-ltd.ru/contacts"
    data = {"url": url, "emails": [], "phones": [],
            "text": ('ООО "ГЛОБУС-ИТ" ИНН 5260365605 '
                     'info@globus-ltd.com.')}
    monkeypatch.setattr(page_reader, "_page", lambda *args: data)
    with httpx.Client() as client:
        website = b2b._public_company_self_declaration(
            client, "ООО «ГЛОБУС-ИТ»", [{"url": url}], None)
    source = public_mirror(
        'Заказчик Общество с ограниченной ответственностью "ГЛОБУС-ИТ"')
    source["independent_company_research"] = {"public_inn": {
        key: website[key] for key in (
            "inn", "buyer_name", "public_source", "fns_verified")}}
    assert b2b.published_buyer_inn_evidence(source) == {
        "inn": "5260365605", "public_source": url,
        "buyer_name": "ООО «ГЛОБУС-ИТ»", "fns_verified": False}


def test_extra_brave_is_networkless_by_default_and_explicitly_opted_in(monkeypatch):
    url = "https://globus-ltd.ru/contacts"
    site = {
        "url": url, "emails": [], "phones": [],
        "text": 'ООО "ГЛОБУС-ИТ" ИНН 5260365605 info@globus-ltd.com',
    }
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"web": {"results": [{"url": url}]}})
    monkeypatch.setattr(page_reader, "_page", lambda *_: site)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert b2b._public_company_self_declaration(
            client, "ООО «ГЛОБУС-ИТ»", [],
            "SYNTHETIC-BRAVE-TOKEN") is None
        assert calls == []
        result = b2b._public_company_self_declaration(
            client, "ООО «ГЛОБУС-ИТ»", [],
            "SYNTHETIC-BRAVE-TOKEN", allow_extra_discovery=True)
    assert len(calls) == 1
    assert result["inn"] == "5260365605"
    assert result["fns_verified"] is False


def test_extra_bounded_search_when_initial_company_pages_have_no_legal_inn(monkeypatch):
    # A plausible website candidate without the exact legal name/INN used to
    # suppress the second Brave lookup; real buyer pages were never reached.
    official = "https://globus-ltd.ru/contacts"
    empty = "https://unrelated.example.org/about"
    import leadpilot.serviceit_public_evidence_research as reader
    candidates = []
    def fetch(client,url,allowed):
        candidates.append(url)
        return {"url":url, "text": (
            'ООО "ГЛОБУС-ИТ" ИНН 5260365605 contact@globus-ltd.com'
            if url == official else 'Общие сведения, без реквизитов'
        ),"emails":[],"phones":[]}
    monkeypatch.setattr(reader, "_page", fetch)
    def answer(request):
        return httpx.Response(200, json={"web": {"results": [{"url":official}]}})
    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        result = b2b._public_company_self_declaration(
            client,"ООО «ГЛОБУС-ИТ»",[{"url":empty}],
            "SYNTHETIC-BRAVE-TOKEN",allow_extra_discovery=True)
    assert result and result["inn"] == "5260365605"
    assert candidates == [empty, official]
