from leadpilot import serviceit_b2b_tender_documents as tender_docs
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import dossier_document_sources
from leadpilot.serviceit_b2b_routerai_deep import public_documents, source_only_investigation

PARENT = "https://www.b2b-center.ru/market/demo/tender-4673595/"
PDF = "https://www.b2b-center.ru/files/conditions.pdf"
TEXT = "Условия оплаты: оплата после поставки с отсрочкой 30 календарных дней."
FINDINGS = {"status": "PUBLIC_PAGES_INSPECTED", "pages": [
    {"url": PARENT, "excerpt": "Тендер 4673595. Поставка серверного оборудования."},
]}


def test_document_is_available_to_existing_dossier_fact_extraction(monkeypatch):
    def inspect(client, findings, tender_id, **limits):
        assert tender_id == "4673595"
        assert limits == {"max_pages": 4, "max_downloads": 2}
        return {"documents": [
            {"url": PDF + "?token=private", "text": TEXT},
            {"url": PDF, "text": TEXT},
        ]}

    monkeypatch.setattr(tender_docs, "inspect_public_documents", inspect)
    result, originals = dossier_document_sources(None, FINDINGS, "4673595")
    assert originals == {PDF: TEXT}
    assert [page["url"] for page in public_documents(result)] == [PARENT, PDF]
    assert any(fact["url"] == PDF and fact["category"] == "PAYMENT"
               for fact in source_only_investigation(result)["facts"])


def test_unavailable_documents_preserve_initial_dossier(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(tender_docs, "inspect_public_documents", unavailable)
    assert dossier_document_sources(None, FINDINGS, "4673595") == (FINDINGS, {})
