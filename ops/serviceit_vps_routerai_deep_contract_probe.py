#!/usr/bin/env python3
"""One harmless SYNTHETIC production RouterAI contract probe. No CRM/PG writes.

Outputs fixed status codes only. No model text, prompts, client data, tokens,
buyer details, credentials, URLs or full HTTP response is logged. Owner gated.
"""
import json
import os
import re

import httpx

from leadpilot.ai import config_from_env
from leadpilot.serviceit_vps_pg_queue import secret_file
from leadpilot.serviceit_b2b_routerai_deep import (
    public_documents, validate_inference, _SYSTEM, QUESTION_SECTIONS,
)


def line(key, value):
    name = "MODEL_DIAG_" + key
    if not re.fullmatch(r"[A-Z_]{4,50}", name):
        return
    value = str(value)
    if not re.fullmatch(r"[A-Z0-9_]{1,55}", value):
        value = "OTHER"
    print(name + "=" + value, flush=True)


def run():
    env = dict(os.environ)
    cfg = config_from_env({
        **env, "AI_ENABLE": "1", "AI_PROVIDER": "routerai",
        "ROUTERAI_API_KEY": secret_file(env, "SERVICEIT_ROUTERAI_API_KEY"),
    })
    assert cfg.provider == "routerai"
    findings = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://www.b2b-center.ru/market/synthetic/",
                   "excerpt": ("Синтетическое учебное объявление. "
                               "Требуются специалисты по настройке серверов, "
                               "поддержке CRM и DevOps. Это учебный пример "
                               "без настоящего заказчика и потребности.")}],
    }
    docs = public_documents(findings)
    assert len(docs) == 1
    prompt = json.dumps({
        "task": "Разобрать публичную ИТ-закупку по цитируемым фактам",
        "documents": [{"source_id": d["source_id"], "text": d["text"]}
                      for d in docs],
        "requested_checks": QUESTION_SECTIONS,
    }, ensure_ascii=False)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=55) as client:
        response = client.post(
            cfg.url,
            json={"model": cfg.model,
                  "messages": [
                      {"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"},
                  "temperature": 0,
                  "max_tokens": 2900},
            headers={"Authorization": "Bearer " + cfg.key,
                     "Accept": "application/json"},
            timeout=55.0,follow_redirects=False)
    status = response.status_code
    line("HTTP", status if status in (200,400,401,402,403,404,408,422,429,500,502,503,504) else "OTHER")
    line("CONTENT_TYPE_JSON",
         "YES" if "application/json" in response.headers.get("content-type","") else "NO")
    if status != 200:
        line("COMPLETED", "NO")
        return
    try:
        envelope = response.json()
        choice = envelope["choices"][0]
        message = choice["message"]
        raw = message.get("content")
        finish = choice.get("finish_reason")
        usage = envelope.get("usage", {})
        normalized_finish = (
            finish.upper().replace("-", "_")[:55]
            if isinstance(finish, str) else "UNSET")
        line("FINISH", normalized_finish)
        line("MESSAGE_CONTENT", "TEXT" if isinstance(raw,str) and raw.strip()
             else "EMPTY_OR_NON_TEXT")
        line("REASONING_PRESENT", "YES" if bool(message.get("reasoning_content"))
             else "NO")
        line("OUTPUT_CHARS", min(len(raw),30000) if isinstance(raw,str) else 0)
        used = usage.get("completion_tokens",0)
        line("OUTPUT_TOKENS", min(100000, int(used)) if isinstance(used,(int,float)) else 0)
        try:
            parsed = json.loads(raw)
            line("JSON_PARSED", "YES")
        except (ValueError,TypeError):
            line("JSON_PARSED", "NO")
            return
        try:
            result = validate_inference(parsed, docs)
            line("INFERENCE_SCHEMA", "VALID")
            line("FACTS", min(12,len(result["facts"])))
        except ValueError as exc:
            # The decoder's fixed internal error code is safe; never expose
            # raw model answer, response body or customer text.
            code = str(exc)
            line("INFERENCE_SCHEMA", "INVALID")
            line("SCHEMA_REASON", code if re.fullmatch(r"[A-Z0-9_]{4,50}", code)
                 else "OTHER")
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        line("ENVELOPE", "INVALID")
    line("COMPLETED", "YES")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        line("EXCEPTION", "NO_CUSTOMER_DATA")
    line("DONE", "YES")
