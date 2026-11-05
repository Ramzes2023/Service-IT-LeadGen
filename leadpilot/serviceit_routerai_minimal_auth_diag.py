"""One micro RouterAI chat test in Ubuntu/Windows runner, auth comparison.

Owner-only, public synthetic prompt. No customer data, no CRM calls or writes,
no logging of full API key/body/HTTP response. Max eight response tokens.
Minimal API request without JSON mode/tools, to distinguish auth from
unsupported response_format and provider availability.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

import httpx

REPO="Ramzes2023/Service-IT-LeadGen"
MARKER="SERVICEIT_ROUTERAI_MINIMAL_AUTH_DIAG_ONCE_V1"
URL="https://routerai.ru/api/v1/chat/completions"
MODEL="deepseek/deepseek-v4.1-flash"


class AuthDiagBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise AuthDiagBlocked(code)


def owner_event(env):
    need(env.get("GITHUB_REPOSITORY")==REPO
         and env.get("GITHUB_REF")=="refs/heads/main"
         and env.get("GITHUB_ACTOR")=="Ramzes2023"
         and env.get("GITHUB_EVENT_NAME")=="issue_comment",
         "ROUTERAI_DIAG_OWNER_EVENT_WRONG_CONTEXT")
    p=Path(env.get("GITHUB_EVENT_PATH") or "/does-not-exist")
    need(p.is_file() and 0<p.stat().st_size<=60000,
         "ROUTERAI_DIAG_OWNER_EVENT_MISSING")
    try:event=json.loads(p.read_text(encoding="utf8"))
    except (OSError,ValueError):
        raise AuthDiagBlocked("ROUTERAI_DIAG_OWNER_EVENT_INVALID") from None
    need(type(event) is dict
         and event.get("action")=="created"
         and (event.get("repository") or {}).get("full_name")==REPO
         and (event.get("issue") or {}).get("number")==148
         and not (event.get("issue") or {}).get("pull_request")
         and (event.get("sender") or {}).get("login")=="Ramzes2023"
         and ((event.get("comment") or {}).get("user") or {}).get("login")=="Ramzes2023"
         and (event.get("comment") or {}).get("body")==MARKER,
         "ROUTERAI_DIAG_OWNER_MARKER_INVALID")


def key_shape(key):
    need(type(key) is str and len(key)>0,"ROUTERAI_DIAG_NO_KEY")
    stripped=key.strip()
    need(len(stripped)>0,"ROUTERAI_DIAG_BLANK_KEY")
    return {
        "secret_present":True,
        "bearer_prefix_in_secret":stripped.lower().startswith("bearer "),
        "quotes_wrapping_secret":stripped[:1] in ("'",'"') or
                                stripped[-1:] in ("'",'"'),
        "interior_newline":("\n" in stripped or "\r" in stripped),
        "leading_or_trailing_whitespace":key!=stripped,
        "unexpected_internal_whitespace":any(ch.isspace() for ch in stripped),
        "token_format_matches_docs_sk_prefix":stripped.startswith("sk-"),
    }


def category_from_error(payload):
    """Whitelist only indicators, no raw errors, no body disclosure."""
    if type(payload) is not dict:
        return "ERROR_BODY_UNVERIFIED"
    error=payload.get("error")
    if type(error) is not dict: return "ERROR_BODY_UNVERIFIED"
    fields=(str(error.get("type") or "")+" "+
            str(error.get("code") or "")+" "+
            str(error.get("message") or "")).lower()[:2000]
    if any(k in fields for k in ("invalid api key","invalid_api_key",
                                 "api key is invalid","key revoked","token revoked")):
        return "ERROR_INVALID_OR_REVOKED_KEY"
    if any(k in fields for k in ("balance","credit","insufficient_funds","payment required")):
        return "ERROR_BALANCE_OR_CREDIT"
    if any(k in fields for k in ("permission","forbidden","not allowed","restricted")):
        return "ERROR_ACCOUNT_OR_MODEL_PERMISSION"
    if any(k in fields for k in ("missing api key","authorization required","unauthorized")):
        return "ERROR_AUTHORIZATION_NOT_ACCEPTED"
    if any(k in fields for k in ("rate limit","quota","too many requests")):
        return "ERROR_RATE_LIMIT"
    return "ERROR_MESSAGE_NOT_CLASSIFIED"


def invoke(client,key,with_key):
    body={"model":MODEL,
          "messages":[{"role":"user","content":"Ответь только: ОК"}],
          "temperature":0,
          "max_tokens":8}
    headers={"Content-Type":"application/json","Accept":"application/json",
             "User-Agent":"ServiceIT-RouterAI-AuthDiag/1.0"}
    if with_key:
        headers["Authorization"]="Bearer "+key.strip()
    try:
        response=client.post(URL,headers=headers,json=body,
                             follow_redirects=False,timeout=32)
    except httpx.TimeoutException:
        return {"status":"NETWORK_TIMEOUT","usable_json":False}
    except httpx.ConnectError:
        return {"status":"CONNECT_TLS_ERROR","usable_json":False}
    except httpx.HTTPError:
        return {"status":"NETWORK_ERROR","usable_json":False}
    status=response.status_code
    out={"status":"HTTP_"+str(status),"usable_json":False}
    if len(response.content)>180000:
        out["error_class"]="BODY_OVERSIZED"
        return out
    try:payload=response.json()
    except ValueError:
        out["error_class"]="NOT_JSON"
        return out
    if status==200:
        choices=payload.get("choices") if type(payload) is dict else None
        out["usable_json"]=(type(choices) is list and len(choices)>0
                            and type(choices[0]) is dict
                            and type(choices[0].get("message")) is dict
                            and type(choices[0]["message"].get("content")) is str
                            and bool(choices[0]["message"]["content"].strip()))
        return out
    out["error_class"]=category_from_error(payload)
    return out


def diagnose(client,env):
    need(env.get("SERVICEIT_AI_PROVIDER")=="routerai",
         "ROUTERAI_DIAG_PROVIDER_NOT_SELECTED")
    need(env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "ROUTERAI_DIAG_PRIVACY_MISSING")
    key=env.get("ROUTERAI_API_KEY","")
    shape=key_shape(key)
    with_key=invoke(client,key,True)
    # After 401, compare the same tiny request with no key. On any success,
    # stop immediately: one paid test at most per runner.
    without_key=(
        invoke(client,"",False)
        if with_key["status"]=="HTTP_401"
        else {"status":"SKIPPED","usable_json":False})
    return {
        "status":"ROUTERAI_MINIMAL_AUTH_DIAG_COMPLETE",
        "key_format":shape,
        "chat_with_existing_key":with_key,
        "chat_without_key":without_key,
        "selected_model":MODEL,
        "paid_chat_attempts_max":1,
        "real_customer_data_sent":False,
        "crm_writes":0,
        "key_and_raw_response_logged":False,
        "production_switched":False,
    }


def main():
    env=dict(os.environ)
    try:
        owner_event(env)
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            result=diagnose(client,env)
    except Exception:
        result={"status":"ROUTERAI_MINIMAL_AUTH_DIAG_BLOCKED",
                "crm_writes":0,"production_switched":False}
    print(json.dumps(result,sort_keys=True,ensure_ascii=False),flush=True)
    return 0 if result["status"]=="ROUTERAI_MINIMAL_AUTH_DIAG_COMPLETE" else 2


if __name__=="__main__":
    raise SystemExit(main())
