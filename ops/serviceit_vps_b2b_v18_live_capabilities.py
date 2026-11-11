#!/usr/bin/env python3
"""V18 no-write live VPS diagnostic: Brave Context + Chromium + Kontur gates.

One Brave Context request only. No CRM, PostgreSQL, customer IDs, model calls,
login, cookie, CAPTCHA, browser bypass, API tokens or source body in output.
Do not assert that index text is independently verified procurement evidence.
"""
from __future__ import annotations

import os
import re
from urllib.robotparser import RobotFileParser

import httpx

TENDER="4675475"
SUBJECT="Приобретение серверного оборудования для нужд МКБ"
KONTUR="https://zakupki.kontur.ru/4675475_40"
CONTEXT="https://api.search.brave.com/res/v1/llm/context"
BROWSER="http://127.0.0.1:18785/health"


def say(key, value):
    assert re.fullmatch(r"[A-Z0-9_]{3,48}",key)
    assert value in ("YES","NO","HTTP_200","HTTP_403","HTTP_429",
                     "HTTP_OTHER","NETWORK_ERROR","NOT_RUN",
                     "ALLOWED","DENIED","UNKNOWN")
    print("V18_PROBE_"+key+"="+value,flush=True)


def run():
    from leadpilot.serviceit_vps_worker import exact_environment,live_permitted
    from leadpilot.serviceit_public_evidence_research import AGENT
    env=dict(os.environ)
    say("FIVE_MINUTE_LIVE_GATES","YES" if live_permitted(env) else "NO")
    # Never print any environment values, keys, or HTTP response bodies.
    try:
        exact=exact_environment(env)
    except Exception:
        say("ROUTERAI_BRAVE_ENV_READY","NO")
        return 2
    key=exact.get("SERVICEIT_BRAVE_SEARCH_API_KEY","")
    say("BRAVE_KEY_READY","YES" if isinstance(key,str) and len(key)>=12 else "NO")
    allowed=env.get("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS","")
    browser_hosts=set(x.strip() for x in allowed.split(","))
    say("BROWSER_KONTUR_ALLOWLIST","YES" if "zakupki.kontur.ru" in browser_hosts else "NO")
    say("BROWSER_B2B_ALLOWLIST","YES" if "b2b-center.ru" in browser_hosts else "NO")
    say("V18_FEATURE_PROPOSED","YES")
    with httpx.Client(trust_env=False,follow_redirects=False,
                      timeout=httpx.Timeout(12.0)) as client:
        try:
            res=client.get(BROWSER)
            ready=(res.status_code==200 and
                   res.json().get("status")=="BROWSER_BRIDGE_READY_NOT_EGRESS_CERTIFIED"
                   and res.json().get("crm_writes")==0)
            say("CHROMIUM_BRIDGE_HEALTH","YES" if ready else "NO")
        except (httpx.HTTPError,ValueError,TypeError):
            say("CHROMIUM_BRIDGE_HEALTH","NO")
        try:
            robots=client.get(
                "https://zakupki.kontur.ru/robots.txt",
                headers={"User-Agent":AGENT,"Accept":"text/plain"},
                timeout=9.0,follow_redirects=False)
            code=("HTTP_200" if robots.status_code==200 else
                  "HTTP_403" if robots.status_code==403 else
                  "HTTP_429" if robots.status_code==429 else "HTTP_OTHER")
            say("KONTUR_ROBOTS_HTTP",code)
            if robots.status_code==200 and len(robots.content)<=160000:
                parser=RobotFileParser()
                parser.parse(robots.text[:20000].splitlines())
                say("KONTUR_ROBOTS_TENDER",
                    "ALLOWED" if parser.can_fetch(AGENT,KONTUR) else "DENIED")
            else:
                say("KONTUR_ROBOTS_TENDER","UNKNOWN")
        except httpx.HTTPError:
            say("KONTUR_ROBOTS_HTTP","NETWORK_ERROR")
            say("KONTUR_ROBOTS_TENDER","UNKNOWN")
        if not isinstance(key,str) or len(key)<12:
            say("BRAVE_CONTEXT_STATUS","NOT_RUN")
            return 0
        try:
            response=client.get(
                CONTEXT,
                params={"q":'"'+TENDER+'" B2B-Center '+SUBJECT,
                        "country":"RU","search_lang":"ru",
                        "count":10,"maximum_number_of_urls":8,
                        "maximum_number_of_tokens":4096,
                        "context_threshold_mode":"balanced"},
                headers={"X-Subscription-Token":key,"Accept":"application/json",
                         "Cache-Control":"no-cache"},
                follow_redirects=False,timeout=20.0)
            code=("HTTP_200" if response.status_code==200 else
                  "HTTP_403" if response.status_code in (401,403) else
                  "HTTP_429" if response.status_code==429 else "HTTP_OTHER")
            say("BRAVE_CONTEXT_STATUS",code)
            if response.status_code==200 and len(response.content)<=256000:
                data=response.json()
                rows=data.get("grounding",{}).get("generic",[])
                valid=isinstance(rows,list)
                hits=[x for x in rows[:20] if isinstance(x,dict)
                      and isinstance(x.get("url"),str)
                      and TENDER in (x.get("title","")+" "+
                                     " ".join(z for z in x.get("snippets",[])[:6]
                                              if isinstance(z,str)))]
                say("BRAVE_CONTEXT_EXACT_ID","YES" if valid and hits else "NO")
                say("BRAVE_CONTEXT_KONTUR_URL","YES" if any(
                    x.get("url","").startswith("https://zakupki.kontur.ru/")
                    for x in hits) else "NO")
                snippets=" ".join(" ".join(
                    t for t in x.get("snippets",[])[:6] if isinstance(t,str))
                    for x in hits)[:40000].lower()
                say("BRAVE_CONTEXT_BUYER_LABEL","YES" if
                    ("заказчик" in snippets or "московский кредитный банк" in snippets)
                    else "NO")
                say("BRAVE_CONTEXT_INN_LABEL","YES" if "7734202860" in snippets else "NO")
                say("BRAVE_CONTEXT_DEADLINE_LABEL","YES" if
                    "16.11.2026" in snippets else "NO")
            else:
                say("BRAVE_CONTEXT_EXACT_ID","NO")
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):
            say("BRAVE_CONTEXT_STATUS","NETWORK_ERROR")
            say("BRAVE_CONTEXT_EXACT_ID","NO")
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(run())
    except Exception:
        say("READONLY_AUDIT_FAILED","YES")
        raise SystemExit(2)
