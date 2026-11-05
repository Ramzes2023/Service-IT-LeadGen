"""No-spend differential diagnosis of RouterAI 401 vs 502 from GitHub runner.

Only HTTPS GET requests; never an LLM completion, CRM request, source ingestion
or paid model call. Never print key, public IP, DNS addresses, raw headers, or
HTTP bodies. Exact corporate scope, repo/actor/owner marker only.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import httpx

REPO="Ramzes2023/Service-IT-LeadGen"
OWNER="Ramzes2023"
MARKER="SERVICEIT_ROUTERAI_TRANSPORT_READONLY_ONCE_V1"
HOST="routerai.ru"
ROOT="https://"+HOST
API=ROOT+"/api/v1"
SCENARIOS=(
    ("homepage_public", "/",False),
    ("docs_public", "/docs/guides/overview/quickstart",False),
    ("models_anonymous", "/api/v1/models",False),
    ("models_authorized", "/api/v1/models",True),
    ("credits_authorized", "/api/v1/credits",True),
)
MODES=("direct","system_env")
CODE_LIMIT=1500

class TransportBlocked(ValueError): pass

def need(ok,code):
    if not ok:raise TransportBlocked(code)

def event_valid(env):
    need(env.get("GITHUB_REPOSITORY")==REPO
         and env.get("GITHUB_REF")=="refs/heads/main"
         and env.get("GITHUB_ACTOR")==OWNER
         and env.get("GITHUB_EVENT_NAME")=="issue_comment",
         "ROUTERAI_TRANSPORT_GITHUB_CONTEXT_INVALID")
    p=Path(env.get("GITHUB_EVENT_PATH") or "/no-event")
    need(p.is_file() and 0<p.stat().st_size<=60000,
         "ROUTERAI_TRANSPORT_OWNER_EVENT_MISSING")
    try:event=json.loads(p.read_text(encoding="utf8"))
    except (ValueError,OSError):
        raise TransportBlocked("ROUTERAI_TRANSPORT_EVENT_UNVERIFIED") from None
    need(type(event) is dict
         and event.get("action")=="created"
         and (event.get("repository") or {}).get("full_name")==REPO
         and (event.get("issue") or {}).get("number")==148
         and not (event.get("issue") or {}).get("pull_request")
         and (event.get("sender") or {}).get("login")==OWNER
         and ((event.get("comment") or {}).get("user") or {}).get("login")==OWNER
         and (event.get("comment") or {}).get("body")==MARKER,
         "ROUTERAI_TRANSPORT_OWNER_MARKER_INVALID")

def dns_facts():
    try:
        records=socket.getaddrinfo(HOST,443,type=socket.SOCK_STREAM)
        family={r[0] for r in records}
        return {"resolves":True,"ipv4":socket.AF_INET in family,
                "ipv6":socket.AF_INET6 in family}
    except OSError:
        return {"resolves":False,"ipv4":False,"ipv6":False}

def request_one(client,path,key,use_auth):
    need(path in {x[1] for x in SCENARIOS},
         "ROUTERAI_TRANSPORT_PATH_FORBIDDEN")
    need(not use_auth or path in ("/api/v1/models","/api/v1/credits"),
         "ROUTERAI_TRANSPORT_PUBLIC_PATH_KEY_FORBIDDEN")
    headers={"Accept":"application/json" if path.startswith("/api/") else "text/html",
             "User-Agent":"ServiceIT-RouterAI-Diagnostic/1.0"}
    if use_auth:
        need(type(key) is str and len(key.strip())>=20,
             "ROUTERAI_TRANSPORT_API_KEY_MISSING")
        headers["Authorization"]="Bearer "+key.strip()
    try:
        resp=client.get(ROOT+path,headers=headers,timeout=12,follow_redirects=False)
    except httpx.ConnectTimeout:
        return {"result":"CONNECT_TIMEOUT"}
    except httpx.ReadTimeout:
        return {"result":"READ_TIMEOUT"}
    except httpx.ProxyError:
        return {"result":"PROXY_ERROR"}
    except httpx.ConnectError:
        return {"result":"CONNECT_OR_TLS_ERROR"}
    except httpx.HTTPError:
        return {"result":"NETWORK_ERROR"}
    # only status code and transport metadata, not raw body or headers.
    code=resp.status_code
    need(type(code) is int and 100<=code<=599,
         "ROUTERAI_TRANSPORT_HTTP_INVALID")
    supported={"cloudflare","nginx","envoy"}
    server=resp.headers.get("server","").lower().strip()
    family=next((name for name in supported if name in server),"unknown")
    return {"result":"HTTP_"+str(code),
            "http_protocol":resp.http_version if resp.http_version in
            ("HTTP/1.1","HTTP/2","HTTP/3") else "unknown",
            "server_family":family,
            "via_proxy_response_header":bool(resp.headers.get("via")),
            "redirect":300<=code<400}

def diagnose(env,*,client_factory=httpx.Client):
    need(env.get("SERVICEIT_AI_PROVIDER")=="routerai",
         "ROUTERAI_PROVIDER_NOT_CONFIGURED")
    need(env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "ROUTERAI_PRIVACY_PERMISSION_NOT_CONFIGURED")
    key=env.get("ROUTERAI_API_KEY","")
    need(type(key) is str and len(key.strip())>=20,
         "ROUTERAI_KEY_UNAVAILABLE")
    out={
        "status":"ROUTERAI_NETWORK_DIAG_COMPLETE",
        "host":HOST,
        "dns":dns_facts(),
        "github_runner_proxy_env_present":{
            "HTTP_PROXY":bool(env.get("HTTP_PROXY") or env.get("http_proxy")),
            "HTTPS_PROXY":bool(env.get("HTTPS_PROXY") or env.get("https_proxy")),
            "ALL_PROXY":bool(env.get("ALL_PROXY") or env.get("all_proxy")),
            "NO_PROXY":bool(env.get("NO_PROXY") or env.get("no_proxy")),
        },
        "endpoint_results":{},
        "key_digest_or_content_logged":False,
        "paid_model_calls":0,"customer_data_sent":False,
        "crm_writes":0,"changed_settings":False,
    }
    for mode in MODES:
        out["endpoint_results"][mode]={}
        with client_factory(trust_env=(mode=="system_env"),
                            follow_redirects=False) as client:
            for label,path,authenticated in SCENARIOS:
                out["endpoint_results"][mode][label]=request_one(
                    client,path,key,authenticated)
    return out

def main():
    env=dict(os.environ)
    try:
        event_valid(env)
        result=diagnose(env)
    except Exception:
        result={"status":"ROUTERAI_TRANSPORT_PROBE_BLOCKED",
                "crm_writes":0,"paid_model_calls":0}
    print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if result["status"]=="ROUTERAI_NETWORK_DIAG_COMPLETE" else 2

if __name__=="__main__":
    raise SystemExit(main())
