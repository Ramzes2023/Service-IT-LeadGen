"""Real corporate Bitrix24 CRM read-coverage audit for Lead Score.

Read-only company/contact/requisites probes on the *same* webhook used by
Stage4. Accessible pages never prove global records or role coverage.
Emits only access booleans and bounded enum strings, never company names,
customer identifiers, counts, webhook URLs or raw responses.
"""
from __future__ import annotations

import json
import os

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_stage4_requisite_scope_readonly import preflight as requisite_preflight

PORTAL = "https://serviseit.bitrix24.ru/rest/"
LISTS = {
    "companies": "crm.company.list",
    "contacts": "crm.contact.list",
    "deals": "crm.deal.list",
    "leads": "crm.lead.list",
}
EXPECTED = frozenset(LISTS.values()) | {"user.admin"}
MAX_BODY = 200_000


class CoverageBlocked(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise CoverageBlocked(code)


def rest_read(client, base, method, params):
    _require(isinstance(client, httpx.Client)
             and method in EXPECTED
             and (params == {} if method == "user.admin" else params == {
                 "filter": {}, "select": ["ID"], "start": 0
             }), "AUDIT_METHOD_NOT_ALLOWED")
    try:
        response = client.post(base + "/" + method + ".json", json=params,
                               timeout=20, follow_redirects=False)
        _require(response.status_code == 200
                 and 0 < len(response.content) <= MAX_BODY,
                 "AUDIT_HTTP_OR_PERMISSION_UNVERIFIED")
        data = response.json()
        _require(type(data) is dict and "error" not in data
                 and "result" in data,
                 "AUDIT_RESPONSE_UNVERIFIED")
        return data
    except CoverageBlocked:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise CoverageBlocked("AUDIT_TRANSPORT_OR_FORMAT_UNVERIFIED") from None


def inspect_list(client, base, method):
    try:
        data = rest_read(client, base, method,
                         {"filter": {}, "select": ["ID"], "start": 0})
        rows = data["result"]
        _require(type(rows) is list and len(rows) <= 50,
                 "AUDIT_LIST_RESPONSE_INVALID")
        ids = set()
        for row in rows:
            _require(type(row) is dict and
                     (type(row.get("ID")) is int
                      or type(row.get("ID")) is str and
                      row["ID"].isascii() and row["ID"].isdecimal()),
                     "AUDIT_ITEM_ID_INVALID")
            identifier = str(row["ID"])
            _require(identifier not in ids, "AUDIT_DUPLICATE_ITEMS")
            ids.add(identifier)
        total, next_page = data.get("total"), data.get("next")
        _require((total is None or
                  type(total) is int and 0 <= total <= 2_000_000_000)
                 and (next_page is None or
                      type(next_page) is int and next_page >= 50),
                 "AUDIT_PAGINATION_INVALID")
        _require(total is None or total >= len(rows),
                 "AUDIT_COUNT_INCONSISTENT")
        _require(not (next_page is not None and not rows),
                 "AUDIT_EMPTY_PAGINATED_RESPONSE")
        return {
            "list_read_access": "AVAILABLE",
            "visible_sample": "NONEMPTY" if rows else "EMPTY",
            "list_scope": ("PAGINATED_VISIBLE_ONLY" if next_page is not None
                           or total is not None and total > len(rows)
                           else "FIRST_PAGE_ONLY"),
        }
    except CoverageBlocked:
        return {
            "list_read_access": "UNAVAILABLE_OR_UNVERIFIED",
            "visible_sample": "UNKNOWN",
            "list_scope": "UNKNOWN",
        }


def audit(client, webhook):
    base = validate_webhook(webhook)
    _require(base.startswith(PORTAL), "AUDIT_PORTAL_OUTSIDE_COMPANY")
    try:
        result = rest_read(client, base, "user.admin", {})["result"]
        _require(type(result) is bool, "AUDIT_ADMIN_RESPONSE_INVALID")
        is_admin = result
    except CoverageBlocked:
        is_admin = None
    seen = {
        name: inspect_list(client, base, method)
        for name, method in LISTS.items()
    }
    try:
        check = requisite_preflight(client, webhook)
        req = check["status"] == "STAGE4_REQUISITES_READONLY_SCOPE_PASS"
    except Exception:
        req = False
    return {
        "status": "SERVICEIT_CRM_READ_COVERAGE_AUDIT",
        "scope": "serviseit.bitrix24.ru:CRM",
        "webhook_is_portal_admin": is_admin,
        "companies": seen["companies"],
        "contacts": seen["contacts"],
        "deals": seen["deals"],
        "leads": seen["leads"],
        "exact_requisite_filter_readable": req,
        "full_corporate_crm_coverage_verified": False,
        "global_dedup_approved": False,
        "company_manager_role_attestation_required": True,
        "formal_score_write_authorized": False,
        "crm_writes": 0,
        "task_writes": 0,
        "customer_contacts": 0,
    }


def main():
    env = dict(os.environ)
    try:
        if (env.get("GITHUB_REPOSITORY") != "Ramzes2023/Service-IT-LeadGen"
                or env.get("GITHUB_REF") != "refs/heads/main"
                or env.get("GITHUB_EVENT_NAME")
                not in ("schedule", "issue_comment")):
            raise CoverageBlocked("AUDIT_GITHUB_SCOPE_INVALID")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            outcome = audit(client, env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({
            "status": "SERVICEIT_CRM_READ_COVERAGE_BLOCKED",
            "full_corporate_crm_coverage_verified": False,
            "formal_score_write_authorized": False,
            "crm_writes": 0, "task_writes": 0,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
