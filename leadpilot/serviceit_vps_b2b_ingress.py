"""Public B2B-Center intake: HMAC only -> PostgreSQL. No CRM credentials.

Separate 127.0.0.1 listener behind the already-existing Caddy HTTPS site.
Never accepts unauthenticated records and never triggers Bitrix or AI here.
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .serviceit_vps_b2b_bridge import (
    MAX_BODY, PATH, enabled, signed_notice, migrate, accept,
    B2BBlocked,
)
from .serviceit_vps_pg_queue import db, secret_file, VpsBlocked
from .serviceit_b2b_appsscript_ingest import IngestBlocked
from .b2b_github_to_bitrix import BridgeBlocked

app = FastAPI(title="ServiceIT B2B VPS signed ingress",
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    return {
        "status": "B2B_INGRESS_ALIVE",
        "source_enabled": os.getenv("SERVICEIT_VPS_B2B_RECEIVER_ENABLED") == "YES",
        "crm_writes": 0,
    }


@app.post(PATH)
async def b2b_notice(request: Request):
    if os.getenv("SERVICEIT_VPS_B2B_RECEIVER_ENABLED") != "YES":
        return JSONResponse({"status": "SOURCE_RECEIVER_DISABLED"}, status_code=503)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return JSONResponse({"status": "JSON_ONLY"}, status_code=415)
    length = request.headers.get("content-length", "")
    if not length.isdecimal() or int(length) < 1 or int(length) > MAX_BODY:
        return JSONResponse({"status": "BOUNDED_BODY_REQUIRED"}, status_code=413)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            return JSONResponse({"status": "BOUNDED_BODY_REQUIRED"}, status_code=413)
    if len(raw) != int(length):
        return JSONResponse({"status": "BODY_SIZE_MISMATCH"}, status_code=400)
    try:
        payload = json.loads(bytes(raw))
        env = dict(os.environ)
        enabled(env)
        signing_key = secret_file(env, "SERVICEIT_VPS_B2B_HMAC_KEY")
        notice, groups, dispatch_id = signed_notice(payload, signing_key)
        with db(env) as connection:
            migrate(connection)
            inserted = accept(connection, notice, groups)
        return JSONResponse({
            "status": "B2B_NOTICE_PERSISTED" if inserted else "B2B_NOTICE_DUPLICATE",
            "crm_writes": 0,
        }, status_code=202 if inserted else 200)
    except (B2BBlocked, IngestBlocked, BridgeBlocked, ValueError, TypeError,
            KeyError, UnicodeError, json.JSONDecodeError):
        return JSONResponse({"status": "SOURCE_AUTH_OR_SCHEMA_REJECTED"},
                            status_code=403)
    except (OSError, VpsBlocked):
        return JSONResponse({"status": "VPS_SOURCE_CONFIGURATION_BLOCKED"},
                            status_code=503)
    except Exception:
        # No raw email subject, sender, buyer, URL, signature or traceback.
        return JSONResponse({"status": "VPS_SOURCE_QUEUE_UNAVAILABLE"},
                            status_code=503)
