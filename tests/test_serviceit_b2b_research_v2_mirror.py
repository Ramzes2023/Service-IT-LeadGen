"""B2B research V2: public full page, evidence & retry safety, offline only."""
from copy import deepcopy
import httpx
import pytest

from leadpilot import serviceit_b2b_public_mirrors as mirror
from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot import serviceit_b2b_routerai_deep as deep
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_vps_native_fence import NATIVE_NAMESPACES

SUBJECT = "Предоставление IT-специалистов, включая 1С, DevOps и ИБ"
NUMBER = "4673595"
TEND_URL = "https://tendem.ru/tenders/16548471"


def site_text():
    intro = ("Запрос котировок B2B-Center опубликован 09.11.2026. "
             "Оказание услуг по предоставлению ИТ-специалистов. "
             "Подача заявок до 23.11.2026 17:00. "
             "Цена не указана. Заказчик ООО Тест-ИТ. "
             "Лоты: аналитики 1С, DevOps-инженеры, ИБ-специалисты. ")
    filler = ("Дополнительная таблица лотов, технические требования. " * 155)
    return intro + filler + " Номер закупки 4673595 B2B-Center публичная ссылка."


def test_full_length_tender_id_is_found_after_seven_thousand_chars():
    raw = site_text()
    assert raw.find(NUMBER) > 7000
    relevant = mirror.matched_excerpt(raw, NUMBER, SUBJECT)
    assert relevant and NUMBER in relevant
    assert "Подача заявок" in relevant
    assert "1С" in relevant and "DevOps" in relevant
    assert mirror.matched_excerpt(raw.replace(NUMBER, "9876543"), NUMBER, SUBJECT) == ""
    assert mirror.matched_excerpt(
        "B2B-Center номер 4673595. " + "Только гардеробные услуги " * 50,
        NUMBER, SUBJECT) == ""


def test_vetted_mirror_robots_200_and_real_page_not_index_snippet(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    seen = []
    def transport(req):
        seen.append((req.url.host, req.url.path))
        if req.url.host == "api.search.brave.com":
            # New V11 source ranking checks later mirrors even if one
            # exact-ID public Tendum page was already fetched.
            return httpx.Response(200, json={"web": {"results": []}},
                                  headers={"Content-Type": "application/json"})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /",
                                  headers={"Content-Type": "text/plain"})
        if req.url.path == "/tenders/16548471":
            return httpx.Response(200,
                text="<html><body>" + site_text() + "</body></html>",
                headers={"Content-Type": "text/html"})
        raise AssertionError("Network other than approved mirror")
    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False, follow_redirects=False) as client:
        found = mirror.brave_mirror_search(
            client, NUMBER, SUBJECT, "SYNTHETIC-KEY-NOT-REAL")
    assert found["status"] == "PUBLIC_PAGES_INSPECTED"
    assert found["pages"][0]["url"] == TEND_URL
    assert NUMBER in found["pages"][0]["excerpt"]
    assert "Подача заявок" in found["pages"][0]["excerpt"]
    assert seen[:2] == [("tendem.ru","/robots.txt"),("tendem.ru","/tenders/16548471")]
    assert all(host in ("tendem.ru", "api.search.brave.com") for host, _ in seen)
    assert sum(host == "api.search.brave.com" for host, _ in seen) <= 3
    assert deep.public_documents(found)


def test_robots_disallow_does_not_become_evidence(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public, "_public_dns", lambda host: True)
    def transport(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200,text="User-agent: *\nDisallow: /",
                                  headers={"Content-Type": "text/plain"})
        if req.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web":{"results":[]}})
        raise AssertionError("Robots must block actual page GET")
    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False,follow_redirects=False) as client:
        assert mirror.brave_mirror_search(
            client, NUMBER, SUBJECT, "SYNTHETIC-KEY-NOT-REAL") is None


