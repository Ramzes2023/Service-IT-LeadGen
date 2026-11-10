"""Regressions for official-B2B ID cross-check on public tender mirrors.

No real HTTP, no login, no CRM calls, no synthetic buyer confirmation.
The evidence fixture reproduces published 4673595/4673971 public layout,
but never pretends to supply a tender contact or official FNS proof.
"""
import re
from types import SimpleNamespace
import pytest

from leadpilot import serviceit_b2b_public_mirrors as mirrors


USER_SUBJECT = "Предоставление IT-специалистов, включая 1С, DevOps и ИБ"
PUBLIC_4673595 = (
    "Контур.Закупки Тендер: Оказание услуг по предоставлению ИТ-специалистов "
    "Нижний Новгород Запрос цен № 4673595 опубликован 09.11.2026 "
    "на B2B-Center Начальная цена контракта сумма не задана "
    "Заказчик Общество с ограниченной ответственностью «Глобус-ИТ» "
    "ИНН-КПП 5260365605-526001001 "
    "Окончание подачи заявок 23.11.2026 17:00 "
    "Место поставки Нижний Новгород "
    "Объекты закупки Разработчик 1С, Junior — удалённо РФ "
    "Разработчик 1С, Middle — офис Москва"
)
PUBLIC_4673971 = (
    "Контур.Закупки Закупка серверов ЗОКИИ с ПНР "
    "и услугами расширенной ТП на 3 года "
    "Запрос цен № 4673971 опубликован 10.11.2026 "
    "на B2B-Center Заказчик ПАО Московская Биржа "
    "ИНН-КПП 7702077840-997950001 "
    "Окончание подачи заявок 17.11.2026 18:00 "
    "Объекты закупки Лот 1 серверы на условиях 100% постоплаты"
)


def test_b2b_4673595_email_adds_services_but_public_title_is_shorter():
    result = mirrors.matched_excerpt(PUBLIC_4673595, "4673595", USER_SUBJECT)
    assert "Глобус-ИТ" in result
    assert "5260365605" in result
    assert "23.11.2026" in result
    assert "Разработчик 1С" in result
    assert "4673595" in result


def test_different_b2b_purchase_same_theme_must_fail_exact_number():
    assert mirrors.matched_excerpt(PUBLIC_4673971, "4673595", USER_SUBJECT) == ""
    other = ("Контур.Закупки Оказание услуг по предоставлению ИТ-специалистов "
             "Запрос цен № 4673595 опубликован на SBERB2B "
             "Заказчик ООО «Другой» ИНН-КПП 0000000000")
    assert mirrors.matched_excerpt(other, "4673595", USER_SUBJECT) == ""


def test_same_number_on_other_platform_with_b2b_in_unrelated_sidebar():
    wrong = ("Контур.Закупки B2B-Center список площадок "
             "Оказание услуг по предоставлению ИТ-специалистов "
             "Запрос цен № 4673595 опубликован на Росэлторг "
             "Заказчик ООО «Другая компания»")
    assert mirrors.matched_excerpt(wrong, "4673595", USER_SUBJECT) == ""


def test_correct_number_but_wrong_procurement_subject_is_not_proof():
    wrong = ("Контур.Закупки Ремонт фасадов жилого дома "
             "Запрос цен № 4673595 опубликован на B2B-Center "
             "Заказчик ООО «Стройка»")
    assert mirrors.matched_excerpt(wrong, "4673595", USER_SUBJECT) == ""


def test_4673971_works_and_keeps_buyer_deadline_and_payment_words():
    matched = mirrors.matched_excerpt(
        PUBLIC_4673971, "4673971",
        "Закупка серверов ЗОКИИ с ПНР и расширенной поддержкой на 3 года")
    assert "7702077840" in matched
    assert "17.11.2026" in matched
    assert "постоплаты" in matched


def test_no_acceptance_on_index_snippet_without_independent_page_read(monkeypatch):
    # One Brave result is a URL discovery hint only. The separately fetched
    # destination is unrelated, so no source evidence can be returned.
    calls = []
    def fake_robots(client,url,hosts):
        calls.append("robots")
    def fake_get(client,url,hosts):
        calls.append("fetch")
        return "<html><body>Случайная реклама без номера закупки</body></html>"
    monkeypatch.setattr(mirrors, "_robots", fake_robots)
    monkeypatch.setattr(mirrors, "_get", fake_get)
    assert mirrors.from_public_page(
        None,"https://zakupki.kontur.ru/4673595_4",
        "4673595",USER_SUBJECT,mirror_host="zakupki.kontur.ru") is None
    assert calls == ["robots","fetch"]


def test_full_page_requires_approved_mirror_host_before_network(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("NO_NETWORK_UNAPPROVED")
    monkeypatch.setattr(mirrors,"_robots",forbidden)
    with pytest.raises(ValueError):
        mirrors.from_public_page(
            None,"https://evil.example/4673595",
            "4673595",USER_SUBJECT,mirror_host="zakupki.kontur.ru")


def test_no_guessing_contacts_or_buyer_on_coincidental_id():
    catalog = ("Каталог тендеров: предоставление ИТ-специалистов "
               "B2B-Center другие закупки: лот № 4673595 "
               "ООО «Посторонний клиент»")
    assert mirrors.matched_excerpt(catalog, "4673595", USER_SUBJECT) == ""


def test_real_public_kontur_buyer_and_checksum_valid_inn_flow_to_dossier():
    from leadpilot.serviceit_vps_b2b_deep_research import (
        public_buyer_candidate, published_buyer_inn_evidence,
    )
    excerpt=mirrors.matched_excerpt(PUBLIC_4673595, "4673595", USER_SUBJECT)
    findings={
        "status":"PUBLIC_PAGES_INSPECTED",
        "pages":[{"url":"https://zakupki.kontur.ru/4673595_4",
                  "excerpt":excerpt}],
    }
    buyer=public_buyer_candidate(findings)
    assert buyer=="ООО «Глобус-ИТ»"
    candidate=published_buyer_inn_evidence(findings)
    assert candidate is not None
    assert candidate["inn"]=="5260365605"
    assert candidate["fns_verified"] is False
    assert candidate["buyer_name"]==buyer


def test_unrelated_marketplace_cannot_assert_buyer_inn():
    from leadpilot.serviceit_vps_b2b_deep_research import (
        public_buyer_candidate, published_buyer_inn_evidence,
    )
    wrong=mirrors.matched_excerpt(
        "Контур.Закупки Оказание услуг по предоставлению ИТ-специалистов "
        "Запрос цен № 4673595 опубликован на Росэлторг "
        "Заказчик ООО «Другой» ИНН-КПП 5260365605",
        "4673595", USER_SUBJECT)
    assert not wrong
    findings={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
              "pages":[]}
    assert not public_buyer_candidate(findings)
    assert published_buyer_inn_evidence(findings) is None
