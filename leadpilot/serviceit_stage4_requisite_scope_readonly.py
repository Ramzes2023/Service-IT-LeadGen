"""Live READ-ONLY company-requisite access preflight for stage 4.

Never dumps identities, company names, INNs, matches or REST bodies.
The public synthetic test INN is used ONLY to verify a scoped exact query.
A result of zero visible matches does NOT prove all CRM customers are clear.
"""
from __future__ import annotations

import json
import os
import httpx

from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_mapping import _is_valid_inn

TEST_ONLY_INN = "7700000009"  # synthetic test fixture; not a real lead
PORTAL = "https://serviseit.bitrix24.ru/rest/"
FIELDS = ["ID", "ENTITY_TYPE_ID", "ENTITY_ID", "RQ_INN"]


class RequisitesPreflightBlocked(ValueError):
    pass


def need(ok, why):
    if not ok:
        raise RequisitesPreflightBlocked(why)


def preflight(client, hook):
    need(type(TEST_ONLY_INN) is str and _is_valid_inn(TEST_ONLY_INN),
         "CONTROL_INN_NOT_VALID")
    base = validate_webhook(hook)
    need(base.startswith(PORTAL), "PORTAL_OUTSIDE_SERVICEIT")
    methods = (
        ("crm.requisite.fields", {}),
        ("crm.requisite.list", {
            "filter": {"RQ_INN": TEST_ONLY_INN},
            "select": FIELDS, "start": 0,
        }),
    )
    responses = []
    for method, params in methods:
        need(method in ("crm.requisite.fields", "crm.requisite.list"),
             "METHOD_NOT_READONLY")
        try:
            res = client.post(base + "/" + method + ".json", json=params,
                              timeout=20, follow_redirects=False)
            need(res.status_code == 200 and 0 < len(res.content) <= 256_000,
                 "REQUISITE_METHOD_OR_SCOPE_UNAVAILABLE")
            body = res.json()
            need(type(body) is dict and "error" not in body
                 and "result" in body, "REQUISITE_REST_RESPONSE_UNVERIFIED")
            responses.append(body)
        except RequisitesPreflightBlocked:
            raise
        except (httpx.HTTPError, TypeError, ValueError):
            raise RequisitesPreflightBlocked("REQUISITE_TRANSPORT_UNVERIFIED") from None

    field_result = responses[0]["result"]
    need(type(field_result) is dict and all(
        type(field_result.get(k)) is dict for k in FIELDS),
        "REQUISITE_FIELD_SCHEMA_INCOMPLETE")
    queried = responses[1]
    rows = queried["result"]
    need(type(rows) is list and len(rows) <= 50
         and queried.get("next") is None
         and (queried.get("total") is None or
              type(queried["total"]) is int and queried["total"] == len(rows)),
         "REQUISITE_QUERY_PAGINATION_UNVERIFIED")
    for row in rows:
        need(type(row) is dict and str(row.get("RQ_INN")) == TEST_ONLY_INN
             and str(row.get("ENTITY_TYPE_ID")) in ("3", "4")
             and str(row.get("ENTITY_ID", "")).isdigit(),
             "REQUISITE_EXACT_FILTER_UNVERIFIED")
    return {
        "status": "STAGE4_REQUISITES_READONLY_SCOPE_PASS",
        "portal": "SERVICEIT_CORPORATE",
        "exact_inn_query_available": True,
        "visibility": "ONLY_CURRENT_WEBHOOK_ACCESSIBLE_REQUISITES",
        "global_customer_and_stop_clearance": False,
        "fns_legal_and_contact_verified": False,
        "score_write_authorized": False,
        "task_created": False,
        "crm_writes": 0,
    }


def main():
    hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = preflight(client, hook)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RequisitesPreflightBlocked, ValueError):
        print(json.dumps({
            "status": "STAGE4_REQUISITES_READONLY_BLOCKED",
            "reason": "CORPORATE_SCOPE_OR_FIELD_PERMISSION_UNVERIFIED",
            "score_write_authorized": False,
            "crm_writes": 0,
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
