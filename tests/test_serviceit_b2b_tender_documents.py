"""Document Intelligence public-boundary, extraction, and non-invasive CRM tests."""
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from leadpilot import serviceit_b2b_tender_documents as docs
from leadpilot import serviceit_vps_b2b_document_worker as worker
from leadpilot import serviceit_public_evidence_research as research


TENDER = "4673595"
URL = "https://www.b2b-center.ru/market/synthetic/4673595/"
ATT = "https://www.b2b-center.ru/files/tz.docx"


def make_docx():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Заказчик: ООО «Учебная фирма». '
            'Требования к участнику: предоставить сертификаты и опыт '
            'внедрения 1С.</w:t></w:r></w:p></w:body></w:document>'
        ))
    return buf.getvalue()


def test_official_b2b_public_file_download_and_docx_extraction(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda host: True)
    monkeypatch.setattr(docs, "_public_dns", lambda host: True)
    monkeypatch.setattr(docs, "_robots", lambda *args: None)
    payload = make_docx()

    def endpoint(request):
        if request.url.path == "/market/synthetic/4673595/":
            return httpx.Response(
                200, text=('<html><body>Тендер 4673595. '
                           '<a href="/files/tz.docx">Скачать ТЗ</a>'
                           '<a href="https://evil.test/private.pdf">'
                           'Скачать документ</a></body></html>'),
                headers={"content-type": "text/html"})
        if request.url.path == "/files/tz.docx":
            return httpx.Response(
                200, content=payload, headers={
                    "content-type": "application/vnd.openxmlformats-officedocument."
                                    "wordprocessingml.document"})
        return httpx.Response(404)

    findings = {"status": "PUBLIC_PAGES_INSPECTED", "pages": [
        {"url": URL, "excerpt": "Подтверждённый публичный тендер №4673595"}
    ]}
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        res = docs.inspect_public_documents(client, findings, TENDER)
    assert res["discovered"] == 1
    assert len(res["documents"]) == 1
    assert res["documents"][0]["kind"] == ".docx"
    assert "сертификаты" in res["documents"][0]["text"]
    assert "внедрения 1С" in res["documents"][0]["text"]
    assert len(res["documents"][0]["sha256"]) == 64


def test_login_page_or_unverified_tender_never_admits_documents(monkeypatch):
    monkeypatch.setattr(research, "_public_dns", lambda _: True)
    monkeypatch.setattr(docs, "_robots", lambda *args: None)
    def handler(req):
        return httpx.Response(200,
            text='<html><a href="/files/tz.docx">Скачать</a>Войти</html>',
            headers={"content-type": "text/html"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert docs.candidate_links(c, {
            "status": "PUBLIC_PAGES_INSPECTED",
            "pages": [{"url": URL, "excerpt": "4673595"}]
        }, TENDER) == []
        with pytest.raises(docs.TenderDocumentBlocked):
            docs.candidate_links(c, {
                "status": "PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
                "pages": [{"url": URL, "excerpt": "4673595"}]
            }, TENDER)


def test_zip_bomb_archive_and_fake_pdf_do_not_become_evidence():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("huge.txt", "a" * 11_000_000)
    with pytest.raises(docs.TenderDocumentBlocked):
        docs.extract_text(buf.getvalue(), ".zip")
    with pytest.raises(docs.TenderDocumentBlocked):
        docs._safe_archive(b"not pdf")


def test_xlsx_standard_cells_work_without_pip_office_suite():
    b = BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/sharedStrings.xml", (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Срок подачи</t></si></sst>'))
        z.writestr("xl/worksheets/sheet1.xml", (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c t="s"><v>0</v></c>'
            '<c><v>30.11.2026</v></c></row></sheetData></worksheet>'))
    assert "30.11.2026" in docs.extract_text(b.getvalue(), ".xlsx")
    assert "Срок подачи" in docs.extract_text(b.getvalue(), ".xlsx")


def test_report_is_cited_extra_timeline_note_not_replacement():
    rows = [{"url": ATT, "kind": ".docx", "text": (
        "Требования к участнику: предоставить сертификаты и опыт внедрения 1С."),
        "bytes": 500, "sha256": "a"*64}]
    reports = [{"provider": "RouterAI", "facts": [{
        "category": "ELIGIBILITY", "url": ATT,
        "quote": "предоставить сертификаты и опыт внедрения 1С"}]}]
    result = worker.build_report({"tender_id": TENDER}, rows, reports)
    assert "УСЛОВИЯ И ТРЕБОВАНИЯ" in result
    assert "предоставить сертификаты" in result
    assert "ФНС" in result
    assert "НЕ подставлялась" in result or "не установлены" in result
    assert "https://www.b2b-center.ru/files/tz.docx" in result
    assert "файла(ов)" in result


def test_sensitive_download_token_not_written_to_bitrix_report():
    assert "непубликуемые параметры" in worker._visible_url(
        "https://www.b2b-center.ru/file.pdf?signature=topsecret")
    assert worker._visible_url("https://www.b2b-center.ru/file.pdf") == (
        "https://www.b2b-center.ru/file.pdf")


def test_no_old_dossier_update_or_manually_unfenced_timeline():
    source = Path("leadpilot/serviceit_vps_b2b_document_worker.py").read_text("utf8")
    assert "crm.timeline.comment.add" in source
    assert "crm.timeline.comment.get" in source
    assert 'transition(\n                connection, card, "AI_INTENT", "WRITE_INTENT"' in source
    assert '"UNCERTAIN" if phase == "WRITE_INTENT"' in source
    for forbidden in (
        "crm.item.update", "crm.item.add", "crm.contact.add",
        "crm.deal.add", "tasks.task.add",
    ):
        assert forbidden not in source
    core = Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert 'env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED", "NO") == "YES"' in core
    assert "b2b_tender_documents" in core
    assert core.index("dossier = worker_once") < core.index("tender_docs =")
