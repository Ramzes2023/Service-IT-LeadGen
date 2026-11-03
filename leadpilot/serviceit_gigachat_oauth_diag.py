"""Owner-triggered no-model, no-CRM GigaChat OAuth/TLS diagnostic.

Only fixed statuses leave this process. No token, response, credentials,
certificate paths or remote exception text is ever logged. TLS verification
is ALWAYS enabled; do not use verify=False.
"""
from __future__ import annotations
import json
import os
import ssl
import uuid

import httpx

from .ai import config_from_env

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"


def category(exc: Exception) -> str:
    """Use exception types and fixed local codes; never echo the exception."""
    cur = exc
    for _ in range(8):
        if isinstance(cur, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return "TLS_CERTIFICATE_VERIFY_FAILED"
        nxt = cur.__cause__ or cur.__context__
        if nxt is None or nxt is cur:
            break
        cur = nxt
    # For httpcore-wrapped Python SSL exceptions which expose safe class
    # information only through the text; no raw message is displayed.
    if isinstance(exc, httpx.ConnectError) and "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return "TLS_CERTIFICATE_VERIFY_FAILED"
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError,
                        httpx.NetworkError)):
        return "NETWORK_CONNECT_FAILED"
    return "TRANSPORT_UNVERIFIED"


def probe(client: httpx.Client, env: dict) -> dict:
    if env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") != "YES":
        return {"status": "CONSENT_NOT_VERIFIED", "oauth": "NOT_ATTEMPTED"}
    if env.get("GIGACHAT_SCOPE") not in {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
        return {"status": "SCOPE_INVALID", "oauth": "NOT_ATTEMPTED"}
    try:
        cfg = config_from_env(env)
    except (ValueError, TypeError, RuntimeError):
        return {"status": "CONFIG_UNVERIFIED", "oauth": "NOT_ATTEMPTED"}
    if cfg.provider != "gigachat":
        return {"status": "PROVIDER_UNVERIFIED", "oauth": "NOT_ATTEMPTED"}
    try:
        resp = client.post(
            OAUTH_URL,
            headers={"Authorization": "Basic " + cfg.credentials.removeprefix("Basic "),
                     "RqUID": str(uuid.uuid4()), "Accept": "application/json"},
            data={"scope": cfg.scope},
            timeout=20, follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return {"status": "OAUTH_UNVERIFIED", "oauth": category(exc)}
    if resp.status_code == 200:
        try:
            token = resp.json().get("access_token")
        except (ValueError, TypeError, AttributeError):
            token = None
        return {"status": "OAUTH_TOKEN_PRESENT" if isinstance(token, str) and token
                else "OAUTH_TOKEN_MISSING", "oauth": "HTTP_200"}
    if resp.status_code in (400, 401, 403, 429, 500, 502, 503):
        return {"status": "OAUTH_HTTP_REJECTED", "oauth": "HTTP_" + str(resp.status_code)}
    return {"status": "OAUTH_HTTP_UNEXPECTED", "oauth": "OTHER_HTTP_STATUS"}


def main() -> int:
    env = dict(os.environ)
    # This is the only available audited entry point, not a reusable bearer.
    try:
        with httpx.Client(verify=env.get("GIGACHAT_CA_BUNDLE_FILE") or True,
                          trust_env=False, follow_redirects=False) as client:
            result = probe(client, env)
    except Exception:
        result = {"status": "OAUTH_UNVERIFIED", "oauth": "TRANSPORT_UNVERIFIED"}
    result.update({"crm_writes": 0, "model_completions": 0,
                   "ledger_reservations": 0})
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "OAUTH_TOKEN_PRESENT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
