"""Service IT V2 — authenticated Google Apps Script Gmail MIME ingress.

HMAC timestamp + body. No Gmail OAuth on server. No GigaChat, GitHub issue
queue, corporate Bitrix or lead-promoting API calls. Stores only derived
procurement observations in the isolated PostgreSQL research store.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .b2b_email_opportunity_bridge import InputRejected
from .serviceit_v2_store import (
    V2StoreBlocked, ingest_message, init_schema,
)

MAX_BODY = 512_000
MAX_MIME = 256_000
EXPECTED_VERSION = "serviceit-appsscript-v1"
ALLOWED_KEYS = frozenset({
    "version", "mailbox", "gmail_message_id", "raw_b64url"
})
SIGN_RE = re.compile(r"sha256=([0-9a-f]{64})\Z")
ID_RE = re.compile(r"[a-f0-9]{8,24}\Z")

app = FastAPI(
    title="ServiceIT V2 private HMAC Gmail intake",
    docs_url=None, redoc_url=None, openapi_url=None
)


class IngressBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise IngressBlocked(code)


def _secret():
    path_raw = os.environ.get("SERVICEIT_V2_APPS_HMAC_SECRET_FILE", "")
    require(bool(path_raw), "INGRESS_NOT_CONFIGURED")
    path = Path(path_raw)
    require(path.is_file() and not path.is_symlink(), "HMAC_FILE_NOT_PRIVATE")
    st = path.stat()
    require(st.st_size <= 2048 and st.st_size >= 32, "HMAC_FILE_INVALID_SIZE")
    if os.name == "posix":
        require((st.st_mode & 0o077) == 0
                and st.st_uid == os.geteuid(), "HMAC_FILE_NOT_PRIVATE")
    try:
        key = path.read_bytes()
    except OSError:
        raise IngressBlocked("HMAC_SECRET_NOT_READABLE") from None
    require(32 <= len(key) <= 512
            and not (b"\n" in key or b"\r" in key)
            and key.strip() == key,
            "HMAC_SECRET_NOT_VALID")
    return key


def _auth(headers, raw: bytes, *, now=None):
    require(os.environ.get("SERVICEIT_V2_APPS_RECEIVER_ENABLED") == "YES",
            "INGRESS_NOT_ENABLED")
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BODY,
            "BODY_SIZE_INVALID")
    time_text = headers.get("x-serviceit-timestamp", "")
    sig_text = headers.get("x-serviceit-signature", "")
    require(re.fullmatch(r"[0-9]{10,11}", time_text) is not None,
            "SIGNATURE_TIMESTAMP_INVALID")
    now = int(time.time()) if now is None else int(now)
    require(abs(now - int(time_text)) <= 300, "SIGNATURE_TIMESTAMP_OUT_OF_WINDOW")
    match = SIGN_RE.fullmatch(sig_text)
    require(match is not None, "SIGNATURE_FORMAT_INVALID")
    expected = hmac.new(_secret(), time_text.encode("ascii") + b"." + raw,
                        hashlib.sha256).hexdigest()
    require(hmac.compare_digest(match.group(1), expected),
            "SIGNATURE_NOT_VALID")


def _payload(body):
    try:
        data = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise IngressBlocked("JSON_INVALID") from None
    require(isinstance(data, dict) and set(data) == ALLOWED_KEYS,
            "JSON_SCHEMA_INVALID")
    require(data["version"] == EXPECTED_VERSION, "VERSION_UNSUPPORTED")
    expected_email = os.environ.get("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL", "")
    require(bool(expected_email)
            and data["mailbox"] == expected_email
            and expected_email == "ramzesisayev444@gmail.com",
            "WRONG_MAILBOX")
    mid = data["gmail_message_id"]
    require(isinstance(mid, str) and ID_RE.fullmatch(mid) is not None,
            "GMAIL_ID_NOT_VALID")
    encoded = data["raw_b64url"]
    require(isinstance(encoded, str)
            and 8 < len(encoded) < 350_000
            and re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded) is not None,
            "RAW_MIME_BASE64URL_INVALID")
    try:
        mime = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError):
        raise IngressBlocked("RAW_MIME_BASE64URL_INVALID") from None
    require(0 < len(mime) <= MAX_MIME, "RAW_MIME_BYTES_INVALID")
    # Enforce canonical base64url; no hidden extra bytes or equivalent encodings.
    require(base64.urlsafe_b64encode(mime).decode("ascii").rstrip("=")
            == encoded.rstrip("="),
            "RAW_MIME_ENCODING_NOT_CANONICAL")
    return mid, mime


def process_signed_message(headers, body: bytes, *, connect):
    """Testable pure orchestration; connect only AFTER successful MAC check."""
    _auth(headers, body)
    mid, mime = _payload(body)
    try:
        with connect() as conn:
            init_schema(conn)
            result = ingest_message(conn, mid, mime)
    except (V2StoreBlocked, InputRejected):
        raise IngressBlocked("SOURCE_MIME_NOT_ACCEPTED") from None
    return {
        "status": "ALREADY_INGESTED" if result["duplicate_messages"] else "INGESTED",
        "gmail_message_id": mid,
        "new_opportunities": result["new_opportunities"],
        "revised_opportunities": result["revised_opportunities"],
        "crm_writes": 0,
        "qualified_leads": 0,
    }


def _connect():
    try:
        import psycopg
    except ImportError:
        raise IngressBlocked("DATABASE_UNAVAILABLE") from None
    dsn = os.environ.get("SERVICEIT_V2_DB_DSN", "")
    require(bool(dsn) and len(dsn) <= 2048, "DATABASE_NOT_CONFIGURED")
    try:
        return psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.Error:
        raise IngressBlocked("DATABASE_UNAVAILABLE") from None


@app.post("/api/v2/gmail/ingest")
async def ingest(request: Request):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY:
        return JSONResponse({"status": "BODY_TOO_LARGE"}, status_code=413)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
        return JSONResponse({"status": "CONTENT_TYPE_REJECTED"}, status_code=415)
    # Limit memory even when a client lies about Content-Length or streams chunks.
    pieces = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY:
            return JSONResponse({"status": "BODY_TOO_LARGE"}, status_code=413)
        pieces.append(chunk)
    body = b"".join(pieces)
    try:
        result = process_signed_message(request.headers, body, connect=_connect)
        return JSONResponse(result, status_code=200 if result["status"] == "ALREADY_INGESTED" else 202)
    except IngressBlocked as e:
        code = str(e)
        if code in {"INGRESS_NOT_CONFIGURED", "INGRESS_NOT_ENABLED",
                    "DATABASE_NOT_CONFIGURED", "DATABASE_UNAVAILABLE"}:
            return JSONResponse({"status": "INGRESS_UNAVAILABLE"}, status_code=503)
        return JSONResponse({"status": "INGRESS_REJECTED"}, status_code=403)
    except (OSError, ValueError, TypeError):
        return JSONResponse({"status": "INGRESS_UNAVAILABLE"}, status_code=503)


@app.get("/health")
async def health():
    return {"status": "SERVICEIT_V2_APPS_RECEIVER_ALIVE",
            "crm_writes": 0,
            "source_enabled": os.environ.get("SERVICEIT_V2_APPS_RECEIVER_ENABLED") == "YES"}
