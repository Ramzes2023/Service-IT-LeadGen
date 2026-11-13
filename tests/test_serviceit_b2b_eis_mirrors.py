import pytest

from leadpilot import serviceit_b2b_eis_mirrors as mirrors


NOTICE = {
    "source_url": "https://www.b2b-center.ru/market/licences/tender-4677160/",
    "tender_id": "4677160",
    "subject": "Приобретение ПО и лицензий 1С для ООО Башкирэнерго",
}
PRIMARY = {
    "url": NOTICE["source_url"],
    "excerpt": "Запрос цен № 4677160. Номер извещения на ЕИС: 32616566539",
}
EXACT = "Приобретение ПО и лицензий 1С для ООО Башкирэнерго (4677160). ЕИС 32616566539."


@pytest.mark.parametrize("wrong", (
    EXACT.replace("4677160", "46771609"),
    EXACT.replace("32616566539", "32616566538"),
    "Поставка зимней спецодежды (4677160). ЕИС 32616566539.",
))
def test_both_identifiers_and_title_are_required(monkeypatch, wrong):
    requests = []

    def page(client, url, allowed):
        requests.append(url)
        assert url.split("/")[2] in allowed
        return {"text": EXACT if "tendik.ru" in url else wrong}

    monkeypatch.setattr(mirrors, "_page", page)
    result = mirrors.supplement_eis_mirrors({"pages": [PRIMARY]}, NOTICE, None)
    assert result["eis_mirror_count"] == 1
    assert result["pages"][0] == PRIMARY
    assert result["pages"][1]["url"] == "https://tendik.ru/tender/eis223/32616566539"
    assert len(requests) == 2


def test_unrelated_signed_url_does_not_trigger_mirror_discovery(monkeypatch):
    def unexpected(*args):
        pytest.fail("unexpected request")

    monkeypatch.setattr(mirrors, "_page", unexpected)
    findings = {"pages": [PRIMARY]}
    notice = {**NOTICE, "source_url": NOTICE["source_url"].replace("4677160", "4677161")}
    assert mirrors.supplement_eis_mirrors(findings, notice, None) == findings


def test_robots_block_is_ignored_and_other_mirror_is_checked(monkeypatch):
    def page(client, url, allowed):
        if "tendik.ru" in url:
            raise mirrors.ResearchBlocked("RESEARCH_ROBOTS_DISALLOWED")
        return {"text": EXACT}

    monkeypatch.setattr(mirrors, "_page", page)
    result = mirrors.supplement_eis_mirrors({"pages": [PRIMARY]}, NOTICE, None)
    assert result["eis_mirror_count"] == 1
    assert result["pages"][1]["url"] == "https://sprosradar.ru/zakupki/223/32616566539/"


def test_registry_inn_requires_buyer_label_and_agreement_between_mirrors(monkeypatch):
    def page(client, url, allowed):
        if "tendik.ru" in url:
            return {"text": EXACT + ' Заказчик ООО "Распределительные электрические сети"'}
        return {"text": EXACT + " Заказчик Распределительные электрические сети ИНН 0277071467"}

    monkeypatch.setattr(mirrors, "_page", page)
    result = mirrors.supplement_eis_mirrors({"pages": [PRIMARY]}, NOTICE, None)
    assert result["eis_registry_buyer"]["inn"] == "0277071467"
    assert "ИНН 0277071467" in mirrors.registry_buyer_note(result)
    rows = result["pages"][1:]
    wrong = {**rows[1], "excerpt": rows[1]["excerpt"].replace("Заказчик", "Организатор")}
    assert mirrors._registry_buyer([rows[0], wrong]) is None


def test_canonical_primary_redirect_preserves_eis_number(monkeypatch):
    monkeypatch.setattr(mirrors, '_page', lambda *args: pytest.fail('primary refetch'))
    canonical = {**PRIMARY, 'url': PRIMARY['url'].replace('/market/', '/app/market-next/')}
    assert mirrors._source_eis_number({'pages': [canonical]}, NOTICE, None) == '32616566539'
