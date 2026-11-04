"""One-shot RouterAI API preflight on a synthetic public tender (NO CRM).

Run through owner-only GitHub Actions after protected secret/variables setup.
Never expose API key, model output, headers, network URL parameters or
private corporate data in logs. A failed validation never writes any card.
"""
from __future__ import annotations

import json
import os

import httpx

from .ai import _SYSTEM, _validated_answer, config_from_env

SYNTHETIC_TEXT = "Публичная учебная закупка: требуется сопровождение 1С для магазина."
MODEL = "deepseek/deepseek-v4.1-flash"
URL = "https://routerai.ru/api/v1/chat/completions"

class PreflightBlocked(ValueError):
    pass

def inspect(client: httpx.Client, env: dict) -> dict:
    base = {"provider": "routerai", "crm_writes": 0, "real_customer_data_sent": False}
    if (env.get("GITHUB_REPOSITORY") != "Ramzes2023/Service-IT-LeadGen"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "issue_comment"):
        return {**base, "status": "OWNER_GITHUB_CONTEXT_UNVERIFIED"}
    if env.get("SERVICEIT_AI_PROVIDER") != "routerai":
        return {**base, "status": "ROUTERAI_PROVIDER_VARIABLE_NOT_ENABLED"}
    if env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") != "YES_1062_21":
        return {**base, "status": "ROUTERAI_PRIVACY_APPROVAL_NOT_SET"}
    if not env.get("ROUTERAI_API_KEY", "").strip():
        return {**base, "status": "ROUTERAI_API_KEY_SECRET_NOT_FOUND"}
    try:
        cfg = config_from_env({**env, "AI_ENABLE": "1", "AI_PROVIDER": "routerai"})
    except (RuntimeError, ValueError):
        return {**base, "status": "ROUTERAI_CONFIGURATION_REJECTED"}
    if cfg.url != URL or cfg.model != MODEL:
        return {**base, "status": "ROUTERAI_FIXED_MODEL_UNEXPECTED"}
    body = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": SYNTHETIC_TEXT}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 800,
    }
    try:
        response = client.post(cfg.url, headers={
            "Authorization": "Bearer " + cfg.key,
            "Accept": "application/json",
        }, json=body, timeout=35, follow_redirects=False)
    except httpx.HTTPError:
        return {**base, "status": "ROUTERAI_NETWORK_TLS_OR_TIMEOUT"}
    if response.status_code in (401, 403):
        return {**base, "status": "ROUTERAI_AUTHORIZATION_REJECTED"}
    if response.status_code == 402:
        return {**base, "status": "ROUTERAI_BALANCE_OR_QUOTA_REQUIRED"}
    if response.status_code == 429:
        return {**base, "status": "ROUTERAI_RATE_LIMITED"}
    if response.status_code in (400, 404, 422):
        return {**base, "status": "ROUTERAI_MODEL_OR_REQUEST_REJECTED"}
    if response.status_code >= 500:
        return {**base, "status": "ROUTERAI_PROVIDER_UNAVAILABLE"}
    if response.status_code != 200 or len(response.content) > 150_000:
        return {**base, "status": "ROUTERAI_HTTP_RESPONSE_UNVERIFIED"}
    try:
        result = _validated_answer(response.json(), SYNTHETIC_TEXT)
    except (ValueError, TypeError, KeyError):
        return {**base, "status": "ROUTERAI_RESPONSE_JSON_OR_SCHEMA_UNVERIFIED"}
    if (result.get("quality_status") != "complete"
            or result.get("origin") != "AI_hypothesis"
            or not result.get("evidence_quote")):
        return {**base, "status": "ROUTERAI_API_AUTH_OK_BUT_AI_REQUIRES_REVIEW"}
    return {**base, "status": "ROUTERAI_API_AND_AI_VALIDATION_PASS"}

def main() -> int:
    env = dict(os.environ)
    # Explicitly avoid dumping env, exceptions, request or response contents.
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = inspect(client, env)
    except Exception:
        result = {"status": "ROUTERAI_PREFLIGHT_INTERNAL_UNVERIFIED",
                  "crm_writes": 0, "real_customer_data_sent": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["status"] == "ROUTERAI_API_AND_AI_VALIDATION_PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
