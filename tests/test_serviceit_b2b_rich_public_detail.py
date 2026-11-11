"""Long PUBLIC tender details are needed for complete buyer/INN/geo/IT matrix.

These are offline fixtures modelled on published tender #4673595.
No login, no snippets, no customer data, no provider request, no Bitrix.
"""
import httpx
import pytest
from leadpilot import serviceit_b2b_public_mirrors as mirror
from leadpilot import serviceit_b2b_routerai_deep as deep
from leadpilot.serviceit_vps_b2b_deep_research import (
    public_buyer_candidate, published_buyer_inn_evidence,
)

TITLE="Предоставление IT-специалистов, включая 1С, DevOps и ИБ"
ID="4673595"
URL="https://zakupki.kontur.ru/4673595_4"


def detailed_parts():
    parts=[
        "Контур.Закупки",
        "Оказание услуг по предоставлению ИТ-специалистов",
        "Запрос цен № 4673595 опубликован 09.11.2026 15:56 на B2B-Center",
        "Начальная цена контракта","сумма не задана",
        "Заказчик",
        "Общество с ограниченной ответственностью «Глобус-ИТ»",
        "ИНН-КПП", "5260365605-526001001",
        "ОГРН","1135260012337",
    ]
    # The short old excerpt has 1C and a few roles but misses the later
    # remote/office requirements, 70+ lot rows and end-of-page sections.
    parts.extend(["Баннеры и навигация публичной карточки"] * 315)
    parts += [
        "Окончание подачи заявок","23.11.2026 17:00",
        "Местонахождение",
        "603024, Нижегородская область, Нижний Новгород, Казанская набережная, 5",
        "Место поставки","Нижегородская обл, г. Нижний Новгород",
        "Объекты закупки","Наименование", "Количество",
        "Разработчик 1С, Junior — удалённо (РФ)", "1 чел-час",
        "Разработчик 1С, Middle — офис (Москва)", "1 чел-час",
        "Архитектор 1С, Lead — офис (Москва)", "1 чел-час",
        "DevOps-инженер, Senior — удалённо (РФ)", "1 чел-час",
        "MLOps-инженер, Lead — офис (Москва)", "1 чел-час",
        "Data Scientist, Senior — удалённо (РФ)", "1 чел-час",
        "Инженер данных для ML, Middle — офис (Москва)", "1 чел-час",
        "Аналитик AI (бизнес/системный), Lead — офис (Москва)", "1 чел-час",
        "Техническое задание","Файл доступен только на исходной площадке",
        "Похожие закупки",
        "Не относящаяся к заявке цена 97 000 000 руб"
    ]
    return parts


def test_full_detail_buyer_and_deep_items_not_lost_when_page_exceeds_seven_k():
    parts=detailed_parts()
    assert len(" ".join(parts)) > 7000
    old=mirror.matched_excerpt(" ".join(parts),ID,TITLE)
    assert "4673595" in old
    assert "MLOps-инженер, Lead" not in old
    new=mirror.detailed_public_excerpt(parts,ID,TITLE)
    assert "4673595" in new
    assert "Глобус-ИТ" in new
    assert "5260365605" in new
    assert "23.11.2026" in new
    assert "Нижний Новгород" in new
    assert "Разработчик 1С, Junior" in new
    assert "DevOps-инженер, Senior" in new
    assert "MLOps-инженер, Lead" in new
    assert "Data Scientist, Senior" in new
    assert "офис (Москва)" in new and "удалённо (РФ)" in new
    assert "97 000 000" not in new
    assert len(new)<=4600


def test_public_buyer_and_checksum_inn_remain_correct_after_extraction():
    excerpt=mirror.detailed_public_excerpt(detailed_parts(),ID,TITLE)
    findings={"status":"PUBLIC_PAGES_INSPECTED",
              "pages":[{"url":URL,"excerpt":excerpt}]}
    assert public_buyer_candidate(findings)=="ООО «Глобус-ИТ»"
    claim=published_buyer_inn_evidence(findings)
    assert claim["inn"]=="5260365605"
    assert claim["fns_verified"] is False
    docs=deep.public_documents(findings)
    assert docs and docs[0]["url"]==URL
    # Source-only and AI citation are sourced from the fetched document.
    assert len(docs[0]["text"])<=deep.MAX_DOCUMENT_CHARS
    source_only=deep.source_only_investigation(findings)
    assert any(f["category"] in ("LOCATION","TECHNOLOGY")
               for f in source_only["facts"])
    assert all(f["quote"] in docs[0]["text"] for f in source_only["facts"])


def test_false_source_platform_or_different_id_never_accepts_long_details():
    rows=detailed_parts()
    wrong_number=[part.replace("4673595","4673596") for part in rows]
    assert mirror.detailed_public_excerpt(wrong_number,ID,TITLE)==""
    wrong_platform=[part.replace("на B2B-Center","на Росэлторг")
                    for part in rows]
    assert mirror.detailed_public_excerpt(wrong_platform,ID,TITLE)==""
    wrong_subject="Ремонт кровли школы №12"
    assert mirror.detailed_public_excerpt(rows,ID,wrong_subject)==""


def test_detail_must_be_from_independently_fetched_robots_approved_html(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public,"_public_dns",lambda h:True)
    visited=[]
    def transport(req):
        visited.append((req.url.host,req.url.path))
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /\n",
                                  headers={"content-type":"text/plain"})
        if req.url.path=="/4673595_4":
            # Full text nodes behind a verified public URL; never search
            # snippets or browser-only screenshots.
            html="<html><body>"+"".join(
                "<div>"+str(x)+"</div>" for x in detailed_parts())+"</body></html>"
            return httpx.Response(200,text=html,
                                  headers={"content-type":"text/html"})
        raise AssertionError("NO_EXTRA_OR_UNKNOWN_HOST")
    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False,follow_redirects=False) as client:
        result=mirror.from_public_page(client,URL,ID,TITLE,
                                      mirror_host="zakupki.kontur.ru")
    assert result["source_kind"]=="INDEPENDENT_PUBLIC_MIRROR"
    assert "MLOps-инженер" in result["excerpt"]
    assert visited==[("zakupki.kontur.ru","/robots.txt"),
                     ("zakupki.kontur.ru","/4673595_4")]


def test_robots_disallow_never_fetches_real_detail(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public,"_public_dns",lambda h:True)
    calls=[]
    def transport(req):
        calls.append(req.url.path)
        assert req.url.path=="/robots.txt"
        return httpx.Response(200,text="User-agent: *\nDisallow: /\n",
                              headers={"content-type":"text/plain"})
    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False,follow_redirects=False) as client:
        with pytest.raises(public.ResearchBlocked):
            mirror.from_public_page(client,URL,ID,TITLE,
                                    mirror_host="zakupki.kontur.ru")
    assert calls==["/robots.txt"]


def test_when_id_is_at_end_still_retained_with_verified_lot_details():
    intro="Тендем Закупка Оказание услуг по предоставлению ИТ-специалистов "
    parts=[intro,"B2B-Center запрос котировок",
           "Разработчик 1С, Junior — удалённо (РФ). DevOps-инженеры 1С"] + (
           ["Подробные позиции и условия закупки"]*500)
    parts+=["Номер у источника 4673595"]
    value=mirror.detailed_public_excerpt(parts,ID,TITLE)
    assert ID in value and len(value)<=4600
