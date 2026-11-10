"""Safe public buyer label follow-up: independent contacts only, no inferred buyer."""
import httpx
from leadpilot import serviceit_vps_b2b_deep_research as runner
from leadpilot import serviceit_independent_company_research as company


def verified(text):
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://tendem.ru/tenders/synthetic",
                   "excerpt": text}],
        "contacts": [],
        "independent_company_research": None,
    }


def test_labelled_public_buyer_triggers_company_search_with_published_name(monkeypatch):
    seen = []
    def fake_search(client, **kwargs):
        seen.append(kwargs)
        return {"status": "INDEX_SEARCH_DONE",
                "contacts": [{"type": "email", "value": "info@example.org",
                              "url": "https://example.org/contact"}],
                "pages": [], "hypotheses": []}
    monkeypatch.setattr(company, "independent_company_research", fake_search)
    source = verified('Номер 4673595. Заказчик: ООО «Тестовая организация». '
                      'Предоставление ИТ-услуг 1С и DevOps.')
    assert runner.public_buyer_candidate(source) == "ООО «Тестовая организация»"
    with httpx.Client() as client:
        result = runner.followup_public_buyer_research(
            source, {"SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES"},
            client, "SYNTHETIC-BRAVE-TOKEN")
    assert len(seen) == 1
    assert seen[0]["company_name"] == "ООО «Тестовая организация»"
    assert seen[0]["source_host"] == "b2b-center.ru"
    assert result["contacts"] == []
    assert result["independent_company_research"]["contacts"][0]["value"] == "info@example.org"
    assert result["independent_company_research"]["company_name_provenance"] == (
        "PUBLIC_BUYER_LABEL_LEGAL_IDENTITY_UNVERIFIED")


def test_organizer_and_unlabelled_name_never_trigger_lookup(monkeypatch):
    called = []
    monkeypatch.setattr(company, "independent_company_research",
                        lambda *a, **k: called.append(k))
    for text in ('Организатор: ООО «Электронная площадка». ИТ-закупка.',
                 'ООО «Поставщик услуг» участвует в торгах.',
                 'Заказчик: неизвестен. Организатор: ООО «Площадка».'):
        source = verified(text)
        assert runner.public_buyer_candidate(source) == ""
        with httpx.Client() as client:
            assert runner.followup_public_buyer_research(
                source, {"SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES"},
                client, "SYNTHETIC-BRAVE-TOKEN") is source
    assert not called


def test_gates_disable_public_company_search_and_preserve_existing_results():
    source = verified('Заказчик: АО «Публичное предприятие». ИТ услуги.')
    with httpx.Client() as client:
        assert runner.followup_public_buyer_research(source, {}, client, None) is source
        source["independent_company_research"] = {"status": "INDEX_SEARCH_DONE"}
        assert runner.followup_public_buyer_research(
            source, {"SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES"}, client,
            None) is source
