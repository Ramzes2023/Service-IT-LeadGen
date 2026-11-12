"""Strict Moscow/Moscow Oblast B2B-Center pre-CRM gate (offline)."""
import pytest

from leadpilot import serviceit_b2b_moscow_geo as geo
from leadpilot import serviceit_vps_b2b_bridge as bridge


NOTICE = {
    "schema": "serviceit-b2b-notice-v1",
    "source": "B2B-Center",
    "email_message_id": "1a19b7630301bafb",
    "tender_id": "1234567",
    "source_url": "https://www.b2b-center.ru/app/market-next/it-support/tender-1234567/",
    "subject": "Оказание услуг по сопровождению системы 1С",
    "organizer": "ООО Пример",
    "buyer_inn": None, "fns_msp": None, "region": None,
    "original_publication_verified": False, "contact_approved": False,
    "qualified_lead": False,
}


@pytest.mark.parametrize("lines,expected", [
    (["Организатор: ООО Москва", "ИНН 123"], "UNKNOWN"),
    (["Юридический адрес заказчика: г. Москва"], "UNKNOWN"),
    (["Регион: Москва"], "UNKNOWN"),  # Can be a site-wide filter.
    (["Место оказания услуг", "Москва"], "MOSCOW"),
    (["Место поставки: Московская область, г. Химки"], "MOSCOW"),
    (["Адрес поставки / оказания услуг",
      "Россия, г. Москва, 121151, ул. Можайский Вал, д. 8"], "MOSCOW"),
    (["Место поставки / оказания услуг", "Московская область, г. Красногорск"], "MOSCOW"),
    (["Место поставки", "Краснодарский край, г. Краснодар, ул. Солнечная, д. 15"], "OUTSIDE"),
    (["Регион исполнения: г. Москва, Тверская улица"], "MOSCOW"),
    (["Адрес поставки: Московский район, Санкт-Петербург"], "OUTSIDE"),
    (["Место поставки: г. Уфа"], "OUTSIDE"),
    (["Место оказания услуг: Москва и Санкт-Петербург"], "MIXED"),
    (["Место поставки: Московская область", "Место оказания услуг: Тверь"], "MIXED"),
    (["Место поставки: Москва", "Место оказания услуг: Казань"], "MIXED"),
    (["Место исполнения договора: по всей России"], "OUTSIDE"),
    (["Место поставки: дистанционно"], "UNKNOWN"),
])
def test_location_requires_procurement_label_and_excludes_other_regions(lines,expected):
    assert geo.classify_location_lines(lines) == expected


def test_canonical_original_b2b_heading_verification_requires_exact_title_number_proximity():
    assert geo.exact_original_page_identity([
        "Приобретение лицензий Teams (продление)", "№ 4677923",
        "Адрес поставки / оказания услуг", "Россия, г. Москва"
    ], "4677923", "Приобретение лицензий Teams (продление)")
    assert not geo.exact_original_page_identity([
        "Актуальные торговые процедуры", "№ 4677923",
        "Справочник лицензий Teams"
    ], "4677923", "Приобретение лицензий Teams (продление)")
    assert not geo.exact_original_page_identity([
        "Приобретение лицензий Teams (продление)", "№ 4677924",
        "Адрес поставки / оказания услуг", "Россия, г. Москва"
    ], "4677923", "Приобретение лицензий Teams (продление)")


def test_realistic_original_b2b_teams_moscow_performance(monkeypatch):
    notice = {**NOTICE,
       "tender_id":"4677923",
       "source_url":"https://www.b2b-center.ru/app/market-next/priobretenie-litsenzii-teams-prodlenie/tender-4677923/",
       "subject":"Приобретение лицензий Teams (продление)"}
    monkeypatch.setattr(geo, "_robots", lambda *args: None)
    monkeypatch.setattr(geo, "_get", lambda *args:
      "<html><body><h1>Приобретение лицензий Teams (продление)</h1>"
      "<p>№ 4677923</p><div>Организатор</div><p>АО ПРИМЕР</p>"
      "<div>Адрес поставки / оказания услуг</div>"
      "<p>Россия, г. Москва, 121151, ул. Можайский Вал, д. 8</p>"
      "</body></html>")
    assert geo.check_public_procurement_location(notice, object()) == "MOSCOW"


def test_realistic_original_b2b_rejects_mismatched_tender_even_with_moscow(monkeypatch):
    notice = {**NOTICE,
       "tender_id":"4677923",
       "source_url":"https://www.b2b-center.ru/app/market-next/priobretenie-litsenzii-teams-prodlenie/tender-4677923/",
       "subject":"Приобретение лицензий Teams (продление)"}
    monkeypatch.setattr(geo, "_robots", lambda *args: None)
    monkeypatch.setattr(geo, "_get", lambda *args:
      "<html><body><h1>Приобретение лицензий Teams (продление)</h1>"
      "<p>№ 4677924</p><div>Адрес поставки / оказания услуг</div>"
      "<p>Москва</p></body></html>")
    assert geo.check_public_procurement_location(notice, object()) == "UNKNOWN"


def test_invalid_public_page_never_promotes_mail_company_address(monkeypatch):
    urls = []
    monkeypatch.setattr(geo, "_robots", lambda client,url,allowed: urls.append(url))
    monkeypatch.setattr(geo, "_get", lambda client,url,allowed:
        "<html><body>Тендер 9999999 Организатор ООО Москва "
        "Место оказания услуг: Москва</body></html>")
    assert geo.check_public_procurement_location(NOTICE, None) == "UNKNOWN"
    assert urls == [NOTICE["source_url"]]


