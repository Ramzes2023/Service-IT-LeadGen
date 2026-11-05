"""Read-only corporate Bitrix proof of nine AI fields and task linkage.

No AI calls, CRM writes, tasks, customer identifiers or credentials in logs.
Presence and writable metadata do NOT prove a production item update succeeds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_SUFFIXES, PREFIX

REPO = "Ramzes2023/Service-IT-LeadGen"
MARKER = "SERVICEIT_NINE_AI_TASK_METADATA_READONLY_V1"


class MetadataBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise MetadataBlocked(code)


def gate(env):
    need(env.get("GITHUB_REPOSITORY") == REPO
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "issue_comment",
         "AI_TASK_AUDIT_CONTEXT_INVALID")
    path = Path(env.get("GITHUB_EVENT_PATH") or "/nonexistent")
    need(path.is_file() and 0 < path.stat().st_size <= 256000,
         "AI_TASK_AUDIT_OWNER_EVENT_NOT_FOUND")
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise MetadataBlocked("AI_TASK_AUDIT_EVENT_JSON_INVALID") from None
    need(data.get("action") == "created"
         and (data.get("repository") or {}).get("full_name") == REPO
         and (data.get("issue") or {}).get("number") == 148
         and not (data.get("issue") or {}).get("pull_request")
         and (data.get("sender") or {}).get("login") == "Ramzes2023"
         and (data.get("comment") or {}).get("body") == MARKER,
         "AI_TASK_AUDIT_NOT_OWNER_APPROVED")


def rest(client, base, method, args):
    allowed = {
        "crm.item.fields": {"entityTypeId": 1062,
                            "useOriginalUfNames": "Y"},
        "crm.type.getByEntityTypeId": {"entityTypeId": 1062},
        "tasks.task.getFields": {},
    }
    need(method in allowed and args == allowed[method],
         "AI_TASK_AUDIT_METHOD_OUTSIDE_SCOPE")
    try:
        response = client.post(base + "/" + method + ".json", json=args,
                               follow_redirects=False, timeout=24)
        need(response.status_code == 200
             and 0 < len(response.content) <= 350000,
             "AI_TASK_AUDIT_HTTP_UNVERIFIED")
        data = response.json()
        need(type(data) is dict and "error" not in data
             and type(data.get("result")) is dict,
             "AI_TASK_AUDIT_RESULT_UNVERIFIED")
        return data["result"]
    except (httpx.HTTPError, ValueError, TypeError):
        raise MetadataBlocked("AI_TASK_AUDIT_TRANSPORT_UNVERIFIED") from None


def inspect(client, webhook):
    base = validate_webhook(webhook)
    need(base.startswith("https://serviseit.bitrix24.ru/rest/"),
         "AI_TASK_AUDIT_NOT_CORPORATE_BITRIX")
    category_type = rest(client, base, "crm.type.getByEntityTypeId",
                         {"entityTypeId": 1062})
    typ = category_type.get("type")
    need(type(typ) is dict and str(typ.get("entityTypeId")) == "1062"
         and str(typ.get("id")) == "13",
         "AI_TASK_AUDIT_SMART_PROCESS_MISMATCH")
    field_data = rest(client, base, "crm.item.fields",
                      {"entityTypeId": 1062, "useOriginalUfNames": "Y"})
    fields = field_data.get("fields")
    need(type(fields) is dict, "AI_TASK_AUDIT_FIELDS_MISSING")
    nine = {}
    for suffix in AI_SUFFIXES:
        code = PREFIX + suffix
        info = fields.get(code)
        nine[suffix] = (type(info) is dict
                        and info.get("type") in ("string", "text")
                        and info.get("isReadOnly") is False
                        and info.get("isMultiple") in (False, None))
    tasks = rest(client, base, "tasks.task.getFields", {})
    task_fields = tasks.get("fields")
    need(type(task_fields) is dict, "AI_TASK_AUDIT_TASK_FIELDS_MISSING")
    task_api = (all(type(task_fields.get(c)) is dict
                    for c in ("TITLE", "DESCRIPTION",
                              "RESPONSIBLE_ID", "UF_CRM_TASK"))
                and task_fields["UF_CRM_TASK"].get("type") == "crm")
    links = typ.get("linkedUserFields") or {}
    task_link = (typ.get("isUseInUserfieldEnabled") in (True, "Y", "true")
                 and type(links) is dict
                 and links.get("TASKS_TASK|UF_CRM_TASK")
                     in (True, "Y", "true", "1"))
    return {
        "status": ("NINE_AI_TASK_METADATA_READY"
                   if all(nine.values()) and task_api and task_link
                   else "NINE_AI_TASK_METADATA_GAPS"),
        "nine_ai_fields_present_and_writable": sum(nine.values()),
        "nine_ai_fields_total": 9,
        "per_field_metadata": nine,
        "task_api_fields_visible": task_api,
        "task_link_configured": task_link,
        "real_task_add_readback_verified_by_this_check": False,
        "real_nine_ai_write_verified_by_this_check": False,
        "crm_writes": 0, "tasks_created": 0, "model_calls": 0,
    }


def runtime_gates(env):
    """Read deployment gates only; never print URLs, secret keys or customer data."""
    need(type(env) is dict, "AI_RUNTIME_ENV_INVALID")
    enabled = env.get("SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED") == "YES"
    privacy = env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
    hosts = env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "")
    import re
    parts = hosts.split(",") if hosts else []
    host_configured = bool(
        0 < len(parts) <= 20
        and all(bool(re.fullmatch(
            r"(?:[a-z0-9-]{1,63}\\.)+[a-z]{2,20}", h))
            and not h.endswith((".test", ".invalid", ".local", ".localhost"))
            for h in parts)
    )
    poll = env.get("SERVICEIT_BITRIX_POLL_ENABLED") == "YES_1062_21_READONLY"
    worker = env.get("SERVICEIT_BITRIX_EVENT_AI_ENABLED") == "YES_1062_21_CLOUD"
    return {
        "scheduled_new_card_poll_enabled": poll,
        "nine_field_event_worker_enabled": worker,
        "model_privacy_approved": privacy,
        "public_title_model_enabled": enabled,
        "approved_public_title_hostlist_valid": host_configured,
        "public_title_model_call_eligible_by_config":
            poll and worker and privacy and enabled and host_configured,
        "model_called_in_this_check": False,
        "source_prepopulated_nine_hold_fields_may_skip_model": True,
        "all_new_cards_receive_llm_analysis_proven": False,
    }


def main():
    try:
        env = dict(os.environ)
        gate(env)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            output = inspect(client, env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
        output["runtime"] = runtime_gates(env)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0 if output["status"] == "NINE_AI_TASK_METADATA_READY" else 2
    except Exception:
        print(json.dumps({"status": "NINE_AI_TASK_AUDIT_BLOCKED",
                          "crm_writes": 0, "tasks_created": 0, "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
