"""VPS deep public research readiness, independent of real customer cards.

Pure non-secret flags and package checks only; no requests, CRM/AI writes,
website navigation, paid search or arbitrary URL processing.
"""
from __future__ import annotations
import importlib.util
import json
import os
import re

SAFE = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}\Z")
BAD = (".test", ".invalid", ".local", ".localhost")
PUBLIC = "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS"
COMPANY = "SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS"


def valid_hosts(text: str) -> list[str]:
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError("PUBLIC_HOST_LIST_INVALID")
    hosts = [x.strip().lower() for x in text.split(",") if x.strip()]
    if len(hosts) > 80 or any(
        not SAFE.fullmatch(h) or h.endswith(BAD) for h in hosts
    ):
        raise ValueError("PUBLIC_HOST_LIST_INVALID")
    return list(dict.fromkeys(hosts))


def status(env: dict) -> dict:
    source = valid_hosts(env.get(PUBLIC, ""))
    company = valid_hosts(env.get(COMPANY, ""))
    browser_package = importlib.util.find_spec("playwright") is not None
    return {
        "status": "PUBLIC_RESEARCH_CONFIG_REVIEWED",
        "approved_source_host_count": len(source),
        "approved_independent_host_count": len(company),
        "public_source_title_model_eligible": bool(source)
            and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
            and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21",
        "deep_public_search": (
            "SOURCE_HOSTS_MISSING_NO_AI_OUTBOUND" if not source else
            "APPROVED_HOSTS_PRESENT_REQUIRES_REAL_SOURCE_TEST"
        ),
        "brave_search": (
            "BRAVE_OPTIONAL_NOT_MOUNTED" if not env.get(
                "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE") else
            "BRAVE_SECRET_MOUNT_PRESENT_AUTH_UNVERIFIED"
        ),
        "free_news_fallback": "CODE_AVAILABLE_NOT_NETWORK_TESTED",
        "js_browser": (
            "PLAYWRIGHT_PACKAGE_PRESENT_BROWSER_BINARY_UNVERIFIED"
            if browser_package else "CHROMIUM_PLAYWRIGHT_NOT_INSTALLED"
        ),
        "crm_writes": 0, "model_calls": 0, "external_requests": 0,
        "source_ingestion": False,
    }


def main():
    try:
        out = status(dict(os.environ))
        code = 0
    except ValueError:
        out = {
            "status": "PUBLIC_RESEARCH_HOST_CONFIG_INVALID",
            "crm_writes": 0, "model_calls": 0, "external_requests": 0,
        }
        code = 2
    print(json.dumps(out, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
