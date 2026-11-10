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


def test_late_document_terms_are_retained_and_sampled_for_routerai():
    """Long technical attachments must not lose late eligibility/deadline."""
    filler = "Нейтральное описание услуг по сопровождению системы. " * 210
    late = ("Критерии оценки: подтвержденный опыт внедрения 1С. "
            "Место исполнения: Республика Татарстан. "
            "Срок подачи заявок: 29.11.2026. "
            "Условия оплаты: после приемки работ.")
    text = filler + late
    assert len(text) > 5500
    sample = worker._priority_excerpt(text)
    assert len(sample) <= 4500
    assert "Критерии оценки" in sample
    assert "Республика Татарстан" in sample
    assert "29.11.2026" in sample
    assert "после приемки работ" in sample

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' +
            text + '</w:t></w:r></w:p></w:body></w:document>'
        ))
    extracted = docs.extract_text(buf.getvalue(), ".docx")
    assert "29.11.2026" in extracted
    assert "Республика Татарстан" in extracted


def test_fact_first_document_dossier_skips_repeated_empty_sections():
    document_url = "https://www.b2b-center.ru/files/terms.pdf"
    document_text = (
        "Требования к участнику: три завершённых проекта внедрения 1С. "
        "Критерии оценки: релевантный опыт 70 процентов. "
        "Регион оказания услуг: Республика Татарстан.")
    docs_read = [{"url": document_url, "kind": ".pdf",
                  "bytes": 1800, "sha256": "b" * 64, "text": document_text}]
    report = worker.build_report(
        {"tender_id": TENDER}, docs_read,
        [{"provider": "PUBLIC_SOURCE_EXTRACTOR", "facts": [
            {"category": "ELIGIBILITY", "url": document_url,
             "quote": "три завершённых проекта внедрения 1С"},
            {"category": "EVALUATION", "url": document_url,
             "quote": "релевантный опыт 70 процентов"},
            {"category": "LOCATION", "url": document_url,
             "quote": "Регион оказания услуг: Республика Татарстан"},
        ]}])
    assert "ПАСПОРТ" not in report
    assert "Республика Татарстан" in report
    assert "КРИТЕРИИ ОЦЕНКИ И ВЫБОРА" in report
    assert "ЧТО ЕЩЁ НЕ ПОДТВЕРЖДЕНО" in report
    assert report.count("Нет подтверждённых цитат:") == 1
    assert "Не найдено подтверждённых цитат" not in report
    assert document_url in report
    assert "достоверный ИНН документами не установлены" in report


def test_public_document_bytes_are_prepared_as_exact_bitrix_files():
    import base64
    import hashlib
    payload = make_docx()
    item = {"url": ATT, "kind": ".docx", "text": "Текст из документа",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload), "payload": payload}
    files, manifest = worker.prepare_public_attachments([item], TENDER)
    assert files == [["b2b-4673595-document-1.docx",
                      base64.b64encode(payload).decode("ascii")]]
    assert manifest == [{"name": "b2b-4673595-document-1.docx",
                         "size": len(payload)}]
    assert worker.verify_timeline_attachments(
        {"FILES": {"739": {"id": 739, "name": manifest[0]["name"],
                           "size": len(payload)}}}, manifest) == 1


def test_attachment_hash_size_missing_data_and_bitrix_partial_readback_block():
    import hashlib
    raw = b"%PDF-" + b"public educational tender" * 8
    record = {"url": "https://www.b2b-center.ru/files/terms.pdf",
              "kind": ".pdf", "bytes": len(raw), "payload": raw,
              "sha256": hashlib.sha256(raw).hexdigest()}
    files, manifest = worker.prepare_public_attachments([record], TENDER)
    assert files and manifest
    for broken in (
        {**record, "payload": raw + b"evil"},
        {**record, "bytes": len(raw) + 1},
        {**record, "sha256": "a" * 64},
        {**record, "payload": None},
    ):
        with pytest.raises(worker.DocumentWorkerBlocked):
            worker.prepare_public_attachments([broken], TENDER)
    with pytest.raises(worker.DocumentWorkerBlocked):
        worker.verify_timeline_attachments({"FILES": {}}, manifest)
    with pytest.raises(worker.DocumentWorkerBlocked):
        worker.verify_timeline_attachments(
            {"FILES": {"1": {"id": 1, "name": manifest[0]["name"],
                             "size": len(raw) + 1}}}, manifest)
    with pytest.raises(worker.DocumentWorkerBlocked):
        worker.verify_timeline_attachments(
            {"FILES": {"1": {"id": 1, "name": "wrong.pdf",
                             "size": len(raw)}}}, manifest)


def test_zip_and_large_files_not_uploaded_and_no_untrusted_filenames():
    import hashlib
    raw = b"ZIP" * 15
    huge = b"large" * 610_000
    def row(content, ext):
        return {"url": "https://www.b2b-center.ru/files/unsafe-name" + ext,
                "kind": ext, "bytes": len(content), "payload": content,
                "sha256": hashlib.sha256(content).hexdigest()}
    pairs, manifest = worker.prepare_public_attachments(
        [row(raw, ".zip"), row(huge, ".pdf"), row(raw, ".txt")], TENDER)
    assert len(pairs) == len(manifest) == 1
    assert pairs[0][0] == "b2b-4673595-document-3.txt"
    assert "unsafe-name" not in pairs[0][0]
    with pytest.raises(worker.DocumentWorkerBlocked):
        worker.prepare_public_attachments([row(raw, ".txt")], "../4673595")


def test_reader_keeps_payload_for_valid_fenced_bitrix_attachment(monkeypatch):
    import hashlib
    monkeypatch.setattr(research, "_public_dns", lambda host: True)
    monkeypatch.setattr(docs, "_public_dns", lambda host: True)
    monkeypatch.setattr(docs, "_robots", lambda *args: None)
    payload = make_docx()
    def endpoint(request):
        if request.url.path == "/market/synthetic/4673595/":
            return httpx.Response(200,
                text='<html><body>Тендер 4673595 <a href="/files/tz.docx">'
                     'Скачать ТЗ</a></body></html>',
                headers={"content-type": "text/html"})
        if request.url.path == "/files/tz.docx":
            return httpx.Response(200, content=payload,
                                  headers={"content-type": "application/octet-stream"})
        return httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        found = docs.inspect_public_documents(
            client,
            {"status": "PUBLIC_PAGES_INSPECTED", "pages":
             [{"url": URL, "excerpt": "Объявление тендера 4673595"}]},
            TENDER)
    doc = found["documents"][0]
    assert doc["payload"] == payload
    assert doc["sha256"] == hashlib.sha256(payload).hexdigest()
