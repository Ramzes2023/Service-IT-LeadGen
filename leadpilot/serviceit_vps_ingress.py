"""Dedicated HTTPS reverse-proxied Bitrix event ingress to Postgres.

Only an authenticated official Bitrix24 onCrmDynamicItemAdd event can enqueue
an exact smart process ID. NEVER invokes an AI provider, writes CRM, fetches
other customers, creates leads or stores incoming payloads.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .serviceit_bitrix_new_card_events import (
    EventRejected, MAX_BODY_BYTES, parse_body, parse_event,
)
from .serviceit_vps_pg_queue import (
    VpsBlocked, db, insert_new, secret_file, CARD_MIN,
)

app = FastAPI(title="ServiceIT VPS Bitrix Event Ingress", docs_url=None,
              redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    return {"status": "SERVICEIT_GATEWAY_RUNNING",
            "crm_writer_enabled": False, "crm_writes": 0}


@app.post("/integrations/serviceit/bitrix24/new-card")
async def new_card(request: Request):
    if os.getenv("SERVICEIT_VPS_INTAKE_ENABLED") != "YES":
        return JSONResponse({"status": "INGRESS_DISABLED"}, status_code=503)
    raw_length = request.headers.get("content-length", "")
    if raw_length.isdigit() and int(raw_length) > MAX_BODY_BYTES:
        return JSONResponse({"status": "REQUEST_TOO_LARGE"}, status_code=413)
    body = await request.body()
    if not body or len(body) > MAX_BODY_BYTES:
        return JSONResponse({"status": "REQUEST_TOO_LARGE"}, status_code=413)
    try:
        token = secret_file(os.environ, "SERVICEIT_BITRIX_APPLICATION_TOKEN")
        candidate = parse_event(
            parse_body(body, request.headers.get("content-type", "")),
            expected_app_token=token)
        if not candidate["queued"]:
            return {"status": "IGNORED_NON_NEW_ITEM", "queued": False}
        card_id = candidate["item_id"]
        if card_id < CARD_MIN:
            raise VpsBlocked("HISTORICAL_ID_FORBIDDEN")
        with db(os.environ) as connection:
            inserted = insert_new(connection, card_id)
    except EventRejected:
        return JSONResponse({"status": "AUTH_OR_EVENT_REJECTED"}, status_code=403)
    except (VpsBlocked, OSError, ValueError):
        return JSONResponse({"status": "SERVICE_CONFIGURATION_UNAVAILABLE"},
                            status_code=503)
    except Exception:
        # No error content: SQL/transport failures may expose secrets/queries.
        return JSONResponse({"status": "QUEUE_UNAVAILABLE"}, status_code=503)
    return JSONResponse({
        "status": "NEW_CARD_QUEUED" if inserted else "ALREADY_QUEUED",
        "queued": bool(inserted), "crm_writes": 0,
    }, status_code=202 if inserted else 200)
