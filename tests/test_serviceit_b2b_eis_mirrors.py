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
