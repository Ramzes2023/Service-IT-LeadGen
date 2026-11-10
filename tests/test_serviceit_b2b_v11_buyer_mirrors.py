"""No-network public procurement detail coverage regression for V11."""
import json

import httpx
import pytest

from leadpilot.serviceit_vps_b2b_deep_research import (
    public_buyer_candidate, published_buyer_inn_evidence,
)
from leadpilot import serviceit_b2b_public_mirrors as mirrors
from leadpilot import serviceit_independent_company_research as search

PUBLIC_GLOBUS = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{"url": "https://zakupki.kontur.ru/4673595_4",
               "excerpt": (
                   "Запрос цен № 4673595. Оказание услуг по предоставлению "
                   "ИТ-специалистов. Заказчик Общество с ограниченной "
                   "ответственностью «Глобус-ИТ» ИНН-КПП 5260365605-526001001 "
                   "Окончание подачи заявок 23.11.2026 17:00. "
                   "Разработчик 1С, Middle — удалённо (РФ)."
               )}]
}
PUBLIC_AGREGAT = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{"url": "https://www.b2b-center.ru/market/tender-4672802/",
               "excerpt": (
                   "Запрос предложений № 4672802. Лицензии для серверов 1С. "
                   "Организатор АО ГМЗ «Агат» Заказчики: ПАО «Агрегат» "
                   "Условия оплаты: 30 рабочих дней после поставки."
               )}]
}
PUBLIC_MOEX = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{"url": "https://zakupki.kontur.ru/4673971_459",
               "excerpt": (
                   "Запрос цен 4673971. Закупка серверов ЗОКИИ. "
                   "Заказчик ПАО Московская Биржа ИНН-КПП "
                   "7702077840-997950001. Окончание подачи 17.11.2026."
               )}]
}


def test_exact_buyer_labels_and_inn_kpp_are_read_but_not_called_fns():
    assert public_buyer_candidate(PUBLIC_GLOBUS) == "ООО «Глобус-ИТ»"
    evidence = published_buyer_inn_evidence(PUBLIC_GLOBUS)
    assert evidence["inn"] == "5260365605"
    assert evidence["buyer_name"] == "ООО «Глобус-ИТ»"
    assert evidence["fns_verified"] is False


def test_plural_b2b_buyer_never_confused_with_organizer():
    assert public_buyer_candidate(PUBLIC_AGREGAT) == "ПАО «Агрегат»"
    assert published_buyer_inn_evidence(PUBLIC_AGREGAT) is None


def test_unquoted_legal_buyer_with_next_requisites_boundary():
    assert public_buyer_candidate(PUBLIC_MOEX) == "ПАО «Московская Биржа»"
    assert published_buyer_inn_evidence(PUBLIC_MOEX)["inn"] == "7702077840"


def test_organizer_without_explicit_buyer_stays_unverified():
    result = {"status": "PUBLIC_PAGES_INSPECTED",
              "pages": [{"url": "https://tendem.ru/tenders/123",
                         "excerpt": "№ 4673971 Организатор ООО «Не заказчик» "
                                    "ИНН-КПП 5260365605-526001001"}]}
    assert public_buyer_candidate(result) == ""
    assert published_buyer_inn_evidence(result) is None


def test_detail_rank_favors_actual_buyer_inn_deadline_terms():
    terse = "Закупка 4673595. Оказание услуг по предоставлению ИТ-специалистов."
    detailed = (terse + " Заказчик ООО «Глобус-ИТ» ИНН-КПП 5260365605. "
                "Окончание подачи заявок 23.11.2026. Условия оплаты: 30 дней.")
    assert mirrors._detail_score(detailed) > mirrors._detail_score(terse)


def test_brave_discovery_keeps_two_fetched_urls_not_first_hint(monkeypatch):
    sites = {
        "https://tendem.ru/tenders/16548471": (
            "Закупка 4673595. Оказание услуг по предоставлению ИТ-специалистов"),
        "https://zakupki.kontur.ru/4673595_4": (
            "Запрос цен 4673595. Оказание услуг по предоставлению "
            "ИТ-специалистов. Заказчик ООО «Глобус-ИТ» ИНН-КПП 5260365605. "
            "Окончание подачи заявок 23.11.2026. Условия оплаты 30 дней."),
        "https://synapsenet.ru/one": (
            "Закупка 4673595. Оказание услуг по предоставлению ИТ-специалистов. "
            "Подача заявок до 23.11.2026.")
    }
    def independently_fetched(client, url, tender_id, subject, mirror_host="tendem.ru"):
        value = sites.get(url)
        return {"url": url, "excerpt": value} if value else None
    monkeypatch.setattr(mirrors, "from_public_page", independently_fetched)
    def handle(req):
        q = str(req.url.params.get("q", ""))
        if "zakupki.kontur.ru" in q:
            rows = [{"url": "https://zakupki.kontur.ru/4673595_4"}]
        elif "synapsenet.ru" in q:
            rows = [{"url": "https://synapsenet.ru/one"}]
        else:
            rows = [{"url": "https://tendem.ru/tenders/16548471"}]
        return httpx.Response(200, json={"web": {"results": rows}},
                              headers={"content-type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        data = mirrors.brave_mirror_search(
            client, "4673595", "Оказание услуг по предоставлению ИТ-специалистов",
            "key0123456789abcdef")
    assert len(data["pages"]) == 2
    assert data["pages"][0]["url"] == "https://zakupki.kontur.ru/4673595_4"
    assert len({x["url"] for x in data["pages"]}) == 2
    assert data["discovery"] == "BRAVE_PUBLIC_MIRRORS_TWO_INDEPENDENT_DETAIL_PAGES"


def test_mismatched_tender_and_missing_search_token_fail_closed():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail("no unsafe request"))) as client:
        assert mirrors.brave_mirror_search(client, "4673595",
                    "Оказание услуг", "bad") is None
