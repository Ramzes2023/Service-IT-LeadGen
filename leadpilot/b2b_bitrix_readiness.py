"""Read-only corporate Bitrix24 1062/21 schema preflight for B2B tender HOLD.

Separate from synthetic-only writers. Does NOT fetch client records, add/update
CRM items, read contacts, send messages, change automation or receive Gmail.
"""
from __future__ import annotations

import argparse
import json
import os
import re

import httpx

from .bitrix_1062_mapping import ALL_FIELD_CODES
from .bitrix_metadata_audit import validate_webhook

ENTITY = 1062
CATEGORY = 21
TYPE_ID = 13
STAGE = "DT1062_21:NEW"
REVIEW_FIELDS = frozenset({
    "UF_CRM_13_SVCIT_SIGNAL_KIND",
    "UF_CRM_13_SVCIT_SIGNAL_ID",
    "UF_CRM_13_SVCIT_SIGNAL_SOURCE",
    "UF_CRM_13_SVCIT_SIGNAL_URL",
    "UF_CRM_13_SVCIT_SUGGESTED_SERVICE",
    "UF_CRM_13_SVCIT_COMPANY_NAME",
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS",
    "UF_CRM_13_SVCIT_REVIEW_DECISION",
    "UF_CRM_13_SVCIT_STOP_REASON",
})
assert REVIEW_FIELDS.issubset(ALL_FIELD_CODES)
ALLOW = frozenset({
    "crm.type.getByEntityTypeId", "crm.category.list",
    "crm.item.fields", "crm.status.list",
})


class PreflightBlocked(ValueError):
    pass


def _must(ok: bool, reason: str):
    if not ok:
        raise PreflightBlocked(reason)


class ReadOnlyBitrix:
    def __init__(self, base_webhook, *, client: httpx.Client):
        self.base = validate_webhook(base_webhook)
        _must(isinstance(client, httpx.Client), "HTTP_CLIENT_REQUIRED")
        self.client = client
        self.methods: list[str] = []

    def request(self, method, params):
        _must(method in ALLOW and isinstance(params, dict),
              "REST_METHOD_DENIED")
        self.methods.append(method)
        _must(len(self.methods) <= 5, "REST_BUDGET_EXCEEDED")
        try:
            result = self.client.post(self.base + "/" + method + ".json",
                                      json=params, follow_redirects=False,
                                      timeout=12)
            # Do not swallow a deliberate fail-closed gate exception as a transport error.
            if result.status_code != 200 or len(result.content) > 256000:
                raise PreflightBlocked("REST_HTTP_DENIED")
            response = result.json()
        except PreflightBlocked:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise PreflightBlocked("REST_TRANSPORT_OR_PARSE_DENIED") from None
        _must(isinstance(response, dict)
              and "error" not in response
              and "result" in response
              and "next" not in response, "REST_RESULT_DENIED")
        return response["result"]

    def __repr__(self):
        return "ReadOnlyBitrix(1062/21; no CRM writes)"


def _item_fields(fields_result):
    _must(isinstance(fields_result, dict)
          and isinstance(fields_result.get("fields"), dict),
          "FIELDS_NOT_VISIBLE")
    values = fields_result["fields"]
    _must(ALL_FIELD_CODES.issubset(values), "SVCIT_ORIGINAL_16_FIELDS_MISSING")
    types = {}
    for name in REVIEW_FIELDS:
        meta = values.get(name)
        _must(isinstance(meta, dict), "REVIEW_FIELD_METADATA_INVALID")
        _must(meta.get("type") in ("string", "text")
              and meta.get("isReadOnly") in (None, False, "N")
              and meta.get("isImmutable") in (None, False, "N"),
              "REVIEW_FIELD_NOT_WRITABLE_TEXT")
        types[name] = meta["type"]
    return types


def _category(categories):
    _must(isinstance(categories, dict)
          and isinstance(categories.get("categories"), list)
          and len(categories["categories"]) < 50, "CATEGORY_METADATA_AMBIGUOUS")
    matching = [v for v in categories["categories"] if
                isinstance(v, dict) and v.get("id") == CATEGORY]
    _must(len(matching) == 1, "TARGET_CATEGORY_21_NOT_FOUND")


def _stages(data):
    _must(isinstance(data, list) and len(data) < 100, "STAGE_METADATA_AMBIGUOUS")
    statuses = [x.get("STATUS_ID") for x in data if
                isinstance(x, dict) and
                x.get("ENTITY_ID") == "DYNAMIC_1062_STAGE_21"]
    _must(STAGE in statuses and len(statuses) == len(set(statuses)),
          "SAFE_NEW_STAGE_NOT_VERIFIED")


def _type(info):
    _must(isinstance(info, dict) and isinstance(info.get("type"), dict),
          "TYPE_INFO_MISSING")
    t = info["type"]
    _must(str(t.get("id")) == str(TYPE_ID)
          and str(t.get("entityTypeId")) == str(ENTITY),
          "SMART_PROCESS_IDENTITY_MISMATCH")
    _must(t.get("isAutomationEnabled") in (False, "N")
          and t.get("isBizProcEnabled") in (False, "N"),
          "AUTOMATION_NOT_CONFIRMED_DISABLED")


def preflight(rest: ReadOnlyBitrix) -> dict:
    _must(isinstance(rest, ReadOnlyBitrix), "READ_ONLY_TRANSPORT_REQUIRED")
    # Fail before any network if the code has somehow changed its allowlist.
    _must(all("add" not in x and "update" not in x and "delete" not in x
              for x in ALLOW), "WRITE_METHOD_IN_ALLOWLIST")
    _type(rest.request("crm.type.getByEntityTypeId",
                       {"entityTypeId": ENTITY}))
    _category(rest.request("crm.category.list", {"entityTypeId": ENTITY}))
    fields = _item_fields(rest.request(
        "crm.item.fields", {"entityTypeId": ENTITY, "useOriginalUfNames": "Y"}))
    _stages(rest.request(
        "crm.status.list",
        {"filter": {"ENTITY_ID": "DYNAMIC_1062_STAGE_21"}}))
    return {
        "status": "BITRIX_B2B_HOLD_METADATA_READ_ONLY_PASS",
        "entity_type_id": ENTITY, "category_id": CATEGORY,
        "known_original_fields": len(ALL_FIELD_CODES),
        "checked_review_fields": len(fields),
        "safe_review_stage": STAGE,
        "automations_confirmed_disabled": True,
        "methods": rest.methods,
        "real_procurement_cards_created": 0,
        "qualified_real_leads": 0,
        "crm_writes": "NO",
        "crm_customer_records_read": 0,
        "outreach": "NO",
        "production_source_worker_enabled": False,
        "next_gate": "ACTUAL_EMAIL_MIME_TEST_ORIGINAL_BUYER_FNS_AND_EXPLICIT_WRITE_AUTHORIZATION",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Bitrix B2B tender schema preflight")
    parser.add_argument("--read-only", action="store_true", required=True)
    opts = parser.parse_args(argv)
    base = os.environ.get("BITRIX24_B2B_READONLY_WEBHOOK", "")
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            rest = ReadOnlyBitrix(base, client=client)
            result = preflight(rest)
    except (PreflightBlocked, ValueError):
        print("BITRIX_B2B_PREFLIGHT=BLOCKED; NO_CRM_WRITES; CHECK_LOCAL_READONLY_PERMISSIONS")
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
