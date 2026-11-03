"""Bounded read-only Bitrix24 access probe for the ServiceIT free-cloud cutover.

Run only with an authorized SERVICEIT_BITRIX_WEBHOOK environment secret.
Outputs fixed non-sensitive status codes, NEVER source/CRM text, API response,
customer names, webhook URLs, token, or arbitrary exception strings.

We probe the documented list operation using minimal parameters, a pre-existing
historical card #3 only for access comparison, and optionally an exact ID of a
CURRENT card supplied by the owner (read-only ONLY). Deleted card #7 is never
used to infer that the webhook cannot read active cards.
This code does not declare an upper-bound cutover and cannot activate cloud.
"""
from __future__ import annotations

import os

import httpx

from .serviceit_enrichment_worker import Rest


def _list_kind(result):
    if not isinstance(result, dict):
        return "INVALID_RESULT"
    records = result.get("items")
    if type(records) is not list or len(records) > 50:
        return "INVALID_ITEMS"
    if not records:
        return "FLAT_EMPTY"
    if all(type(item) is dict for item in records):
        return "FLAT_OBJECTS"
    if all(type(group) is list for group in records):
        if all(len(group) == 0 for group in records):
            return "NESTED_EMPTY"
        if all(group and all(type(x) is dict for x in group) for group in records):
            return "NESTED_OBJECTS"
        return "NESTED_MALFORMED"
    return "MIXED_OR_INVALID"


def _safe_list(rest, params):
    try:
        return _list_kind(rest("crm.item.list", params))
    except Exception:
        return "API_UNAVAILABLE"


def _safe_card(rest, card_id):
    try:
        result = rest("crm.item.get", {
            "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y",
        })
        item = result.get("item")
        if not isinstance(item, dict):
            return "INVALID_RESULT"
        if (str(item.get("id")) == str(card_id)
                and str(item.get("entityTypeId")) == "1062"
                and str(item.get("categoryId")) == "21"):
            return "VISIBLE_1062_21"
        if (str(item.get("id")) == str(card_id)
                and str(item.get("entityTypeId")) == "1062"):
            return "VISIBLE_OTHER_CATEGORY"
        return "IDENTITY_UNVERIFIED"
    except Exception:
        return "API_UNAVAILABLE"


def _safe_type(rest):
    try:
        result = rest("crm.type.getByEntityTypeId", {"entityTypeId": 1062})
        row = result.get("type")
        if (isinstance(row, dict) and
                str(row.get("entityTypeId")) == "1062"):
            return "VERIFIED_1062"
        return "UNVERIFIED"
    except Exception:
        return "API_UNAVAILABLE"


def probe(rest, *, current_id=None):
    """Only scoped read calls; never search IDs or infer cloud cutover."""
    if current_id is not None and (type(current_id) is not int or
                                   not 8 <= current_id <= 2_000_000_000):
        raise ValueError("CURRENT_CARD_ID_INVALID")
    return {
        "TYPE": _safe_type(rest),
        "LIST_MINIMAL": _safe_list(rest, {
            "entityTypeId": 1062, "useOriginalUfNames": "Y",
            "select": ["id", "categoryId"], "start": 0,
        }),
        "LIST_CATEGORY_ONLY": _safe_list(rest, {
            "entityTypeId": 1062, "useOriginalUfNames": "Y",
            "filter": {"categoryId": 21},
            "select": ["id", "categoryId"], "start": 0,
        }),
        "GET_3": _safe_card(rest, 3),
        "GET_CURRENT": (_safe_card(rest, current_id)
                        if current_id is not None else "NOT_SPECIFIED"),
    }


def main():
    webhook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not webhook:
        print("SERVICEIT_CUTOVER_PROBE_STATUS=NO_WEBHOOK")
        return 2
    supplied = os.environ.pop("SERVICEIT_CUTOVER_CURRENT_CARD_ID", "").strip()
    if supplied:
        if not supplied.isascii() or not supplied.isdecimal():
            print("SERVICEIT_CUTOVER_PROBE_STATUS=CURRENT_ID_INVALID")
            return 2
        current_id = int(supplied)
        if not 8 <= current_id <= 2_000_000_000:
            print("SERVICEIT_CUTOVER_PROBE_STATUS=CURRENT_ID_INVALID")
            return 2
    else:
        current_id = None
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20,
        ) as client:
            statuses = probe(Rest(webhook, client), current_id=current_id)
    except Exception:
        print("SERVICEIT_CUTOVER_PROBE_STATUS=BLOCKED")
        return 2
    for label in ("TYPE", "LIST_MINIMAL", "LIST_CATEGORY_ONLY", "GET_3", "GET_CURRENT"):
        print("SERVICEIT_CUTOVER_PROBE_" + label + "=" + statuses[label])
    print("SERVICEIT_CUTOVER_PROBE_CRM_WRITES=0")
    print("SERVICEIT_CUTOVER_PROBE_GIGACHAT_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