def test_independent_public_page_with_exact_id_subject_and_place(monkeypatch):
    monkeypatch.setattr(geo, "_robots", lambda *args: None)
    monkeypatch.setattr(geo, "_get", lambda *args:
        '<html><body><h1>Закупка №1234567 Оказание услуг по '
        'сопровождению системы 1С</h1><p>B2B-Center</p>'
        '<p>Место оказания услуг</p><p>г. Москва</p></body></html>')
    assert geo.check_public_procurement_location(NOTICE, None) == "MOSCOW"


def test_robots_block_or_unavailable_source_is_not_a_moscow_pass(monkeypatch):
    monkeypatch.setattr(geo, "_robots", lambda *args:
        (_ for _ in ()).throw(geo.ResearchBlocked("RESEARCH_ROBOTS_DISALLOWED")))
    monkeypatch.setattr(geo, "_get", lambda *args:
        pytest.fail("no fetch after robots block"))
    assert geo.check_public_procurement_location(NOTICE, None) == "UNKNOWN"


def test_geo_source_url_mismatch_stops_before_network(monkeypatch):
    bad = {**NOTICE, "source_url":
        "https://www.b2b-center.ru/app/market-next/other/tender-9999999/"}
    monkeypatch.setattr(geo, "_robots", lambda *args:
        pytest.fail("must not request a mismatched tender"))
    assert geo.check_public_procurement_location(bad, None) == "UNKNOWN"


@pytest.mark.parametrize("decision", ["UNKNOWN", "OUTSIDE", "MIXED"])
def test_writer_geo_review_fences_before_any_crm_post(monkeypatch, decision):
    states = []
    monkeypatch.setattr(bridge, "attempted_24h", lambda con: 0)
    monkeypatch.setattr(bridge, "read_candidate",
                        lambda con: ("B2B-CENTER-1234567", NOTICE))
    monkeypatch.setattr(bridge, "validate_webhook", lambda x: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda *args: True)
    monkeypatch.setattr(bridge, "exact_duplicate", lambda *args: [])
    monkeypatch.setattr(bridge, "set_review",
                        lambda con,source: states.append(source) or True)
    monkeypatch.setattr(bridge, "reserve_post",
                        lambda *args: pytest.fail("no irreversible reservation"))
    monkeypatch.setattr(bridge, "bitrix_call",
                        lambda *args: pytest.fail("no CRM call"))
    monkeypatch.setattr(geo, "check_public_procurement_location",
                        lambda notice,client: decision)
    assert bridge.deliver_one(object(),object(),"test",geo_only=True) == "GEO_" + decision
    assert states == ["B2B-CENTER-1234567"]


def test_moscow_verified_pass_keeps_original_safe_writer_contract(monkeypatch):
    events = []
    monkeypatch.setattr(bridge, "attempted_24h", lambda con: 0)
    monkeypatch.setattr(bridge, "read_candidate",
                        lambda con: ("B2B-CENTER-1234567", NOTICE))
    monkeypatch.setattr(bridge, "validate_webhook", lambda x: "https://example.invalid/rest")
    monkeypatch.setattr(bridge, "positive_duplicate_control", lambda *args: True)
    monkeypatch.setattr(bridge, "exact_duplicate", lambda *args: [])
    monkeypatch.setattr(geo, "check_public_procurement_location",
                        lambda notice,client: "MOSCOW")
    monkeypatch.setattr(bridge, "reserve_post",
                        lambda con,source: events.append("RESERVE") or True)
    monkeypatch.setattr(bridge, "confirm",
                        lambda con,source,card: events.append("CONFIRM"))
    monkeypatch.setattr(bridge, "insert_new",
                        lambda con,card: events.append("NATIVE_QUEUE") or True)

    def crm(client,base,method,params):
        if method == "crm.item.add":
            events.append("ADD")
            crm.fields = params["fields"]
            return {"result": {"item": {"id": 45}}}
        if method == "crm.item.get":
            events.append("GET")
            return {"result":{"item":{
                "id":45, "entityTypeId":1062, "categoryId":21,
                **crm.fields}}}
        pytest.fail("unexpected CRM API")
    monkeypatch.setattr(bridge, "bitrix_call",crm)
    assert bridge.deliver_one(object(),object(),"test",geo_only=True) == "ONE_REAL_HOLD_QUEUED"
    assert events == ["RESERVE","ADD","GET","NATIVE_QUEUE","CONFIRM"]


def test_geo_flag_is_explicit_opt_in_and_invalid_values_fail(monkeypatch):
    monkeypatch.setattr(bridge,"live_permitted",lambda e: True)
    flags = {
        "SERVICEIT_VPS_B2B_RECEIVER_ENABLED":"YES",
        "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK":"YES",
        "SERVICEIT_VPS_B2B_WRITER_ENABLED":"YES",
    }
    assert bridge.enabled(flags,writer=True)
    flags["SERVICEIT_B2B_MOSCOW_ONLY_ENABLED"] = "NO"
    assert bridge.enabled(flags,writer=True)
    flags["SERVICEIT_B2B_MOSCOW_ONLY_ENABLED"] = "YES"
    assert bridge.enabled(flags,writer=True)
    flags["SERVICEIT_B2B_MOSCOW_ONLY_ENABLED"] = "MAYBE"
    with pytest.raises(bridge.B2BBlocked,match="B2B_MOSCOW_ONLY_FLAG_INVALID"):
        bridge.enabled(flags,writer=True)
