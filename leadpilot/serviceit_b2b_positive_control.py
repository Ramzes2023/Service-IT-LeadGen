"""Fail-closed positive/negative Bitrix 1062/21 source filter control.

Never depend on a deleted historical B2B item. Use independently readable
existing items as positive source identity witnesses, checking both native
field-name formats and exact item GET. Source values never enter logs.
"""
from __future__ import annotations

import re

from .b2b_github_to_bitrix import bitrix_call, exact_duplicate, BridgeBlocked

ENTITY = 1062
CATEGORY = 21
CANARY_ID = 3
FALLBACK_CANARY_IDS = (17, 15, 13, 11)
ORIGINAL = "UF_CRM_13_SVCIT_SIGNAL_ID"
CAMEL = "ufCrm13SvcitSignalId"
ABSENT = "B2B-CENTER-999999999999"


def positive_duplicate_control(client, base):
    for card in dict.fromkeys((CANARY_ID, *FALLBACK_CANARY_IDS)):
        try:
            found = bitrix_call(client, base, "crm.item.get", {
                "entityTypeId": ENTITY, "id": card, "useOriginalUfNames": "Y",
            })["result"].get("item")
        except BridgeBlocked as error:
            # A retired control can return HTTP 400. The replacement still
            # has to pass exact GET, both list formats and the negative test.
            if str(error) not in {"CRM_TRANSPORT_UNCERTAIN", "CRM_RESULT_UNCERTAIN"}:
                raise
            continue
        break
    else:
        raise BridgeBlocked("B2B_POSITIVE_CONTROL_ITEM_UNAVAILABLE")
    if not isinstance(found, dict) or (
        str(found.get("id")) != str(card)
        or str(found.get("entityTypeId")) != str(ENTITY)
        or str(found.get("categoryId")) != str(CATEGORY)
    ):
        raise BridgeBlocked("B2B_POSITIVE_CONTROL_ITEM_UNAVAILABLE")
    source = found.get(ORIGINAL)
    if not (isinstance(source, str)
            and re.fullmatch(r"[A-Za-z0-9:_-]{4,120}", source)):
        raise BridgeBlocked("B2B_POSITIVE_CONTROL_SOURCE_UNAVAILABLE")

    for mode, field in (("Y", ORIGINAL), ("N", CAMEL)):
        response = bitrix_call(client, base, "crm.item.list", {
            "entityTypeId": ENTITY, "useOriginalUfNames": mode,
            "filter": {"categoryId": CATEGORY, field: source},
            "start": 0,
        })
        body = response["result"]
        rows = body.get("items")
        if ("next" in body or "next" in response
                or not isinstance(rows, list) or len(rows) != 1):
            raise BridgeBlocked("B2B_POSITIVE_CONTROL_LIST_UNVERIFIED")
        row = rows[0]
        if (not isinstance(row, dict) or str(row.get("id")) != str(card)
                or str(row.get("categoryId")) != str(CATEGORY)
                or row.get(field) != source):
            raise BridgeBlocked("B2B_POSITIVE_CONTROL_LIST_MISMATCH")
        for total in (body.get("total"), response.get("total")):
            if total is not None and (type(total) is not int or total != 1):
                raise BridgeBlocked("B2B_POSITIVE_CONTROL_TOTAL_MISMATCH")

    # Prove a fabricated but syntactically valid source gives no result.
    if exact_duplicate(client, base, ABSENT):
        raise BridgeBlocked("B2B_NEGATIVE_CONTROL_CONFLICT")
    return True
