"""V16: newly read public procurement detail must be broad, exact-ID and non-invasive."""
import httpx

from leadpilot import serviceit_b2b_public_mirrors as mirrors

ID = "4673595"
TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
URL = "https://zakupki.kontur.ru/4673595_4"


def page():
    parts = [
        "Контур.Закупки",
        TITLE,
        "Запрос цен № 4673595 опубликован 09.11.2026 на B2B-Center",
        "Начальная цена контракта", "сумма не задана",
        "Заказчик", "Общество с ограниченной ответственностью «Глобус-ИТ»",
        "ИНН-КПП", "5260365605-526001001",
    ]
    parts += ["Публичный текст карточки"] * 350
    parts += [
        "Окончание подачи заявок", "23.11.2026 17:00",
        "Место поставки", "Нижегородская обл, Нижний Новгород",
        "Разработчик 1С, Junior — удалённо (РФ)", "1 чел-час",
        "Разработчик 1С, Junior — офис (Москва)", "1 чел-час",
        "Системный аналитик 1С, Middle — удалённо (РФ)", "1 чел-час",
        "Методолог 1С, Senior — офис (Москва)", "1 чел-час",
        "Тестировщик-консультант 1С, Middle — удалённо (РФ)", "1 чел-час",
        "Архитектор 1С, Lead — офис (Москва)", "1 чел-час",
        "DevOps-инженер 1С, Senior — удалённо (РФ)", "1 чел-час",
        "Технический лидер 1С, Lead — офис (Москва)", "1 чел-час",
        "ML-инженер, Senior — удалённо (РФ)", "1 чел-час",
        "MLOps-инженер, Lead — офис (Москва)", "1 чел-час",
        "Data Scientist, Senior — удалённо (РФ)", "1 чел-час",
        "Разработчик AI / LLM-сервисов, Middle — офис (Москва)", "1 чел-час",
        "Инженер данных для ML, Senior — удалённо (РФ)", "1 чел-час",
        "Tech Lead AI / ML, Lead — офис (Москва)", "1 чел-час",
        "Аналитик AI (бизнес/системный), Middle — удалённо (РФ)", "1 чел-час",
        "DevOps-инженер, Senior — офис (Москва)", "1 чел-час",
        "DevSecOps / Security Champion, Middle — удалённо (РФ)", "1 чел-час",
        "Сетевой инженер, Senior — офис (Москва)", "1 чел-час",
        "Инженер WAF, Lead — удалённо (РФ)", "1 чел-час",
        "Условия участия", "На площадке требуется заявка участника.",
        "Критерии оценки", "Оценка по указанным документам заявки.",
    ]
    return parts


def test_diverse_roles_and_geography_from_long_verified_page():
    value = mirrors.detailed_public_excerpt(page(), ID, TITLE)
    assert "4673595" in value and "5260365605" in value
    assert "сумма не задана" in value
    assert "23.11.2026" in value and "Нижний Новгород" in value
    assert "Разработчик 1С" in value
    assert "MLOps-инженер" in value
    assert "Data Scientist" in value
    assert "DevSecOps" in value
    assert "офис (Москва)" in value and "удалённо (РФ)" in value
    assert len(value) <= 4600


def test_no_matching_id_or_wrong_platform_means_zero_usable_content():
    assert mirrors.detailed_public_excerpt(
        [x.replace(ID, "4673596") for x in page()], ID, TITLE) == ""
    assert mirrors.detailed_public_excerpt(
        [x.replace("B2B-Center", "ETP OTHER") for x in page()], ID, TITLE) == ""


def test_direct_exact_kontur_is_fetched_when_brave_search_fails(monkeypatch):
    calls = []
    def evidence(client, url, tender_id, subject, mirror_host="tendem.ru"):
        calls.append((url, mirror_host))
        if url == URL and tender_id == ID and subject == TITLE:
            return {"url": url, "excerpt": "B2B-Center: заказчик ООО «Глобус-ИТ» "
                    "ИНН 5260365605, срок 23.11.2026"}
        return None
    monkeypatch.setattr(mirrors, "from_public_page", evidence)
    def no_search(req):
        assert req.url.host == "api.search.brave.com"
        return httpx.Response(429, text="quota")
    with httpx.Client(transport=httpx.MockTransport(no_search),
                      trust_env=False, follow_redirects=False) as client:
        out = mirrors.brave_mirror_search(
            client, ID, TITLE, "sample-token-123456789")
    assert out is not None
    assert out["pages"][0]["url"] == URL
    assert (URL, "zakupki.kontur.ru") in calls
    assert out["discovery"] == "BRAVE_PUBLIC_MIRRORS_TWO_INDEPENDENT_DETAIL_PAGES"


def test_brave_snippet_does_not_override_rejected_independent_fetch(monkeypatch):
    def nothing(client, url, tender_id, subject, mirror_host="tendem.ru"):
        return None
    monkeypatch.setattr(mirrors, "from_public_page", nothing)
    def index(req):
        return httpx.Response(200, json={
            "web": {"results": [{"url": URL, "description":
             "ООО Глобус-ИТ ИНН 5260365605"}]}},
            headers={"content-type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(index),
                      trust_env=False, follow_redirects=False) as client:
        assert mirrors.brave_mirror_search(
            client, ID, TITLE, "sample-token-123456789") is None
