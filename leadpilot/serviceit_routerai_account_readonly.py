"""RouterAI account metadata probe: ZERO model tokens and ZERO CRM calls.

GET /credits and GET /models under protected API key. Does not show raw API
responses, balance amount, API key, request headers or corporate customer data.
This probe distinguishes account credits from read-only catalog access.
It does NOT verify paid /chat/completions, permission to send corporate text,
nor does it change provider selection / activation flags.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import httpx

REPOSITORY="Ramzes2023/Service-IT-LeadGen"
MARKER="SERVICEIT_ROUTERAI_ACCOUNT_READONLY_ONCE_V1"
API="https://routerai.ru/api/v1"


class AccountDiagnosticBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:raise AccountDiagnosticBlocked(code)


def owner_event(env):
    require(env.get("GITHUB_REPOSITORY")==REPOSITORY
            and env.get("GITHUB_REF")=="refs/heads/main"
            and env.get("GITHUB_EVENT_NAME")=="issue_comment"
            and env.get("GITHUB_ACTOR")=="Ramzes2023",
            "ROUTERAI_ACCOUNT_GITHUB_CONTEXT_INVALID")
    p=Path(env.get("GITHUB_EVENT_PATH") or "/not_a_file")
    require(p.is_file() and 0<p.stat().st_size<=60000,
            "ROUTERAI_ACCOUNT_EVENT_MISSING")
    try:
        event=json.loads(p.read_text("utf-8"))
    except (ValueError,OSError):
        raise AccountDiagnosticBlocked("ROUTERAI_ACCOUNT_EVENT_UNVERIFIED") from None
    require(type(event) is dict
            and event.get("action")=="created"
            and (event.get("repository") or {}).get("full_name")==REPOSITORY
            and (event.get("issue") or {}).get("number")==148
            and not (event.get("issue") or {}).get("pull_request")
            and (event.get("sender") or {}).get("login")=="Ramzes2023"
            and ((event.get("comment") or {}).get("user") or {}).get("login")=="Ramzes2023"
            and (event.get("comment") or {}).get("body")==MARKER,
            "ROUTERAI_ACCOUNT_OWNER_MARKER_UNVERIFIED")


def _numeric(v):
    if type(v) not in (str,int,float,Decimal):
        return None
    try:
        parsed=Decimal(str(v))
    except InvalidOperation:return None
    if not parsed.is_finite() or abs(parsed)>Decimal("1000000000"):
        return None
    return parsed


def bucket_credits(result):
    """Conservative qualitative interpretation; no balance amount returned."""
    if type(result) is not dict:
        return "CREDITS_RESPONSE_SCHEMA_UNKNOWN"
    nodes=[result]
    for k in ("data","credits","account"):
        if type(result.get(k)) is dict:
            nodes.append(result[k])
    for n in nodes:
        for k in ("balance","remaining","available_balance","remaining_balance","available"):
            value=_numeric(n.get(k))
            if value is not None:
                return "BALANCE_POSITIVE" if value>0 else "BALANCE_EMPTY_OR_ZERO"
        total=_numeric(n.get("total_credits"))
        spent=_numeric(n.get("total_usage"))
        if total is not None and spent is not None:
            return ("BALANCE_POSITIVE" if total-spent>0
                    else "BALANCE_EMPTY_OR_ZERO")
    return "CREDITS_RESPONSE_SCHEMA_UNKNOWN"


def api_read(client, path, key):
    require(path in ("/credits","/models"),
            "ROUTERAI_NON_READONLY_METHOD_FORBIDDEN")
    require(type(key) is str and len(key.strip())>=20,
            "ROUTERAI_ACCOUNT_API_KEY_MISSING")
    try:
        response=client.get(
            API+path,
            headers={"Authorization":"Bearer "+key.strip(),
                     "Accept":"application/json"},
            follow_redirects=False,timeout=20)
    except httpx.HTTPError:
        return "TRANSPORT_UNVERIFIED",None
    code=response.status_code
    if code!=200:
        return "HTTP_"+str(code),None
    if not 0<len(response.content)<=2000000:
        return "RESPONSE_SIZE_UNVERIFIED",None
    try:
        payload=response.json()
    except ValueError:
        return "NOT_JSON",None
    if type(payload) is not dict:
        return "UNEXPECTED_SHAPE",None
    return "HTTP_200",payload


def inspect(client,env):
    if env.get("SERVICEIT_AI_PROVIDER")!="routerai":
        return {"status":"ROUTERAI_CONFIG_NOT_SELECTED",
                "paid_inference_calls":0,"crm_writes":0}
    if env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")!="YES_1062_21":
        return {"status":"ROUTERAI_PRIVACY_NOT_APPROVED",
                "paid_inference_calls":0,"crm_writes":0}
    key=env.get("ROUTERAI_API_KEY","")
    if type(key) is not str or len(key.strip())<20:
        return {"status":"ROUTERAI_KEY_NOT_AVAILABLE",
                "paid_inference_calls":0,"crm_writes":0}
    cs,cp=api_read(client,"/credits",key)
    ms,mp=api_read(client,"/models",key)
    bucket=bucket_credits(cp) if cs=="HTTP_200" else "BALANCE_NOT_VERIFIABLE"
    listed=None
    if ms=="HTTP_200" and isinstance(mp.get("data"),list):
        listed=any(type(m) is dict
                   and m.get("id")=="deepseek/deepseek-v4.1-flash"
                   for m in mp["data"])
    return {
        "status":"ROUTERAI_ACCOUNT_READONLY_DIAGNOSTIC_DONE",
        "credit_endpoint_status":cs,
        "models_endpoint_status":ms,
        "balance_bucket":bucket,
        "model_listed_in_visible_page":listed,
        "selected_model":"deepseek/deepseek-v4.1-flash",
        "chat_generation_verified":False,
        "paid_inference_calls":0,
        "account_balance_exact_amount_disclosed":False,
        "crm_writes":0,
        "secrets_logged":False,
    }


def main():
    env=dict(os.environ)
    try:
        owner_event(env)
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            out=inspect(client,env)
    except Exception:
        out={"status":"ROUTERAI_ACCOUNT_READONLY_CHECK_BLOCKED",
             "paid_inference_calls":0,"crm_writes":0,"secrets_logged":False}
    print(json.dumps(out,sort_keys=True,ensure_ascii=False),flush=True)
    return 0 if out["status"]=="ROUTERAI_ACCOUNT_READONLY_DIAGNOSTIC_DONE" else 2


if __name__=="__main__":
    raise SystemExit(main())