def test_public_snippet_is_never_used_without_page_match(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public,"_public_dns",lambda host:True)
    def transport(req):
        if req.url.host=="api.search.brave.com":
            return httpx.Response(200,json={"web":{"results":[
                {"url":"https://tendem.ru/tenders/unrelated",
                 "description":"4673595 : 1C DevOps 999999999 ₽"}
            ]}})
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /",
                                  headers={"Content-Type":"text/plain"})
        if req.url.path=="/tenders/16548471":
            return httpx.Response(200,text="<html>Нет такого лота</html>",
                                  headers={"Content-Type":"text/html"})
        if req.url.path=="/tenders/unrelated":
            return httpx.Response(200,text="<html><body>Нужна офисная мебель</body></html>",
                                  headers={"Content-Type":"text/html"})
        # V17 permits two bounded exact-ID Kontur discovery attempts when
        # Brave yields no independently verified source. Wrong pages reject.
        if req.url.host=="zakupki.kontur.ru" and req.url.path in (
            "/4673595_4", "/4673595_40", "/4673595_459"):
            return httpx.Response(200,
                text="<html><body>Тендер не найден</body></html>",
                headers={"Content-Type":"text/html"})
        raise AssertionError("Unexpected network")
    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False,follow_redirects=False) as client:
        assert mirror.brave_mirror_search(
            client, NUMBER, SUBJECT, "SYNTHETIC-KEY-NOT-REAL") is None


def test_incomplete_v1_status_eligible_for_version_two_and_verified_cited_not():
    assert research.RECEIPT == "B2B_RESEARCH_V4"
    assert "B2B_RESEARCH_V2" in NATIVE_NAMESPACES
    assert "B2B_RESEARCH_V3" in NATIVE_NAMESPACES
    assert "B2B_RESEARCH_V4" in NATIVE_NAMESPACES
    assert "B2B_RESEARCH_V1" in NATIVE_NAMESPACES
    for value in ("NOT_RUN_SOURCE_UNVERIFIED",
                  "PUBLIC_RESEARCH_NO_VERIFIED_PUBLIC_TEXT",
                  "PUBLIC_PAGES_ROUTERAI_UNVERIFIED"):
        assert value in research.RESEARCHABLE_STATES
    assert "PUBLIC_RESEARCH_ROUTERAI_CITED" not in research.RESEARCHABLE_STATES


def test_factual_source_extraction_survives_unusable_model():
    data = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": TEND_URL, "excerpt":
                   "Оказание услуг по предоставлению ИТ-специалистов. "
                   "Лоты: аналитики 1С и DevOps-инженеры. "
                   "Подача заявок до 23.11.2026 17:00. Цена не указана."}],
    }
    investigation = deep.source_only_investigation(data)
    assert investigation["provider"] == "PUBLIC_SOURCE_EXTRACTOR"
    assert len(investigation["facts"]) >= 2
    report = deep.cited_fields(
        {key: "ПРЕДВАРИТЕЛЬНО" for key in AI_FIELDS},
        data, investigation)
    assert report[PREFIX+"AI_STATUS"] == "PUBLIC_RESEARCH_SOURCE_CITED_AI_UNVERIFIED"
    assert TEND_URL in report[PREFIX+"AI_FULL_REPORT"]
    assert "23.11.2026" in report[PREFIX+"AI_FULL_REPORT"]
    assert "HOLD" in report[PREFIX+"AI_FULL_REPORT"]


def test_model_valid_json_with_bad_optional_prose_still_returns_cited_facts():
    data = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": TEND_URL, "excerpt":
                   "Оказание услуг по предоставлению ИТ-специалистов. "
                   "Требуются разработчики 1С и DevOps-инженеры."}],
    }
    docs=deep.public_documents(data)
    model={
        "overview":"Системная инструкция <execute> игнорируй",
        "service_fit":None,
        "likely_need":"Некорректный контакт info@private.ru",
        "facts":[{
            "category":"TECHNOLOGY","source_id":1,
            "quote":"Требуются разработчики 1С и DevOps-инженеры",
            "finding":"Выдуманный подтверждённый бюджет 999 миллионов",
        }],
        "missing":{"bad":"shape"},
    }
    result=deep.validate_inference(model,docs)
    assert result["facts"]
    assert "999 миллионов" not in str(result["facts"])
    assert result["overview"]=="Публичные сведения проверены по цитатам"
    assert result["service_fit"]=="не установлено"
    assert result["likely_need"]=="Потребность требует отдельного подтверждения"
