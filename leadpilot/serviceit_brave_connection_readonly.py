"""Production-secret Brave / Bitrix / AI readiness attestation; STRICTLY READ-ONLY.

Designed to execute only after the owner connects the Brave API key in
GitHub Actions Secrets. One public generic Brave search call (~1 paid API
request, subject to plan/credits), one exact CRM metadata call; zero CRM
writes, zero CRM item reads and zero AI inference calls. Returns status only,
never secret/key, returned search results, private CRM data or webhook URL.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .serviceit_b2b_hold_ai_fields import AI_FIELDS
from .serviceit_enrichment_worker import Rest
from .serviceit_independent_company_research import SEARCH_URL
from .serviceit_brave_llm_context import CONTEXT_URL

STATUS_OK = "ALL_COMPONENTS_READY_READONLY"
KNOWN_GOOD = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}\Z")


def _brave(client: httpx.Client, key: str, *, context_mode=False):
    if not isinstance(key, str) or not 12 <= len(key) <= 200 or not key.isascii():
        return "BRAVE_KEY_MISSING_OR_INVALID"
    try:
        # A generic published informational query. NO CRM company, customer,
        # title, email, private field or Bitrix URL goes to the search vendor.
        response = client.get(
            CONTEXT_URL if context_mode else SEARCH_URL,
            params=({"q": "Python official documentation",
                     "country": "RU", "search_lang": "ru",
                     "count": 2, "maximum_number_of_tokens": 1024,
                     "maximum_number_of_urls": 2,
                     "context_threshold_mode": "balanced"}
                    if context_mode else
                    {"q": "Python official documentation",
                     "count": 2, "country": "RU",
                     "search_lang": "ru", "safesearch": "strict"}),
            headers={"X-Subscription-Token": key,
                     "Accept": "application/json",
                     # Brave validates this exact header on some deployments.
                     "Cache-Control": "no-cache"},
            follow_redirects=False, timeout=15.0)
        if response.status_code == 401:
            return "BRAVE_INVALID_KEY_401"
        if response.status_code == 403:
            return "BRAVE_FORBIDDEN_403"
        if response.status_code == 429:
            return "BRAVE_RATE_LIMIT_429"
        if response.status_code == 422:
            # Emit only audited validation field names, never provider error
            # messages, request values, search results or response bodies.
            if len(response.content) <= 8192:
                try:
                    payload = response.json()
                    error = payload.get("error") if type(payload) is dict else None
                    if type(error) is dict and error.get("code") == "VALIDATION":
                        meta = error.get("meta")
                        errors = meta.get("errors") if type(meta) is dict else None
                        if type(errors) is list and len(errors) <= 10:
                            for item in errors:
                                loc = item.get("loc") if type(item) is dict else None
                                if loc == ["header", "cache-control"]:
                                    return "BRAVE_VALIDATION_CACHE_CONTROL_422"
                                if loc == ["query", "country"]:
                                    return "BRAVE_VALIDATION_COUNTRY_422"
                                if loc == ["query", "search_lang"]:
                                    return "BRAVE_VALIDATION_SEARCH_LANG_422"
                except (TypeError, ValueError):
                    pass
            return "BRAVE_PLAN_OR_QUERY_REJECTED_422"
        if response.status_code == 402:
            return "BRAVE_PLAN_OR_QUERY_REJECTED_402"
        if response.status_code != 200:
            return "BRAVE_HTTP_" + str(response.status_code)
        if not 0 < len(response.content) <= 200000:
            return "BRAVE_RESPONSE_SIZE_INVALID"
        if "application/json" not in response.headers.get("content-type", ""):
            return "BRAVE_NOT_JSON"
        payload = response.json()
        if context_mode:
            if not (isinstance(payload, dict)
                    and isinstance(payload.get("grounding"), dict)
                    and isinstance(payload["grounding"].get("generic"), list)
                    and isinstance(payload.get("sources"), dict)):
                return "BRAVE_CONTEXT_SCHEMA_UNVERIFIED"
            return "BRAVE_CONTEXT_API_200_VALID_GROUNDING"
        if not (isinstance(payload, dict) and isinstance(payload.get("web"), dict)
                and isinstance(payload["web"].get("results"), list)):
            return "BRAVE_RESULT_SCHEMA_UNVERIFIED"
        # We never log returned content or private data.
        return "BRAVE_API_200_VALID_WEB_RESULTS"
    except (httpx.HTTPError, TypeError, ValueError):
        return "BRAVE_TRANSPORT_OR_RESPONSE_UNVERIFIED"


def _crm_metadata(client: httpx.Client, webhook: str):
    if not isinstance(webhook, str) or not webhook:
        return "BITRIX_WEBHOOK_MISSING"
    try:
        rest = Rest(webhook, client)
        fields = rest("crm.item.fields", {
            "entityTypeId": 1062,
            "useOriginalUfNames": "Y",
        }).get("fields")
        if not isinstance(fields, dict):
            return "BITRIX_FIELDS_SCHEMA_UNVERIFIED"
        if not AI_FIELDS.issubset(set(fields)):
            return "BITRIX_NINE_AI_FIELDS_NOT_ALL_VISIBLE"
        if not all(isinstance(fields[name], dict)
                   and fields[name].get("isReadOnly") is False
                   for name in AI_FIELDS):
            return "BITRIX_NINE_AI_FIELDS_NOT_WRITABLE"
        return "BITRIX_NINE_AI_FIELDS_VISIBLE_READONLY_VERIFIED"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "BITRIX_METADATA_READONLY_FAILED"


def readiness(env: dict, client: httpx.Client):
    """The return object is safe for GitHub Actions logs."""
    required = {
        "source_domains": [
            d.strip().lower()
            for d in env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "").split(",")
            if d.strip()
        ],
        "company_domains": [
            d.strip().lower()
            for d in env.get("SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS", "").split(",")
            if d.strip()
        ],
    }
    all_domains = required["source_domains"] + required["company_domains"]
    hosts_valid = bool(required["source_domains"]) and len(all_domains) <= 80 and all(
        KNOWN_GOOD.fullmatch(h) and not h.endswith(
            (".test", ".invalid", ".local", ".localhost"))
        for h in all_domains)
    provider = "routerai" if (
        env.get("SERVICEIT_AI_PROVIDER") == "routerai"
        and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21"
        and env.get("ROUTERAI_API_KEY")
    ) else "gigachat"
    model_ready = bool(
        env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
        and env.get("SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED") == "YES"
        and (env.get("ROUTERAI_API_KEY") if provider == "routerai"
             else env.get("GIGACHAT_CREDENTIALS"))
    )
    context_mode = env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES"
    r = {
        "check_mode": "REAL_EXTERNAL_API_AND_BITRIX_METADATA_ONLY",
        "brave_search_mode": ("LLM_CONTEXT_ONE_REQUEST" if context_mode else
                              "WEB_SEARCH_ONE_REQUEST"),
        "brave": _brave(client, env.get("SERVICEIT_BRAVE_SEARCH_API_KEY", ""),
                        context_mode=context_mode),
        "bitrix": _crm_metadata(client, env.get("SERVICEIT_BITRIX_WEBHOOK", "")),
        "new_card_exact_worker": (
            "ENABLED" if env.get("SERVICEIT_BITRIX_EVENT_AI_ENABLED")
            == "YES_1062_21_CLOUD" else "DISABLED"),
        "batch_new_card_worker": (
            "ENABLED" if env.get("SERVICEIT_BITRIX_POLL_ENABLED")
            == "YES_1062_21_READONLY" else "DISABLED"),
        "public_deep_research": (
            "ENABLED_IN_WORKFLOWS" if env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED")
            == "YES" else "DISABLED"),
        "public_source_allowlist": (
            "PRESENT" if hosts_valid else "MISSING_OR_INVALID"),
        "main_ai": (
            "CONFIGURED_" + provider.upper() + "_NOT_CALLED"
            if model_ready else "NOT_READY_OR_PRIVACY_NOT_APPROVED"),
        "crm_writes": 0,
        "new_leads_created": 0,
        "ai_inferences": 0,
        "brave_search_requests": (
            1 if isinstance(env.get("SERVICEIT_BRAVE_SEARCH_API_KEY"), str)
            and len(env.get("SERVICEIT_BRAVE_SEARCH_API_KEY", "")) >= 12 else 0),
    }
    r["readiness"] = (
        STATUS_OK if (
            r["brave"] == ("BRAVE_CONTEXT_API_200_VALID_GROUNDING"
                               if context_mode else
                               "BRAVE_API_200_VALID_WEB_RESULTS")
            and r["bitrix"] == "BITRIX_NINE_AI_FIELDS_VISIBLE_READONLY_VERIFIED"
            and r["new_card_exact_worker"] == "ENABLED"
            and r["batch_new_card_worker"] == "ENABLED"
            and r["public_deep_research"] == "ENABLED_IN_WORKFLOWS"
            and r["public_source_allowlist"] == "PRESENT"
            and r["main_ai"].startswith("CONFIGURED_")
        ) else "BLOCKERS_PRESENT_REVIEW_SAFE_STATUS"
    )
    return r


def main():
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = readiness(dict(os.environ), client)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["readiness"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
