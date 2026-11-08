"""VPS-only Brave Search + LLM Context authentication, two generic public requests.

No Bitrix REST, database queries, model calls, company names or card contents.
Never print tokens, search results, header values, response bodies or URLs.
Operator-only manual deployment gate; ~2 billable Brave Search-plan requests.
"""
from __future__ import annotations

import json
import os

import httpx

from .serviceit_vps_pg_queue import secret_file, VpsBlocked
from .serviceit_brave_connection_readonly import _brave


def probe(env: dict, client: httpx.Client) -> dict:
    key = secret_file(env, "SERVICEIT_BRAVE_SEARCH_API_KEY")
    web = _brave(client, key, context_mode=False)
    # Fail closed: do not issue another billable query after a failed web check.
    if web != "BRAVE_API_200_VALID_WEB_RESULTS":
        return {
            "status": "BRAVE_SEARCH_VPS_NOT_AUTHENTICATED",
            "web": web,
            "context": "NOT_CALLED",
            "api_requests_max": 1,
            "crm_writes": 0, "model_calls": 0, "leads_added": 0,
        }
    context = _brave(client, key, context_mode=True)
    return {
        "status": ("BRAVE_SEARCH_VPS_READY" if
                   context == "BRAVE_CONTEXT_API_200_VALID_GROUNDING"
                   else "BRAVE_SEARCH_VPS_NOT_AUTHENTICATED"),
        "web": web,
        "context": context,
        "api_requests_max": 2,
        "crm_writes": 0, "model_calls": 0, "leads_added": 0,
    }


def main() -> int:
    try:
        # Never log exceptions, which may contain secrets and search URLs.
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = probe(dict(os.environ), client)
    except (VpsBlocked, OSError, ValueError):
        result = {
            "status": "BRAVE_VPS_SECRET_MISSING_OR_UNVERIFIED",
            "web": "NOT_CALLED", "context": "NOT_CALLED",
            "api_requests_max": 0, "crm_writes": 0,
            "model_calls": 0, "leads_added": 0,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "BRAVE_SEARCH_VPS_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
