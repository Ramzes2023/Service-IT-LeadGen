from types import SimpleNamespace

import httpx
import pytest

from leadpilot import serviceit_b2b_routerai_deep as model
from leadpilot import serviceit_b2b_public_mirrors as mirrors
from leadpilot import serviceit_vps_b2b_dossier_v11_worker as worker


TENDER = "4677404"
PRIMARY = "https://www.b2b-center.ru/market/view.html?id=4677404"
WRONG = "https://www.b2b-center.ru/market/litsenzii-1s/tender-4677407/"
MIRROR = "https://zakupki.kontur.ru/4677404_40"


def pages(*urls):
    return {"status": "PUBLIC_PAGES_INSPECTED", "pages": [
        {"url": url, "excerpt": "Процедура 4677404. Закупка лицензий 1С."}
        for url in urls
    ]}


def test_wrong_tender_in_url_is_discarded_even_if_text_contains_correct_number():
    assert worker.tender_scoped_findings(pages(PRIMARY, WRONG), TENDER)["pages"] == pages(PRIMARY)["pages"]


def test_model_can_choose_document_and_mirror_and_observe_result(monkeypatch):
    trace = []

    def plan(client, config, tender, findings, actions, history):
        trace.append((tuple(actions), list(history)))
        return "search_kontur" if not history else "read_documents"

    def search(client, tender, title, key, *, allow_mirrors, index):
        assert (tender, allow_mirrors) == (TENDER, frozenset({"zakupki.kontur.ru"}))
        return pages(MIRROR, WRONG)

    def docs(client, findings, tender):
        assert {page["url"] for page in findings["pages"]} == {PRIMARY, MIRROR}
        return findings, {"https://example.org/spec.pdf": "Публичное ТЗ"}

    monkeypatch.setattr(worker, "plan_research_step", plan)
    monkeypatch.setattr(worker, "secret_file", lambda *args, **kwargs: "fake-test-token")
    monkeypatch.setattr(mirrors, "brave_mirror_search", search)
    monkeypatch.setattr(worker, "dossier_document_sources", docs)
    findings, originals, tried, count = worker.research_with_routerai(
        object(), object(), {"SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED": "YES"},
        {"tender_id": TENDER, "subject": "Приобретение лицензий 1С"}, pages(PRIMARY),
    )
    assert tried and count == 2 and originals
    assert trace[1][1] == [{"step": "search_kontur", "new_sources": 1}]
    assert "search_kontur" not in trace[1][0]
    assert [p["url"] for p in findings["pages"]] == [PRIMARY, MIRROR]


def test_model_cannot_supply_arbitrary_tools_or_urls():
    config = SimpleNamespace(provider="routerai", url="https://routerai.ru/api/v1/chat/completions",
                             key="test-token", model="test-model")
    def respond(request):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": '{"action":"https://untrusted.example"}'},
        }]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(model.DeepEvidenceError, match="ACTION_INVALID"):
            model.plan_research_step(client, config, TENDER, pages(PRIMARY), ["read_documents"], [])


def test_inconsistent_deadline_is_withheld_without_losing_other_facts():
    facts = [
        {"category": "DEADLINE", "url": PRIMARY, "quote": "Подача до 17.11.2026 16:00"},
        {"category": "DEADLINE", "url": MIRROR, "quote": "Подача до 18.11.2026 16:00"},
        {"category": "SCOPE", "url": PRIMARY, "quote": "Лицензии 1С"},
    ]
    report, sources = worker.withhold_conflicting_deadlines({"facts": facts})
    assert report["facts"] == [facts[-1]]
    assert sources == [PRIMARY, MIRROR]
