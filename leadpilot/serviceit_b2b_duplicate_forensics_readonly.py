"""Bounded, sanitized Bitrix 1062/21 source identity probe (NO WRITES).

Diagnose the source-filter anomaly before authorizing any live CRM POST.
Only known historical item 7 and fixed source IDs are inspected; output uses
fixed labels and enums without CRM values, response bodies or webhook tokens.
"""
from __future__ import annotations

import json
import os
import re
import httpx

from .bitrix_metadata_audit import validate_webhook

ENTITY = 1062
CATEGORY = 21
ORIGINAL = "UF_CRM_13_SVCIT_SIGNAL_ID"
CAMEL = "ufCrm13SvcitSignalId"
KNOWN7 = "B2B-CENTER-4661617"
OLD = "B2B-CENTER-4665948"
ABSENT = "B2B-CENTER-999999999999"


def _call(client, base, method, payload):
    assert method in {"crm.item.get", "crm.item.fields", "crm.item.list"}
    response = client.post(
        base + "/" + method + ".json", json=payload,
        follow_redirects=False, timeout=15)
    if response.status_code != 200 or len(response.content) > 256000:
        return None
    try:
        result = response.json()
    except (ValueError, TypeError):
        return None
    if type(result) is not dict or "error" in result or type(result.get("result")) is not dict:
        return None
    return result


def _shape(response, expected, field):
    if response is None:
        return "API_ERROR"
    results = response["result"]
    rows = results.get("items")
    if type(rows) is not list or len(rows) > 50:
        return "INVALID_ROWS"
    if "next" in response or "next" in results:
        return "PAGED"
    if not rows:
        return "EMPTY"
    if not all(type(row) is dict and row.get("categoryId") in (21, "21")
               and type(row.get("id")) in (int, str)
               and str(row.get("id")).isdigit() for row in rows):
        return "UNTRUSTED_ROW"
    if all(row.get(field) == expected for row in rows):
        return "SOURCE_MATCH"
    if all(field not in row for row in rows):
        return "FIELD_ABSENT"
    return "FIELD_MISMATCH"


def run(secret, client):
    base = validate_webhook(secret)
    result = {"status": "B2B_DUPLICATE_FORENSICS_READONLY",
              "crm_writes": 0, "outreach": 0, "source_replay": 0}
    for mode in ("Y", "N"):
        get = _call(client, base, "crm.item.get", {
            "entityTypeId": ENTITY, "id": 7, "useOriginalUfNames": mode})
        item = ((get or {}).get("result") or {}).get("item")
        expected_field = ORIGINAL if mode == "Y" else CAMEL
        status = ("UNAVAILABLE" if type(item) is not dict else
                  "OUT_OF_SCOPE" if str(item.get("id")) != "7" or
                    str(item.get("entityTypeId")) != str(ENTITY) or
                    str(item.get("categoryId")) != str(CATEGORY) else
                  "KNOWN_4661617" if item.get(expected_field) == KNOWN7 else
                  "OTHER_OR_MISSING")
        result["GET_7_"+mode] = status
        fields = _call(client, base, "crm.item.fields", {
            "entityTypeId": ENTITY, "useOriginalUfNames": mode})
        meta = ((fields or {}).get("result") or {}).get("fields")
        result["FIELD_"+mode] = (
            "AVAILABLE" if type(meta) is dict and expected_field in meta else
            "NOT_AVAILABLE")
    probes = [
        ("KNOWN_4661617_Y", "Y", ORIGINAL, KNOWN7),
        ("KNOWN_4661617_N", "N", CAMEL, KNOWN7),
        ("OLD_4665948_Y", "Y", ORIGINAL, OLD),
        ("OLD_4665948_N", "N", CAMEL, OLD),
        ("ABSENT_N", "N", CAMEL, ABSENT),
    ]
    for label, mode, field, source in probes:
        list_response = _call(client, base, "crm.item.list", {
            "entityTypeId": ENTITY, "useOriginalUfNames": mode,
            "filter": {"categoryId": CATEGORY, field: source}, "start": 0})
        result["LIST_"+label] = _shape(list_response, source, field)
    return result


def main():
    secret = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not secret:
        print("B2B_DUPLICATE_DIAG=SECRET_MISSING")
        return 2
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            data = run(secret, client)
        print(json.dumps(data, sort_keys=True))
        return 0
    except Exception:
        print("B2B_DUPLICATE_DIAG=BLOCKED; CRM_WRITES=ZERO")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
