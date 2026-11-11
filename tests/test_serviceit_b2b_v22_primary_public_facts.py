"""V22 independently read B2B primary pages: real conditions vs login gates."""
from leadpilot.serviceit_b2b_routerai_deep import (
    b2b_primary_detail_facts, extract_source_facts, source_only_investigation,
)

MKB="https://www.b2b-center.ru/market/priobretenie-servernogo-oborudovaniia-dlia-nuzhd-mkb/tender-4675475/"
SERVER="https://www.b2b-center.ru/market/postavka-servera-zaiavka-9236/tender-4674861/"

def rows(url,text):
    return [{"url":url,"text":text,"source_id":1}]


def mkb():
    return ("Предварительный квалификационный отбор № 4675475 "
            "Приобретение серверного оборудования для нужд МКБ. "
            "Актуально до: 16.11.2026 16:00 "
            "Организатор: ПАО «МОСКОВСКИЙ КРЕДИТНЫЙ БАНК». "
            "Доступно зарегистрированным пользователям: "
            "Для просмотра зарегистрируйтесь. Условия участия "
            "Зарегистрируйтесь, чтобы увидеть условия участия.")


def test_pko_type_and_deadline_without_fabricating_buyer_contract():
    facts=b2b_primary_detail_facts(rows(MKB,mkb()))
    categories={x["category"] for x in facts}
    assert {"OTHER","DEADLINE"} <= categories
    assert all(x["url"]==MKB and x["quote"] in mkb() for x in facts)
    assert any("Предварительный квалификационный отбор" in x["quote"]
               for x in facts)
    assert any("16.11.2026 16:00" in x["quote"] for x in facts)
    assert any("Доступно зарегистрированным" in x["quote"] for x in facts)
    assert all(x["category"] != "BUYER" for x in facts)
    assert all(x["category"] != "ELIGIBILITY" for x in facts)


def test_server_purchase_payment_delivery_and_document_requirement():
    text=("Запрос предложений № 4674861 Поставка сервера заявка 9236. "
          "Общая стоимость закупки: Без указания цены. "
          "Дата окончания подачи заявок: 17.11.2026 12:00. "
          "При выборе победителя учитывается: Цена с НДС "
          "Подгрузка документации к заявке обязательна "
          "Организатор не будет рассматривать заявки без документации: Да. "
          "Условия оплаты: Отсрочка 30 кал. дней. "
          "Условия поставки: г.Москва ул. Новохохловская 89с3")
    facts=b2b_primary_detail_facts(rows(SERVER,text))
    cats={f["category"] for f in facts}
    assert {"OTHER","DEADLINE","BUDGET","PAYMENT","LOCATION","DOCUMENTS","EVALUATION"} <= cats
    assert all(f["quote"] in text for f in facts)
    assert any("Без указания цены" in f["quote"] and f["category"]=="BUDGET"
               for f in facts)
    assert not any("0,00 ₽" in f["quote"] for f in facts)


def test_primary_source_requires_url_id_match_and_readable_text():
    assert b2b_primary_detail_facts(rows(
        SERVER,mkb()))==[]
    assert b2b_primary_detail_facts(rows(
        "https://example.test/tender-4675475/",mkb()))==[]
    assert b2b_primary_detail_facts(rows(
        "https://www.b2b-center.ru/search/servery/",mkb()))==[]


def test_registration_prompt_is_not_false_eligibility_in_source_only():
    text=mkb()
    raw=rows(MKB,text)
    facts=extract_source_facts(raw)
    assert any("16.11.2026" in f["quote"] for f in facts)
    assert not any(f["category"]=="ELIGIBILITY" and
                   "Зарегистрируйтесь" in f["quote"] for f in facts)
    findings={"status":"PUBLIC_PAGES_INSPECTED","pages":[
        {"url":MKB,"excerpt":text}]}
    source=source_only_investigation(findings)
    assert any(f["category"]=="DEADLINE" for f in source["facts"])
    assert not any(f["category"]=="BUYER" and "организатор" in f["quote"].lower()
                   for f in source["facts"])
