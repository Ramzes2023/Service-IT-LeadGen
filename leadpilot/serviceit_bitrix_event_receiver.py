"""ASGI receiver for officially authenticated Bitrix24 new smart-process events.

No REST requests or GigaChat model inference. This server ONLY stores bounded
new-card IDs into a durable private queue. A separate verified worker is
required to read/assess/update actual CRM cards.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .serviceit_bitrix_new_card_events import (
    EventRejected, MAX_BODY_BYTES, configured_queue, intake,
)

app=FastAPI(title="ServiceIT isolated new-card event receiver",
            docs_url=None,redoc_url=None,openapi_url=None)


@app.post("/integrations/serviceit/bitrix24/new-card")
async def new_card(request: Request):
    content_length=request.headers.get("content-length","")
    if content_length.isdigit() and int(content_length)>MAX_BODY_BYTES:
        return JSONResponse({"status":"BODY_TOO_LARGE"},status_code=413)
    body=await request.body()
    if len(body)>MAX_BODY_BYTES:
        return JSONResponse({"status":"BODY_TOO_LARGE"},status_code=413)
    token=os.environ.get("SERVICEIT_BITRIX_APPLICATION_TOKEN","")
    if len(token)<24:
        return JSONResponse({"status":"EVENT_RECEIVER_NOT_CONFIGURED"},status_code=503)
    try:
        result=intake(body,request.headers.get("content-type",""),
                      token=token,db_file=configured_queue())
    except EventRejected as exc:
        # Only fixed reason codes; never raw headers/token/body/exception context.
        reason=str(exc)
        return JSONResponse(
            {"status":"EVENT_REJECTED","safe_reason":reason if reason in {
                "AUTH_MISSING","EVENT_TOKEN_INVALID","PORTAL_NOT_APPROVED",
                "ITEM_ID_OR_TYPE_INVALID","EVENT_FIELDS_INVALID",
                "EVENT_TYPE_INVALID","EVENT_BODY_TOO_LARGE_OR_EMPTY",
                "INVALID_EVENT","INVALID_FORM","REQUIRED_FORM_FIELD_MISSING",
                "EVENT_BODY_INVALID","CONTENT_TYPE_FORBIDDEN",
                "QUEUE_PATH_INVALID","QUEUE_NOT_CONFIGURED",
                "SERVER_TOKEN_NOT_CONFIGURED",
            } else "EVENT_UNTRUSTED"},
            status_code=403 if reason in {"AUTH_MISSING","EVENT_TOKEN_INVALID",
                                          "PORTAL_NOT_APPROVED"} else 400)
    except (OSError,ValueError,TypeError):
        return JSONResponse({"status":"QUEUE_UNAVAILABLE"},status_code=503)
    return JSONResponse(result,status_code=202 if result.get("queued") else 200)


@app.get("/health")
async def health():
    # No queue counts, webhook URLs or tenant metadata on unauthenticated route.
    return {"status":"RECEIVER_PROCESS_RUNNING","production_writer_enabled":False}
