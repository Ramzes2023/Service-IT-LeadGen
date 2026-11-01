"""Company-portal Bitrix24 metadata audit. Read-only and no customer records.

Calls ONLY read-only metadata methods: crm.category.list, crm.item.fields,
crm.type.list and crm.status.list.
Never accesses CRM items, contacts, tasks, deals, timeline or robots.
Use only an administrator-authorized webhook with the CRM scope.
Webhook is passed only through the process environment and never printed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

PORTAL = "serviseit.bitrix24.ru"
ALLOWLIST = frozenset({"crm.category.list", "crm.item.fields", "crm.type.list", "crm.status.list"})
ENTITIES = {1: "lead", 2: "deal", 3: "contact", 4: "company"}
MAX_BYTES = 512_000
AUDIT_VERSION = "mvp-bitrix24-metadata-v1"


def validate_webhook(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) > 1000 or not raw or raw != raw.strip():
        raise ValueError("BITRIX24_WEBHOOK_MISSING_OR_BAD_FORMAT")
    try:
        u = urlsplit(raw)
        if (u.scheme != "https" or (u.hostname or "").lower() != PORTAL
                or u.port not in (None, 443) or u.username or u.password
                or u.query or u.fragment or not re.fullmatch(
                    r"/rest/[1-9][0-9]*/[A-Za-z0-9_-]{6,}/?", u.path
                )):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError("BITRIX24_WEBHOOK_MUST_BE_OFFICIAL_PORTAL") from None
    return raw.rstrip("/")


def safe_name(value: object) -> str:
    """Avoid embedding arbitrary payloads, URLs, contacts or control sequences."""
    if not isinstance(value, str) or len(value) > 90 or not value.strip():
        return "[название скрыто]"
    if (re.search(r"@|https?://|(?:\+?7|8)[0-9\s()\-]{10,}", value, re.I)
            or re.search(r"[\x00-\x1f\x7f]", value)):
        return "[название скрыто]"
    return value.strip()


def _post_read(client: httpx.Client, base: str, method: str, params: dict) -> tuple[str, object | None]:
    if method not in ALLOWLIST:
        raise ValueError("BITRIX24_METHOD_NOT_ALLOWED")
    try:
        resp = client.post(base + "/" + method, json=params, follow_redirects=False)
        if resp.status_code != 200 or len(resp.content) > MAX_BYTES:
            return "HTTP_DENIED_OR_UNAVAILABLE", None
        payload = resp.json()
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            return "API_DENIED_OR_UNAVAILABLE", None
        return "OK", payload["result"]
    except (httpx.HTTPError, ValueError, TypeError):
        # Never print raw exception: it may contain the secret webhook URL.
        return "HTTP_OR_PARSE_FAILED", None


def offline_blueprint() -> dict:
    return {
        "version": AUDIT_VERSION,
        "portal": PORTAL, "mode": "OFFLINE_PLAN_NO_NETWORK",
        "crm_writes": "NO", "customer_records_read": 0,
        "approved_test_space": "NOT_VERIFIED",
        "expected_entities": ["lead", "deal", "company", "smart_process"],
        "expected_fields": [
            "ИНН", "Название", "Регион", "ID сигнала", "Дата/ссылка/подтверждение",
            "Вид услуги", "Lead Score и версия", "Стоп-лист/дубль",
            "Статус проверки", "Ответственный", "Задача/следующее действие",
        ],
        "production_support_funnel": "PRESERVE_AND_DO_NOT_WRITE",
        "real_access": "NOT_CHECKED",
        "source_integrations": "OUT_OF_SCOPE",
    }


def _fields_summary(result: object) -> dict:
    fields = result.get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, dict):
        return {"visible_codes_count": 0, "has_inn_uf_candidate": False,
                "user_field_codes": []}
    codes = [key for key in fields if isinstance(key, str)
             and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,80}", key)]
    return {
        "visible_codes_count": len(codes),
        "has_inn_uf_candidate": any("INN" in key.upper() for key in codes),
        "user_field_codes": sorted(key for key in codes if key.upper().startswith("UF_CRM"))[:100],
    }


def _read_categories(client: httpx.Client, base: str, entity_type_id: int,
                     report: dict) -> list[dict]:
    status, result = _post_read(client, base, "crm.category.list",
                                {"entityTypeId": entity_type_id})
    report["methods"][f"crm.category.list.{entity_type_id}"] = status
    categories = result.get("categories") if status == "OK" and isinstance(result, dict) else None
    if not isinstance(categories, list):
        return []
    normalized = []
    for item in categories[:50]:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if type(cid) is int and 0 <= cid <= 99999999:
            normalized.append({"id": cid, "name": safe_name(item.get("name"))})
    report["category_list_paginated"][str(entity_type_id)] = len(categories) >= 50
    return normalized


def _read_stages(client: httpx.Client, base: str, entity_type_id: int,
                 category_id: int, report: dict) -> list[dict]:
    entity_code = ("DEAL_STAGE" if entity_type_id == 2 and category_id == 0 else
                   f"DEAL_STAGE_{category_id}" if entity_type_id == 2 else
                   f"DYNAMIC_{entity_type_id}_STAGE_{category_id}")
    status, result = _post_read(client, base, "crm.status.list",
                                {"filter": {"ENTITY_ID": entity_code}})
    key = f"crm.status.list.{entity_code}"
    report["methods"][key] = status
    if status != "OK" or not isinstance(result, list):
        return []
    stages = []
    for item in result[:100]:
        if (isinstance(item, dict) and item.get("ENTITY_ID") == entity_code
                and isinstance(item.get("STATUS_ID"), str)):
            stages.append({"id": safe_name(item["STATUS_ID"]),
                           "name": safe_name(item.get("NAME")),
                           "semantics": safe_name(item.get("SEMANTICS"))})
    return stages


def audit(webhook: str, *, client: httpx.Client | None = None) -> dict:
    base = validate_webhook(webhook)
    own = client is None
    if own:
        client = httpx.Client(timeout=12.0, follow_redirects=False, trust_env=False)
    report = {
        "version": AUDIT_VERSION, "portal": PORTAL,
        "mode": "LIVE_METADATA_READ_ONLY", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "crm_writes": "NO", "customer_records_read": 0,
        "email_sms_calls": "NO", "test_record_created": False,
        "production_support_funnel": "PRESERVE_AND_DO_NOT_WRITE",
        "permissions_inferred_from_menu": False,
        "safe_test_space": "NOT_VERIFIED",
        "methods": {}, "deal_categories": [], "smart_processes": [],
        "entity_fields": {}, "category_list_paginated": {},
    }
    try:
        report["deal_categories"] = _read_categories(client, base, 2, report)
        for category in report["deal_categories"]:
            category["stages"] = _read_stages(client, base, 2, category["id"], report)
        for entity_id, name in ENTITIES.items():
            status, result = _post_read(client, base, "crm.item.fields", {
                "entityTypeId": entity_id, "useOriginalUfNames": "Y"
            })
            report["methods"]["crm.item.fields." + name] = status
            report["entity_fields"][name] = _fields_summary(result if status == "OK" else None)
        status, result = _post_read(client, base, "crm.type.list", {})
        report["methods"]["crm.type.list"] = status
        if status == "OK" and isinstance(result, dict) and isinstance(result.get("types"), list):
            for item in result["types"][:50]:
                if not isinstance(item, dict):
                    continue
                eid = item.get("entityTypeId")
                if type(eid) is int and eid >= 128:
                    process = {"entityTypeId": eid, "title": safe_name(item.get("title")),
                               "fields": {}, "categories": []}
                    field_status, field_result = _post_read(client, base, "crm.item.fields", {
                        "entityTypeId": eid, "useOriginalUfNames": "Y"
                    })
                    report["methods"][f"crm.item.fields.smart_process.{eid}"] = field_status
                    process["fields"] = _fields_summary(field_result if field_status == "OK" else None)
                    process["categories"] = _read_categories(client, base, eid, report)
                    for category in process["categories"]:
                        category["stages"] = _read_stages(client, base, eid, category["id"], report)
                    report["smart_processes"].append(process)
        mandatory = [
            report["methods"].get("crm.category.list.2"),
            report["methods"].get("crm.item.fields.lead"),
            report["methods"].get("crm.item.fields.deal"),
            report["methods"].get("crm.item.fields.contact"),
            report["methods"].get("crm.item.fields.company"),
        ]
        report["status"] = (
            "METADATA_READ_PASS" if all(x == "OK" for x in mandatory)
            else "PARTIAL_OR_ACCESS_DENIED"
        )
        report["crm_write_authorized"] = False
        report["manager_approved"] = False
        report["next_gate"] = "VERIFY_ISOLATED_TEST_PROCESS_AND_ADMIN_APPROVAL"
        return report
    finally:
        if own:
            client.close()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Service IT Bitrix metadata-only audit")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--read-only", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    out = Path(args.output).resolve()
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ServiceIT" / "Bitrix24-Metadata"
    root = root.resolve()
    if out.suffix.lower() != ".json" or not out.is_relative_to(root) or out.exists():
        raise SystemExit("BITRIX_AUDIT_OUTPUT_MUST_BE_NEW_JSON_UNDER_LOCALAPPDATA")
    try:
        if args.offline:
            result = offline_blueprint()
        else:
            result = audit(os.environ.get("BITRIX24_AUDIT_WEBHOOK", ""))
    except ValueError:
        raise SystemExit("BITRIX_AUDIT_WEBHOOK_MISSING_OR_NOT_APPROVED_FORMAT") from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("BITRIX_AUDIT_MODE=" + result["mode"])
    print("BITRIX_AUDIT_STATUS=" + result.get("status", "OFFLINE_PLAN_ONLY"))
    print("BITRIX_AUDIT_METHODS=" + json.dumps(result.get("methods", {}), sort_keys=True))
    print("BITRIX_DEAL_CATEGORIES_FOUND=" + str(len(result.get("deal_categories", []))))
    print("BITRIX_SMART_PROCESSES_FOUND=" + str(len(result.get("smart_processes", []))))
    print("BITRIX_CRM_WRITES=NO; CRM_CUSTOMER_RECORDS_READ=0; NO_EMAILS_CALLS")
    print("BITRIX_AUDIT_REPORT=" + str(out))


if __name__ == "__main__":
    run()
