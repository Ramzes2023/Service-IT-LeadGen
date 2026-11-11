"""V17: empty Brave results must not suppress independently verified Kontur pages.

No live network, CRM, model calls or guessed buyer identities in these tests.
The observed public suffixes _4 and _459 are discovery only; all accepted
facts still require the strict source identity gate in from_public_page().
"""
import httpx

from leadpilot import serviceit_b2b_public_mirrors as mirrors

ID = "4673971"
TITLE = "Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года"
URL4 = "https://zakupki.kontur.ru/" + ID + "_4"
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
    assert visited == [URL4, URL459]


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
