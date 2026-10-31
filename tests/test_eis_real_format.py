"""Regression scenarios modeled on the real 29 July 2026 EIS RSS shape.

Real published specimen: stufently/free-zakupki-gov-ru-monitor,
tests/fixtures/real-eis-rss.xml. These records are SYNTHETIC and prove parser
compatibility, NOT real current procurement opportunities.
"""
from datetime import date
from html import escape

import pytest

from leadpilot.eis_rss import parse_feed

TODAY = date(2026, 10, 31)
FEED_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C"
NOTICE = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=01234567890123456789"
GENERIC_TITLE = "Электронный аукцион №01234567890123456789"
SERVICE = "Оказание услуг по сопровождению конфигурации 1С:Предприятие"


def eis_rss(*, subject: str | None, title: str = GENERIC_TITLE,
            query: str = "сопровождение 1С", statute: str = "44-ФЗ",
            double_encoded: bool = False) -> bytes:
    html = (
        "<b>Параметры поиска: </b><br/><strong>Искомое слово (словосочетание): </strong>"
        + escape(query) + "<br/><strong>Закупки по: </strong>" + statute
        + "<br/><strong>Найденный результат: </strong><br/>"
    )
    if subject is not None:
        html += "<strong>Наименование объекта закупки: </strong>" + escape(subject) + "<br/>"
    html += "<strong>Размещение выполняется по: </strong>" + statute + "<br/>"
    html += "<strong>Наименование Заказчика: </strong>ГОСУДАРСТВЕННОЕ УЧРЕЖДЕНИЕ<br/>"
    body = escape(html)
    if double_encoded:
        body = escape(body)
    rss = (
        "<rss version='2.0'><channel><title>Результаты поиска в реестре</title>"
        "<item><title>" + escape(title) + "</title><link>" + escape(NOTICE)
        + "</link><description>" + body + "</description>"
        "<author>ГОСУДАРСТВЕННОЕ УЧРЕЖДЕНИЕ</author>"
        "<pubDate>Fri, 30 Oct 2026 11:00:00 +0300</pubDate>"
        "</item></channel></rss>"
    )
    return rss.encode("utf-8")


@pytest.mark.parametrize("double_encoded", [False, True])
def test_real_eis_generic_auction_title_with_1c_procurement_subject(double_encoded):
    result = parse_feed(eis_rss(subject=SERVICE, double_encoded=double_encoded),
                        feed_url=FEED_URL, today=TODAY)
    assert result["counts"]["candidates_for_identity_review"] == 1
    record = result["items"][0]
    assert record["title"] == SERVICE
    assert record["rss_title"] == GENERIC_TITLE
    assert record["procurement_subject_from_rss"] == SERVICE
    assert record["buyer_name_rss_unverified"] == "ГОСУДАРСТВЕННОЕ УЧРЕЖДЕНИЕ"
    assert record["buyer_inn"] is None and record["lead_score"] is None
    assert record["crm_ready"] is False and record["outbound_authorized"] is False


@pytest.mark.parametrize("subject", [
    "Поставка принтеров и сканеров",
    "null",
    "Поставка коробочной лицензии программы 1С:Предприятие",
    "Оказание услуг по сопровождению 1С-Битрикс",
    "Поставка лицензий без услуг сопровождения 1С:Предприятие",
])
def test_search_word_cannot_impersonate_actual_procurement_subject(subject):
    r = parse_feed(eis_rss(subject=subject), feed_url=FEED_URL, today=TODAY)
    assert r["items"] == [] and r["counts"]["qualified_small_business_leads"] == 0


def test_generic_title_without_label_is_not_buyer_intent():
    r = parse_feed(eis_rss(subject=None), feed_url=FEED_URL, today=TODAY)
    assert r["items"] == []


def test_detailed_1c_service_title_accepted_without_description_label():
    title = "Оказание услуг по технической поддержке 1С:Предприятие"
    r = parse_feed(eis_rss(subject=None, title=title), feed_url=FEED_URL, today=TODAY)
    assert r["items"][0]["title"] == title
    assert r["items"][0]["procurement_subject_from_rss"] is None
    assert r["items"][0]["buyer_fns_size"] == "UNVERIFIED"


def test_html_does_not_persuade_bot_to_promote_government_buyer_to_sme():
    r = parse_feed(eis_rss(subject=SERVICE), feed_url=FEED_URL, today=TODAY)
    assert r["counts"]["qualified_small_business_leads"] == 0
    assert r["counts"]["crm_ready"] == 0
    assert r["crm_writes"] == "NO" and r["outbound_messages"] == "NO"
