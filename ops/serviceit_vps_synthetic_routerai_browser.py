#!/usr/bin/env python3
"""Owner-triggered synthetic RouterAI + public browser acceptance (NO Bitrix).

One harmless public fictional tender text is sent to the already-approved
RouterAI model; never sends CRM fields, INNs, contacts, webhook or passwords.
Optionally reads up to three pre-allowlisted PUBLIC homepages via VPS browser.
Outputs fixed allowlisted status codes ONLY.
"""
from __future__ import annotations

import os
import re

import httpx

from leadpilot.serviceit_vps_pg_queue import secret_file
from leadpilot.serviceit_routerai_synthetic_preflight import inspect
from leadpilot.serviceit_vps_browser_client import render_approved_url


def status(name, value):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,65}", name):
        return
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,110}", value):
        value = "UNVERIFIED"
    print(name + "=" + value, flush=True)


def main():
    env = dict(os.environ)
    status("ACCEPTANCE_START", "YES")
    status("ACCEPTANCE_CRM_WRITES", "ZERO")
    status("ACCEPTANCE_CUSTOMER_DATA_SENT", "NO")
    try:
        key = secret_file(env, "SERVICEIT_ROUTERAI_API_KEY")
        test = {
            **env,
            "AI_PROVIDER": "routerai",
            "AI_ENABLE": "1",
            "ROUTERAI_API_KEY": key,
            "SERVICEIT_AI_PROVIDER": "routerai",
            "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "issue_comment",
        }
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = inspect(client, test)
        model_status = response.get("status", "ROUTERAI_SYNTHETIC_UNVERIFIED")
    except Exception:
        model_status = "ROUTERAI_SYNTHETIC_UNVERIFIED"
    status("ACCEPTANCE_ROUTERAI", str(model_status))

    try:
        hosts = ",".join((
            env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", ""),
            env.get("SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS", ""),
        ))
        allowed = sorted(set(x.strip().lower() for x in hosts.split(",") if x.strip()))
        good = (0 < len(allowed) <= 80 and all(
            re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}", h)
            for h in allowed))
        status("ACCEPTANCE_PUBLIC_HOSTS_READY", "YES" if good else "NO")
        if (not good or env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") != "YES"
                or env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") != "YES"):
            status("ACCEPTANCE_BROWSER", "NOT_CONFIGURED")
        else:
            succeeded = False
            for hostname in allowed[:3]:
                with httpx.Client(trust_env=False, follow_redirects=False) as client:
                    result = render_approved_url(
                        "https://" + hostname + "/", frozenset(allowed),
                        client=client)
                if isinstance(result, dict) and result.get("browser_unverified") is True:
                    succeeded = True
                    break
            status("ACCEPTANCE_BROWSER", "PUBLIC_RENDER_PASS"
                   if succeeded else "NO_PUBLIC_RENDER_VERIFIED")
    except Exception:
        status("ACCEPTANCE_PUBLIC_HOSTS_READY", "UNVERIFIED")
        status("ACCEPTANCE_BROWSER", "UNVERIFIED")
    status("ACCEPTANCE_SOURCES_CONNECTED", "NO")
    status("ACCEPTANCE_E2E_REAL_CARD", "NOT_TESTED")
    status("ACCEPTANCE_DONE", "YES")


if __name__ == "__main__":
    main()
