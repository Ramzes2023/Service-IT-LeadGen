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


def test_new_b2b_center_split_headings_restore_teams_payment_and_moscow_delivery():
    """No AI is needed for exact words on a real public tender layout."""
    url = ("https://www.b2b-center.ru/app/market-next/"
           "priobretenie-litsenzii-teams-prodlenie/tender-4677923/")
    text = (
        'B2B-Center Приобретение лицензий Teams (продление) '
        'Закупка № 4677923 Общая сумма закупки Не указана, ₽ '
        'Организатор АО «АЗОТ-ВЗРЫВ» '
        'Условия оплаты предпочтительно отсрочка 30 календарных дней, '
        'предоплата в исключительных случаях возможна '
        'Условия поставки / оказания услуг электронная поставка '
        'Адрес поставки / оказания услуг Россия, г. Москва, '
        '121151, ул Можайский Вал, д. 8, этаж 11 '
        'Окончание приёма заявок 20.11.2026 13:59'
    )
    facts = b2b_primary_detail_facts(rows(url, text))
    assert any(x["category"] == "DEADLINE" and "20.11.2026 13:59" in x["quote"]
               for x in facts)
    assert any(x["category"] == "PAYMENT" and "отсрочка 30" in x["quote"]
               for x in facts)
    assert any(x["category"] == "LOCATION" and "электронная поставка" in x["quote"]
               for x in facts)
    assert any(x["category"] == "LOCATION" and "Можайский Вал" in x["quote"]
               for x in facts)
    assert not any(x["category"] == "BUYER" for x in facts)
    assert all(x["quote"] in text and x["url"] == url for x in facts)


def test_original_b2b_source_reverified_with_late_fields_without_paid_search(monkeypatch):
    """A 5k public detail is not reduced to its navigation before RouterAI."""
    import httpx
    from leadpilot import serviceit_public_evidence_research as evidence
    from leadpilot.serviceit_vps_b2b_deep_research import verified_original_b2b_details
    from leadpilot.serviceit_b2b_routerai_deep import public_documents
    monkeypatch.setattr(evidence, "_public_dns", lambda _: True)
    title = "Приобретение лицензий Teams (продление)"
    url = ("https://www.b2b-center.ru/app/market-next/"
           "priobretenie-litsenzii-teams-prodlenie/tender-4677923/")
    parts = [
        "B2B-Center", title, "Закупка № 4677923",
        "Общая сумма закупки", "Не указана, ₽",
        "Организатор", "АО «АЗОТ-ВЗРЫВ»",
    ] + ["Навигация по открытому каталогу закупок"] * 128 + [
        "Условия оплаты",
        "предпочтительно отсрочка 30 календарных дней, предоплата возможна",
        "Условия поставки / оказания услуг", "электронная поставка",
        "Адрес поставки / оказания услуг",
        "Россия, г. Москва, ул Можайский Вал, д. 8, этаж 11",
        "Окончание приёма заявок", "20.11.2026 13:59",
    ]
    assert len(" ".join(parts)) > 4000
    html = "<html><body>" + "".join("<div>" + part + "</div>" for part in parts) + "</body></html>"
    requested = []
    def transport(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n",
                                  headers={"content-type": "text/plain"})
        assert request.url.path.endswith("/tender-4677923/")
        return httpx.Response(200, text=html, headers={"content-type":"text/html"})
    old = {"status":"PUBLIC_PAGES_INSPECTED",
           "pages":[{"url":url,"excerpt":"B2B-Center Закупка № 4677923 " + title}]}
    notice={"tender_id":"4677923","source_url":url,"subject":title}
    with httpx.Client(transport=httpx.MockTransport(transport),
                      follow_redirects=False,trust_env=False) as client:
        improved = verified_original_b2b_details(old, notice, client)
    excerpt = improved["pages"][0]["excerpt"]
    assert improved["original_b2b_detail_independently_read"] is True
    assert len(excerpt) <= 4600
    for required in ("20.11.2026 13:59", "отсрочка 30", "Можайский Вал",
                     "электронная поставка", "АЗОТ-ВЗРЫВ"):
        assert required in excerpt
    docs=public_documents(improved)
    assert docs and "Можайский Вал" in docs[0]["text"]
    assert requested == ["/robots.txt", "/app/market-next/"
                         "priobretenie-litsenzii-teams-prodlenie/tender-4677923/"]


def test_original_b2b_detail_fails_closed_when_robots_or_id_wrong(monkeypatch):
    import httpx
    from leadpilot import serviceit_public_evidence_research as evidence
    from leadpilot.serviceit_vps_b2b_deep_research import verified_original_b2b_details
    monkeypatch.setattr(evidence, "_public_dns", lambda _: True)
    url="https://www.b2b-center.ru/market/licenses/tender-4677923/"
    old={"status":"PUBLIC_PAGES_INSPECTED","pages":[{"url":url,"excerpt":"Original unchanged"}]}
    notice={"source_url":url,"tender_id":"4677923",
            "subject":"Приобретение лицензий Teams (продление)"}
    def denied(request):
        assert request.url.path == "/robots.txt"
        return httpx.Response(200,text="User-agent: *\nDisallow: /\n",
                              headers={"content-type":"text/plain"})
    with httpx.Client(transport=httpx.MockTransport(denied),
                      trust_env=False,follow_redirects=False) as client:
        assert verified_original_b2b_details(old,notice,client)==old
    wrong=dict(notice,tender_id="4677924")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("NO_REQUEST"))
    )) as client:
        assert verified_original_b2b_details(old,wrong,client)==old
