"""Bitrix 1062/21 read-only listing shape forensics, owner-triggered only.

Never print CRM values, IDs from responses, user field contents, URLs,
headers, REST secrets or arbitrary exception strings. Exact IDs #3 and #5
are fixed historical comparison points. No CRM add/update/delete, no AI.
"""
from __future__ import annotations

import os

import httpx

from .bitrix_metadata_audit import validate_webhook

KNOWN_SOURCE = "B2B-CENTER-4665948"
SIGNAL = "UF_CRM_13_SVCIT_SIGNAL_ID"

# Deliberately small, fixed requests. No unbounded pagination or enumerate.
LIST_CASES = (
    ("KNOWN_Y_SELECTED", {"entityTypeId": 1062, "useOriginalUfNames": "Y",
                          "filter": {"categoryId": 21, SIGNAL: KNOWN_SOURCE},
                          "select": ["id", "categoryId", SIGNAL], "start": 0}),
    ("KNOWN_Y_NO_SELECT", {"entityTypeId": 1062, "useOriginalUfNames": "Y",
                           "filter": {"categoryId": 21, SIGNAL: KNOWN_SOURCE},
                           "start": 0}),
    ("CATEGORY_Y_ID_ONLY", {"entityTypeId": 1062, "useOriginalUfNames": "Y",
                            "filter": {"categoryId": 21},
                            "select": ["id", "categoryId"], "start": 0}),
    ("BROAD_Y_ID_ONLY", {"entityTypeId": 1062, "useOriginalUfNames": "Y",
                         "select": ["id", "categoryId"], "start": 0}),
    ("ID_3_Y_SELECTED", {"entityTypeId": 1062, "useOriginalUfNames": "Y",
                         "filter": {"id": 3},
                         "select": ["id", "categoryId"], "start": 0}),
    ("BROAD_N_ID_ONLY", {"entityTypeId": 1062, "useOriginalUfNames": "N",
                         "select": ["id", "categoryId"], "start": 0}),
    ("CATEGORY_N_ID_ONLY", {"entityTypeId": 1062, "useOriginalUfNames": "N",
                            "filter": {"categoryId": 21},
                            "select": ["id", "categoryId"], "start": 0}),
    ("ID_3_N_SELECTED", {"entityTypeId": 1062, "useOriginalUfNames": "N",
                         "filter": {"id": 3},
                         "select": ["id", "categoryId"], "start": 0}),
    ("CATEGORY_N_GT_3_ORDER", {"entityTypeId": 1062, "useOriginalUfNames": "N",
                               "filter": {"categoryId": 21, ">id": 3},
                               "order": {"id": "ASC"},
                               "select": ["id", "categoryId", "createdTime"],
                               "start": 0}),
    ("CATEGORY_N_RECENT", {"entityTypeId": 1062, "useOriginalUfNames": "N",
                            "filter": {"categoryId": 21,
                                       ">=createdTime": "2026-11-03T00:00:00+03:00"},
                            "select": ["id", "categoryId", "createdTime"],
                            "start": 0}),
)


def shape(value) -> str:
    if type(value) is not dict:
        return "INVALID_ENVELOPE"
    if "error" in value:
        return "API_ERROR"
    result = value.get("result")
    if type(result) is not dict:
        return "INVALID_RESULT"
    items = result.get("items")
    if type(items) is not list:
        return "NO_ITEMS_LIST"
    if len(items) > 50:
        return "TOO_MANY_ITEMS"
    if len(items) == 0:
        return "FLAT_EMPTY"
    if all(type(item) is dict for item in items):
        if any(type(row.get("id")) not in (str, int) for row in items):
            return "FLAT_MISSING_ID"
        # List records may omit entityTypeId by official design.
        if all("categoryId" in row for row in items):
            return "FLAT_WITH_CATEGORY"
        return "FLAT_WITHOUT_CATEGORY"
    if all(type(item) is list and not item for item in items):
        return "NESTED_EMPTY"
    if all(type(item) is list for item in items):
        return "NESTED_OTHER"
    return "MIXED_UNTRUSTED"


def list_probe(client: httpx.Client, base: str, params: dict) -> str:
    try:
        r = client.post(base + "/crm.item.list.json", json=params, timeout=20,
                        follow_redirects=False)
        if r.status_code != 200 or len(r.content) > 512000:
            return "HTTP_OR_SIZE_BLOCKED"
        return shape(r.json())
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return "TRANSPORT_OR_PARSE_BLOCKED"


def exact_get_probe(client: httpx.Client, base: str, card_id: int) -> str:
    assert card_id in (3, 5)
    try:
        r = client.post(base + "/crm.item.get.json",
                        json={"entityTypeId": 1062, "id": card_id,
                              "useOriginalUfNames": "Y"},
                        timeout=20, follow_redirects=False)
        if r.status_code != 200 or len(r.content) > 512000:
            return "HTTP_OR_SIZE_BLOCKED"
        body = r.json()
        if type(body) is not dict or type(body.get("result")) is not dict:
            return "INVALID_RESULT"
        row = body["result"].get("item")
        if type(row) is not dict:
            return "MISSING_ITEM"
        if str(row.get("entityTypeId")) != "1062" or str(row.get("id")) != str(card_id):
            return "ID_SCOPE_MISMATCH"
        if str(row.get("categoryId")) != "21":
            return "OTHER_CATEGORY"
        # Only a fixed equality marker, never the actual custom-field text.
        return "TARGET_MATCHES_KNOWN_SOURCE" if row.get(SIGNAL) == KNOWN_SOURCE else "TARGET_OTHER_SOURCE"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return "TRANSPORT_OR_PARSE_BLOCKED"


def main() -> int:
    webhook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not webhook:
        print("SERVICEIT_B2B_LIST_SHAPE=NO_EXISTING_SECRET")
        return 2
    try:
        base = validate_webhook(webhook)
    except ValueError:
        print("SERVICEIT_B2B_LIST_SHAPE=INVALID_REST_SCOPE")
        return 2
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        for name, params in LIST_CASES:
            print("B2B_LIST_" + name + "=" + list_probe(client, base, params))
        for card_id in (3, 5):
            print("B2B_GET_FIXED_" + str(card_id) + "=" + exact_get_probe(client, base, card_id))
    print("B2B_LIST_DIAGNOSTIC=READONLY_COMPLETE")
    print("CRM_WRITES=0; GIGACHAT_CALLS=0; GOOGLE_CHANGES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
