"""V18 offline evidence pipeline: Brave indexed context, guarded browser and CRM.

No external web, CRM, model, paid API calls or production changes.
"""
import httpx
import pytest

from leadpilot import serviceit_b2b_tender_index_v18 as index
from leadpilot import serviceit_b2b_public_mirrors as mirrors
from leadpilot import serviceit_public_evidence_research as public
from leadpilot.serviceit_b2b_dossier_v11 import compose
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

ID="4675475"
TITLE="Приобретение серверного оборудования для нужд МКБ"
URL="https://zakupki.kontur.ru/4675475_40"
KEY="SYNTHETIC-V18-API-KEY"
MARKET="https://www.b2b-center.ru/market/test/tender-4675475/"


def indexed_payload():
    return {"grounding":{"generic":[
        {"url":URL,"title":"Предварительный квалификационный отбор №4675475: "+TITLE,
         "snippets":[
             "B2B-Center предварительный квалификационный отбор № 4675475. "
             + TITLE + ". Заказчик: ПАО «Московский Кредитный Банк»\n"
             "ИНН: 7734202860\n"
             "Окончание подачи заявок: 16.11.2026 16:00 МСК\n"
             "Место поставки: Москва, 2-й проезд Перова Поля, 2, стр.4\n"
             "Начальная цена: сумма не задана"]},
        {"url":"https://synapsenet.ru/someone-else",
         "title":"4675475 закупить стулья и мебель",
         "snippets":["B2B-Center 4675475 офисная мебель, стулья"]},
        {"url":"https://somewhere-unknown.ru/4675475",
         "title":"Тот же номер 4675475",
         "snippets":[TITLE+" B2B-Center Заказчик: поддельный"]},
    ]},"sources":{URL:{"hostname":"zakupki.kontur.ru"},
        "https://synapsenet.ru/someone-else":{"hostname":"synapsenet.ru"},
        "https://somewhere-unknown.ru/4675475":{"hostname":"somewhere-unknown.ru"}}}


def test_one_context_call_and_sources_are_unverified_index_leads():
    calls=[]
    def answer(req):
        calls.append(str(req.url))
        assert req.url.path=="/res/v1/llm/context"
        assert req.headers["X-Subscription-Token"]==KEY
        assert ID in req.url.params["q"]
        return httpx.Response(200,json=indexed_payload(),
                              headers={"content-type":"application/json"})
    with httpx.Client(transport=httpx.MockTransport(answer),
                      trust_env=False,follow_redirects=False) as client:
        result=index.tender_index_context(client,ID,TITLE,KEY)
    assert result["status"]=="INDEX_CANDIDATES"
    assert result["search_requests"]==1
    assert len(calls)==1
    assert len(result["candidates"])==1
    row=result["candidates"][0]
    assert row["evidence_level"]=="INDEX_ONLY_UNVERIFIED"
    assert row["url"]==URL
    assert {x["category"] for x in row["claims"]}>={"BUYER","INN","DEADLINE","LOCATION","BUDGET"}


def test_context_quota_429_never_retries():
    calls=[]
    def answer(req):
        calls.append(str(req.url))
        return httpx.Response(429,headers={"content-type":"application/json"})
    with httpx.Client(transport=httpx.MockTransport(answer),
                      trust_env=False) as client:
        data=index.tender_index_context(client,ID,TITLE,KEY)
    assert len(calls)==1
    assert data["status"]=="INDEX_QUOTA_OR_AUTH"
    assert data["candidates"]==[]


def test_web_pages_are_still_required_for_approved_verified_facts(monkeypatch):
    monkeypatch.setattr(public,"_public_dns",lambda _:True)
    def answer(req):
        if req.url.host=="api.search.brave.com":
            return httpx.Response(200,json={"web":{"results":[]}},
                                  headers={"content-type":"application/json"})
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nDisallow: /",
                                  headers={"content-type":"text/plain"})
        raise AssertionError("NO_DIRECT_PAGE_ALLOWED")
    hint=index.tender_index_context
    with httpx.Client(transport=httpx.MockTransport(answer),trust_env=False) as client:
        result=mirrors.brave_mirror_search(client,ID,TITLE,KEY,
                index={"candidates":indexed_payload()["grounding"]["generic"]})
    assert result is None


def test_browser_renders_only_robots_approved_200_js_shell(monkeypatch):
    monkeypatch.setattr(public,"_public_dns",lambda _:True)
    pages=[]
    def browser(url):
        pages.append(url)
        return {"browser_unverified":True,"text":
                "Запрос цен № 4675475. "+TITLE+". "
                "B2B-Center площадка опубликована. "
                "Заказчик: ПАО «Московский Кредитный Банк» "
                "Окончание подачи заявок 16.11.2026 16:00"}
    def answer(req):
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /",
                                  headers={"content-type":"text/plain"})
        if req.url.path=="/4675475_40":
            return httpx.Response(200,text="<html><body><script>render()</script></body></html>",
                                  headers={"content-type":"text/html"})
        raise AssertionError("NO_OTHER_NETWORK "+str(req.url))
    with httpx.Client(transport=httpx.MockTransport(answer),trust_env=False) as client:
        result=mirrors._from_page_or_public_browser(
            client,URL,ID,TITLE,"zakupki.kontur.ru",browser)
    assert result and result["source_kind"]=="BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED"
    assert ID in result["excerpt"]
    assert pages==[URL]


def test_browser_cannot_rerender_after_robots_denial(monkeypatch):
    monkeypatch.setattr(public,"_public_dns",lambda _:True)
    def answer(req):
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nDisallow: /",
                                  headers={"content-type":"text/plain"})
        raise AssertionError("UNEXPECTED_PAGE_FETCH")
    def browser(url):
        pytest.fail("BROWSER_MUST_NOT_BYPASS_ROBOTS")
    with httpx.Client(transport=httpx.MockTransport(answer),trust_env=False) as client:
        with pytest.raises(public.ResearchBlocked):
            mirrors._from_page_or_public_browser(
                client,URL,ID,TITLE,"zakupki.kontur.ru",browser)


def test_index_facts_are_visible_but_never_promoted_to_verified_in_bitrix():
    hint=index._safe_index_candidate(
        URL,"Предварительный квалификационный отбор №4675475 "+TITLE,
        "B2B-Center отбор 4675475. "+TITLE+
        "\nЗаказчик: ПАО «Московский Кредитный Банк»\nИНН: 7734202860",
        ID,TITLE)
    assert hint
    findings={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
              "pages":[],"indexed_candidates":[hint]}
    prior={x:"PREVIOUS_KEEP" for x in AI_FIELDS}
    prior[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_NO_VERIFIED_PUBLIC_TEXT"
    notice={"source":"B2B-Center","tender_id":ID,"source_url":MARKET,
            "subject":TITLE,"source_service_groups":["SERVERS"]}
    fields,coverage=compose(notice,prior,findings,{"facts":[]})
    brief=fields[PREFIX+"AI_MANAGER_BRIEF"]
    report=fields[PREFIX+"AI_FULL_REPORT"]
    assert "ИНДЕКС BRAVE" in brief and "7734202860" in brief
    assert "НЕ ПОДТВЕРЖДЕНО" in report
    assert "ИНН: нет подтверждённой связки" in report
    assert "ИНН: 7734202860 (публичный кандидат" not in report
    assert fields[PREFIX+"AI_STATUS"]==prior[PREFIX+"AI_STATUS"]
    assert coverage["buyer_public"] is False
    assert set(fields)==AI_FIELDS
