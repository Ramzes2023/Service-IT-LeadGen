"""No-spend attestation of ACTUAL cloud Bitrix AI routing settings.

This checks the precise AI_PROVIDER variable resolved by Actions, validated
RouterAI key shape, backup GigaChat credential and pinned TLS bundle path.
It never calls either AI API or CRM, never prints any keys or customer data.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from .ai import config_from_env,routerai_api_key_format_valid

def snapshot(env):
    if (env.get("GITHUB_REPOSITORY")!="Ramzes2023/Service-IT-LeadGen"
        or env.get("GITHUB_REF")!="refs/heads/main"
        or env.get("GITHUB_EVENT_NAME")!="issue_comment"):
        return {"status":"ROUTERAI_PRIMARY_SCOPE_BLOCKED","crm_writes":0,
                "model_calls":0}
    selected=env.get("AI_PROVIDER")
    has_key=routerai_api_key_format_valid(env.get("ROUTERAI_API_KEY",""))
    fallback=(env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT")=="YES")
    backup=bool(env.get("GIGACHAT_CREDENTIALS"))
    f=env.get("GIGACHAT_CA_BUNDLE_FILE","")
    ca=bool(f and Path(f).is_file() and Path(f).stat().st_size>200_000)
    routerai_ok=False
    if selected=="routerai" and has_key:
        try:
            cfg=config_from_env({**env,"AI_ENABLE":"1"})
            routerai_ok=(cfg.provider=="routerai" and
                         cfg.model=="deepseek/deepseek-v4.1-flash")
        except (RuntimeError,ValueError):
            routerai_ok=False
    ready=(
        selected=="routerai" and routerai_ok and
        env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED")=="YES" and
        env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21" and
        fallback and backup and ca
    )
    return {
        "status":"ROUTERAI_PRIMARY_WITH_GIGACHAT_STANDBY_READY"
                 if ready else "ROUTERAI_PRIMARY_NOT_FULLY_READY",
        "primary_routerai_selected":selected=="routerai",
        "primary_model_verified":routerai_ok,
        "routerai_api_key_shape_valid":has_key,
        "fallback_gigachat_enabled":fallback,
        "fallback_credential_present":backup,
        "fallback_pinned_ca_present":ca,
        "corporate_model_privacy_allowed":
            env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED")=="YES",
        "routerai_privacy_approved":
            env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
        "crm_writes":0,"model_calls":0,"secret_values_logged":False,
    }

def main():
    try:out=snapshot(dict(os.environ))
    except Exception:out={"status":"ROUTERAI_PRIMARY_AUDIT_UNVERIFIED",
                          "crm_writes":0,"model_calls":0}
    print(json.dumps(out,ensure_ascii=False,sort_keys=True))
    return 0 if out["status"]=="ROUTERAI_PRIMARY_WITH_GIGACHAT_STANDBY_READY" else 2

if __name__=="__main__":
    raise SystemExit(main())
