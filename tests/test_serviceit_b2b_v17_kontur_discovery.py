"""V17: empty Brave results must not suppress independently verified Kontur pages.

No live network, CRM, model calls or guessed buyer identities in these tests.
The observed public suffixes _4, _40 and _459 are discovery only; all accepted
facts still require the strict source identity gate in from_public_page().
"""
import httpx

from leadpilot import serviceit_b2b_public_mirrors as mirrors

ID = "4673971"
TITLE = "Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года"
URL4 = "https://zakupki.kontur.ru/" + ID + "_4"
URL40 = "https://zakupki.kontur.ru/" + ID + "_40"
URL459 = "https://zakupki.kontur.ru/" + ID + "_459"
TOKEN = "synthetic-test-token-123456789"


def no_brave_hits(request):
    assert request.url.host == "api.search.brave.com"
    return httpx.Response(
        200, json={"web": {"results": []}},
        headers={"Content-Type": "application/json"})


def test_empty_brave_index_can_still_find_observed_459_variant(monkeypatch):
    called = []

    def independently_checked(client, url, tender_id, subject,
                              mirror_host="tendem.ru"):
        called.append((url, mirror_host))
        if (url, tender_id, subject, mirror_host) == (
            URL459, ID, TITLE, "zakupki.kontur.ru"
        ):
            return {"url": url, "excerpt":
                    "Запрос цен № 4673971 на B2B-Center; закупка серверов"}
        return None

    monkeypatch.setattr(mirrors, "from_public_page", independently_checked)
    with httpx.Client(transport=httpx.MockTransport(no_brave_hits),
                      trust_env=False, follow_redirects=False) as client:
        got = mirrors.brave_mirror_search(client, ID, TITLE, TOKEN)
    assert got is not None
    assert got["pages"][0]["url"] == URL459
    assert called == [(URL4, "zakupki.kontur.ru"),
                      (URL40, "zakupki.kontur.ru"),
                      (URL459, "zakupki.kontur.ru")]


def test_unverified_guessed_urls_never_become_source_evidence(monkeypatch):
    visited = []

    def rejects(client, url, tender_id, subject, mirror_host="tendem.ru"):
        visited.append(url)
        return None

    monkeypatch.setattr(mirrors, "from_public_page", rejects)
    with httpx.Client(transport=httpx.MockTransport(no_brave_hits),
                      trust_env=False, follow_redirects=False) as client:
        got = mirrors.brave_mirror_search(client, ID, TITLE, TOKEN)
    assert got is None
    assert visited == [URL4, URL40, URL459]


def test_public_candidate_does_not_trigger_second_guess(monkeypatch):
    visited = []

    def checks(client, url, tender_id, subject, mirror_host="tendem.ru"):
        visited.append(url)
        if url == URL4:
            return {"url": URL4, "excerpt":
                    "Номер закупки 4673971 B2B-Center Закупка серверов"}
        return None

    monkeypatch.setattr(mirrors, "from_public_page", checks)
    with httpx.Client(transport=httpx.MockTransport(no_brave_hits),
                      trust_env=False, follow_redirects=False) as client:
        got = mirrors.brave_mirror_search(client, ID, TITLE, TOKEN)
    assert got is not None
    assert visited == [URL4]


def test_kontur_not_allowlisted_never_fetched(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("DISALLOWED_KONTUR_FETCH")
    monkeypatch.setattr(mirrors, "from_public_page", never)
    with httpx.Client(transport=httpx.MockTransport(no_brave_hits),
                      trust_env=False, follow_redirects=False) as client:
        got = mirrors.brave_mirror_search(
            client, ID, TITLE, TOKEN, allow_mirrors=frozenset({"tendem.ru"}))
    assert got is None


def test_real_mkb_prequalification_page_is_eligible_public_evidence():
    """Published 2026-11-10 #4675475 is not a request for prices."""
    source_id = "4675475"
    subject = "Приобретение серверного оборудования для нужд МКБ"
    # Real public page line order, data on Kontur; offline fixture only.
    parts = [
        "Контур.Закупки",
        subject,
        "Предварительный квалификационный отбор № 4675475 "
        "опубликован 10.11.2026 14:53 на B2B-Center",
        "Начальная цена контракта", "сумма не задана",
        "Заказчик", "ПАО «Московский Кредитный Банк»",
        "ИНН-КПП", "7734202860-775001001",
        "Окончание подачи заявок", "16.11.2026 16:00",
        "Место поставки", "111141 Россия, г. Москва, "
        "проезд 2-й Перова Поля, д. 2 стр. 4",
    ]
    excerpt = mirrors.detailed_public_excerpt(parts, source_id, subject)
    assert "4675475" in excerpt
    assert "Московский Кредитный Банк" in excerpt
    assert "7734202860" in excerpt
    assert "16.11.2026" in excerpt
    assert "2-й Перова Поля" in excerpt
    assert "сумма не задана" in excerpt


def test_mkb_wrong_prequalification_origin_rejected_even_with_matching_id():
    source_id = "4675475"
    subject = "Приобретение серверного оборудования для нужд МКБ"
    page = ("Контур.Закупки B2B-Center в навигации "
            + subject + " Предварительный квалификационный отбор № "
            + source_id + " опубликован 10.11.2026 на Росэлторг "
            + "Заказчик ПАО «Чужой» ИНН 7734202860")
    # Do not accidentally certify buyer merely because the procurement
    # ID and topic exist on another platform.
    assert mirrors.detailed_public_excerpt(page.split(" "), source_id, subject) == ""


def test_mkb_40_link_discovery_on_empty_brave_index(monkeypatch):
    number = "4675475"
    subject = "Приобретение серверного оборудования для нужд МКБ"
    url = "https://zakupki.kontur.ru/" + number + "_40"
    visited = []
    def independently_checked(client, page, tender_id, title, mirror_host="tendem.ru"):
        visited.append(page)
        if page == url and tender_id == number and title == subject:
            return {"url": page, "excerpt":
                    "Предварительный квалификационный отбор №4675475 "
                    "опубликован на B2B-Center Заказчик ПАО МКБ "
                    "ИНН 7734202860"}
        return None
    monkeypatch.setattr(mirrors, "from_public_page", independently_checked)
    with httpx.Client(transport=httpx.MockTransport(no_brave_hits),
                      trust_env=False, follow_redirects=False) as client:
        result = mirrors.brave_mirror_search(client, number, subject, TOKEN)
    assert result is not None
    assert result["pages"][0]["url"] == url
    assert visited == [
        "https://zakupki.kontur.ru/4675475_4", url
    ]
