"""Public tender attachment discovery and evidence-only extraction.

No login bypass, cookies, search snippets or automatic CRM writes. Only a
previously source-ID-verified public tender page may nominate document links.
No redirect, robots bypass, private IP, executable, macro or nested archive.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import re
import zipfile
from urllib.parse import urljoin, urlsplit, unquote

import httpx
from defusedxml import ElementTree as XML

from .serviceit_public_evidence_research import (
    AGENT, VisibleText, ResearchBlocked, _host, _public_dns, _robots, _get,
)

PUBLIC_MARKETS = frozenset((
    "b2b-center.ru", "tendem.ru", "zakupki.kontur.ru", "synapsenet.ru",
))
EXTS = frozenset((".pdf", ".docx", ".xlsx", ".zip", ".txt"))
MAX_LINKS = 8
MAX_FILES = 6
MAX_FILE_BYTES = 4_000_000
MAX_ARCHIVE_BYTES = 10_000_000
MAX_TEXT = 24000
MAX_PDF_PAGES = 24
SAFE_ID = re.compile(r"\d{5,12}\Z")
SAFE_NAME = re.compile(r"[^\\/\x00-\x1f]{1,150}\Z")


class TenderDocumentBlocked(ValueError):
    pass


def fail(ok, why):
    if not ok:
        raise TenderDocumentBlocked(why)


def extension(path):
    path = unquote(path).lower()
    return next((x for x in EXTS if path.endswith(x)), "")


def public_market_url(url):
    host = _host(url, PUBLIC_MARKETS)
    fail(any(host == m or host.endswith("." + m) for m in PUBLIC_MARKETS),
         "DOCUMENT_HOST_NOT_PUBLIC_MARKET")
    return host


def candidate_links(client, findings, tender_id):
    """Follow hrefs ONLY on already verified, exact-ID tender detail pages."""
    fail(type(tender_id) is str and SAFE_ID.fullmatch(tender_id),
         "DOCUMENT_TENDER_ID_INVALID")
    fail(type(findings) is dict and
         findings.get("status") == "PUBLIC_PAGES_INSPECTED",
         "DOCUMENT_SOURCE_NOT_VERIFIED")
    candidates, seen = [], set()
    exact = re.compile(r"(?<!\d)" + tender_id + r"(?!\d)")
    for page in (findings.get("pages") or [])[:3]:
        if not isinstance(page, dict):
            continue
        url, excerpt = page.get("url"), page.get("excerpt")
        if not isinstance(excerpt, str) or not exact.search(excerpt):
            continue
        try:
            base_host = public_market_url(url)
            _robots(client, url, PUBLIC_MARKETS)
            raw = _get(client, url, PUBLIC_MARKETS)
            parser = VisibleText()
            parser.feed(raw)
            # Reject generic login/landing pages even if an indexed
            # search result or old cached excerpt claimed this is the tender.
            text = " ".join(parser.parts)
            if not exact.search(text):
                continue
        except (TenderDocumentBlocked, ResearchBlocked,
                httpx.HTTPError, ValueError, TypeError):
            continue
        for href, label in parser.links[:150]:
            if len(candidates) >= MAX_LINKS:
                break
            if not isinstance(href, str) or len(href) > 900:
                continue
            dest = urljoin(url, href)
            try:
                host = public_market_url(dest)
                fail(host == base_host, "DOCUMENT_CROSS_HOST_LINK")
                suffix = extension(urlsplit(dest).path)
                # Dynamic download URLs are allowed only with explicit
                # document/download labels AND a download-looking endpoint.
                dynamic = (bool(re.search(r"(?i)(?:download|attachment|file)", dest))
                           and any(w in label for w in (
                               "скачать", "документ", "тз", "download", "файл")))
                if not suffix and not dynamic:
                    continue
                if dest not in seen and dest != url:
                    seen.add(dest)
                    candidates.append({"url": dest, "label": label[:80]})
            except (TenderDocumentBlocked, ResearchBlocked, ValueError):
                continue
    return candidates[:MAX_LINKS]


def fetch_bytes(client, url):
    """Bound total downloaded bytes; never follow redirects or HTML logins."""
    host = public_market_url(url)
    fail(_public_dns(host), "DOCUMENT_PUBLIC_DNS_FAILED")
    _robots(client, url, PUBLIC_MARKETS)
    size = 0
    chunks = []
    try:
        with client.stream("GET", url, follow_redirects=False, timeout=14.0,
                           headers={"User-Agent": AGENT,
                                    "Accept": "application/pdf,application/zip,"
                                              "application/octet-stream,text/plain,"
                                              "application/vnd.openxmlformats-officedocument.*"}
                           ) as response:
            fail(response.status_code == 200,
                 "DOCUMENT_DOWNLOAD_NOT_PUBLIC")
            fail(not response.headers.get("content-type", "").lower().startswith(
                ("text/html", "application/json", "text/xml")),
                 "DOCUMENT_DOWNLOAD_LOGIN_OR_HTML")
            length = response.headers.get("content-length", "")
            fail(not length or (length.isdecimal()
                 and int(length) <= MAX_FILE_BYTES),
                 "DOCUMENT_DOWNLOAD_SIZE_REJECTED")
            for chunk in response.iter_bytes():
                size += len(chunk)
                fail(size <= MAX_FILE_BYTES, "DOCUMENT_DOWNLOAD_SIZE_REJECTED")
                chunks.append(chunk)
            media = response.headers.get("content-type", "").lower().split(";")[0]
    except httpx.HTTPError:
        raise TenderDocumentBlocked("DOCUMENT_HTTP_ERROR") from None
    data = b"".join(chunks)
    fail(20 <= len(data) <= MAX_FILE_BYTES, "DOCUMENT_EMPTY_OR_OVERSIZED")
    if data.startswith(b"%PDF-"):
        kind = ".pdf"
    elif data.startswith(b"PK\x03\x04"):
        # ZIP, DOCX and XLSX have identical outer signatures. Inspect
        # fixed OOXML member names, never an untrusted filename alone.
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                kind = (".docx" if "word/document.xml" in names else
                        ".xlsx" if "xl/workbook.xml" in names else ".zip")
        except (zipfile.BadZipFile, ValueError):
            raise TenderDocumentBlocked("DOCUMENT_ZIP_INVALID") from None
    elif media == "text/plain" and b"\x00" not in data[:3000]:
        kind = ".txt"
    else:
        raise TenderDocumentBlocked("DOCUMENT_UNSUPPORTED_FORMAT")
    requested = extension(urlsplit(url).path)
    fail(not requested or requested == kind or
         (requested == ".zip" and kind in (".docx", ".xlsx")),
         "DOCUMENT_CONTENT_TYPE_MISMATCH")
    return data, kind


def _safe_archive(data):
    try:
        z = zipfile.ZipFile(BytesIO(data))
        members = z.infolist()
        fail(len(members) <= 120, "DOCUMENT_ARCHIVE_TOO_MANY_ENTRIES")
        fail(sum(x.file_size for x in members) <= MAX_ARCHIVE_BYTES,
             "DOCUMENT_ARCHIVE_DECOMPRESSION_LIMIT")
        for m in members:
            fail(not m.flag_bits & 1 and m.file_size <= MAX_FILE_BYTES
                 and (m.compress_size > 0 or m.file_size == 0)
                 and (m.file_size <= 100 * m.compress_size or m.file_size == 0),
                 "DOCUMENT_ARCHIVE_UNSAFE_MEMBER")
        return z
    except (zipfile.BadZipFile, RuntimeError):
        raise TenderDocumentBlocked("DOCUMENT_ARCHIVE_INVALID") from None


def _word(data):
    with _safe_archive(data) as z:
        fail("word/document.xml" in z.namelist(), "DOCUMENT_DOCX_NOT_VALID")
        raw = z.read("word/document.xml")
        fail(len(raw) <= MAX_ARCHIVE_BYTES, "DOCUMENT_XML_TOO_LARGE")
    root = XML.fromstring(raw)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    rows = []
    for p in root.iter(ns + "p"):
        line = " ".join(n.text for n in p.iter(ns + "t") if n.text)
        if line.strip():
            rows.append(line)
        if sum(map(len, rows)) > MAX_TEXT * 2:
            break
    return "\n".join(rows)[:MAX_TEXT]


def _excel(data):
    with _safe_archive(data) as z:
        names = z.namelist()
        fail("xl/workbook.xml" in names, "DOCUMENT_XLSX_NOT_VALID")
        shared = []
        if "xl/sharedStrings.xml" in names:
            raw = z.read("xl/sharedStrings.xml")
            fail(len(raw) < MAX_ARCHIVE_BYTES, "DOCUMENT_XML_TOO_LARGE")
            root = XML.fromstring(raw)
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for si in root.iter(ns + "si"):
                shared.append(" ".join(t.text for t in si.iter(ns + "t") if t.text))
                if len(shared) > 10000:
                    break
        worksheets = [n for n in names
                      if re.fullmatch(r"xl/worksheets/sheet[0-9]{1,3}\.xml", n)]
        rows = []
        for sheet in worksheets[:3]:
            raw = z.read(sheet)
            fail(len(raw) < MAX_ARCHIVE_BYTES, "DOCUMENT_XML_TOO_LARGE")
            root = XML.fromstring(raw)
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for row in root.iter(ns + "row"):
                cells = []
                for c in list(row)[:28]:
                    if c.tag != ns + "c":
                        continue
                    v = c.find(ns + "v")
                    if c.get("t") == "inlineStr":
                        val = " ".join(t.text for t in c.iter(ns + "t") if t.text)
                    elif v is None or not v.text:
                        val = ""
                    elif c.get("t") == "s":
                        try:
                            val = shared[int(v.text)]
                        except (IndexError, ValueError):
                            val = ""
                    else:
                        val = v.text
                    if val:
                        cells.append(val[:180])
                if cells:
                    rows.append(" | ".join(cells))
                if sum(map(len, rows)) > MAX_TEXT:
                    break
    return "\n".join(rows)[:MAX_TEXT]


def _pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise TenderDocumentBlocked("DOCUMENT_PDF_LIBRARY_MISSING") from None
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        fail(not reader.is_encrypted and len(reader.pages) <= 200,
             "DOCUMENT_PDF_PROTECTED_OR_TOO_MANY_PAGES")
        lines = []
        for page in list(reader.pages)[:MAX_PDF_PAGES]:
            value = page.extract_text() or ""
            lines.append(value)
            if sum(map(len, lines)) > MAX_TEXT * 2:
                break
        return "\n".join(lines)[:MAX_TEXT]
    except TenderDocumentBlocked:
        raise
    except Exception:
        raise TenderDocumentBlocked("DOCUMENT_PDF_PARSE_FAILED") from None


def extract_text(data, kind):
    if kind == ".pdf":
        return _pdf(data)
    if kind == ".docx":
        return _word(data)
    if kind == ".xlsx":
        return _excel(data)
    if kind == ".txt":
        return data.decode("utf8", "replace")[:MAX_TEXT]
    if kind == ".zip":
        out = []
        with _safe_archive(data) as z:
            candidates = [x for x in z.infolist() if not x.is_dir()
                          and extension(x.filename) in
                          (".pdf", ".docx", ".xlsx", ".txt")]
            for info in candidates[:MAX_FILES]:
                fail(SAFE_NAME.fullmatch(info.filename.replace("/", "_")),
                     "DOCUMENT_ARCHIVE_NAME_UNSAFE")
                data_part = z.read(info)
                sub_kind = extension(info.filename)
                out.append(info.filename[:80] + "\n" +
                           extract_text(data_part, sub_kind)[:MAX_TEXT // 3])
        return "\n".join(out)[:MAX_TEXT]
    raise TenderDocumentBlocked("DOCUMENT_KIND_UNSUPPORTED")


def inspect_public_documents(client, findings, tender_id):
    """No raw document saved; returned evidence only from verified HTTP bytes."""
    candidates = candidate_links(client, findings, tender_id)
    documents, reasons = [], {}
    for row in candidates:
        if len(documents) >= MAX_FILES:
            break
        try:
            payload, kind = fetch_bytes(client, row["url"])
            content = extract_text(payload, kind)
            content = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", content)
            content = " ".join(content.split())[:MAX_TEXT]
            fail(len(content) >= 30, "DOCUMENT_SCANNED_OR_EMPTY_TEXT")
            documents.append({
                "url": row["url"], "kind": kind, "text": content,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                # Ephemeral bytes are used only to attach approved public files
                # to the same fenced CRM comment; never persisted in PG/logs.
                "payload": payload,
            })
        except (TenderDocumentBlocked, ResearchBlocked, ValueError) as exc:
            code = str(exc)
            reasons[code] = reasons.get(code, 0) + 1
    return {"documents": documents, "discovered": len(candidates),
            "rejections": reasons}
