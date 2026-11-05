"""Privacy-preserving full *visible* CRM census with strict pagination.

Evidence that the REST technical user can enumerate its accessible records
across companies, contacts, leads, deals, and the LeadGen smart process.

Even a complete, stable, multi-page visible scan is NOT proof that every
employee's private objects are visible. No customer IDs, INNs, names,
webhook secrets or totals are printed, persisted or transmitted elsewhere.
No CRM writes or contact activity.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import httpx

from .bitrix_metadata_audit import validate_webhook

REPO = "Ramzes2023/Service-IT-LeadGen"
PORTAL = "https://serviseit.bitrix24.ru/rest/"
MARKER = "SERVICEIT_CRM_VISIBLE_CENSUS_ONCE_V1"
LIMIT = 50
MAX_PAGES = 40
LIMITS = {
    "companies": ("crm.company.list", None, "ID"),
    "contacts": ("crm.contact.list", None, "ID"),
    "leads": ("crm.item.list", 1, "id"),
    "deals": ("crm.item.list", 2, "id"),
    "leadgen_1062_21": ("crm.item.list", 1062, "id"),
}


class CensusBlocked(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise CensusBlocked(reason)


def owner_event(env):
    if env.get("GITHUB_EVENT_NAME") == "schedule":
        return
    need(env.get("GITHUB_EVENT_NAME") == "issue_comment",
         "CENSUS_EVENT_FORBIDDEN")
    path = Path(env.get("GITHUB_EVENT_PATH", ""))
    need(path.is_file() and 0 < path.stat().st_size < 256000,
         "CENSUS_OWNER_EVENT_UNREADABLE")
    data = json.loads(path.read_text("utf-8"))
    need(type(data) is dict and data.get("action") == "created"
         and (data.get("repository") or {}).get("full_name") == REPO
         and (data.get("issue") or {}).get("number") == 200
         and not (data.get("issue") or {}).get("pull_request")
         and (data.get("sender") or {}).get("login") == "Ramzes2023"
         and (data.get("comment") or {}).get("body") == MARKER,
         "CENSUS_OWNER_EVENT_NOT_APPROVED")


def rest(client, base, kind, start):
    need(kind in LIMITS and type(start) is int and
         0 <= start < MAX_PAGES * LIMIT and start % LIMIT == 0,
         "CENSUS_REST_ALLOWLIST")
    method, entity, key = LIMITS[kind]
    if entity is None:
        params = {"filter": {}, "select": [key], "start": start}
    else:
        filt = {"categoryId": 21} if entity == 1062 else {}
        params = {"entityTypeId": entity, "filter": filt,
                  "select": [key], "start": start}
    try:
        res = client.post(base + "/" + method + ".json",
                          json=params, timeout=22, follow_redirects=False)
        need(res.status_code == 200 and 0 < len(res.content) <= 256000,
             "CENSUS_HTTP_OR_PERMISSION_UNKNOWN")
        payload = res.json()
        need(type(payload) is dict and "error" not in payload,
             "CENSUS_REST_ERROR")
        if entity is None:
            rows = payload.get("result")
            container = payload
        else:
            container = payload.get("result")
            need(type(container) is dict, "CENSUS_ITEM_CONTAINER_UNKNOWN")
            rows = container.get("items")
        need(type(rows) is list and len(rows) <= LIMIT,
             "CENSUS_ROWS_UNKNOWN")
        for meta in ("next", "total"):
            need(meta not in payload or meta not in container
                 or payload[meta] == container[meta],
                 "CENSUS_PAGINATION_CONFLICT")
        next_page = payload.get("next", container.get("next"))
        total = payload.get("total", container.get("total"))
        need(next_page is None or
             (type(next_page) is int and next_page == start + LIMIT
              and len(rows) == LIMIT),
             "CENSUS_NEXT_PAGE_NOT_VERIFIED")
        need(total is None or type(total) is int and total >= 0,
             "CENSUS_TOTAL_INVALID")
        ids = []
        for row in rows:
            need(type(row) is dict
                 and (type(row.get(key)) is int
                      and row[key] > 0
                      or type(row.get(key)) is str
                      and row[key].isascii() and row[key].isdecimal()
                      and int(row[key]) > 0),
                 "CENSUS_RECORD_ID_NOT_VERIFIED")
            ids.append(int(row[key]))
        return ids, next_page, total
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise CensusBlocked("CENSUS_TRANSPORT_OR_FORMAT_UNKNOWN") from None


def inspect_domain(client, base, domain):
    need(domain in LIMITS, "CENSUS_DOMAIN_NOT_ALLOWED")
    observed = set()
    expected_total = None
    position = 0
    for page in range(MAX_PAGES):
        ids, following, total = rest(client, base, domain, position)
        need(not any(i in observed for i in ids),
             "CENSUS_DUPLICATE_PAGE_OR_MUTATED_RESULT")
        observed.update(ids)
        if total is not None:
            if expected_total is None:
                expected_total = total
            need(expected_total == total, "CENSUS_TOTAL_CHANGED")
        if following is None:
            need(expected_total is None or expected_total == len(observed),
                 "CENSUS_TOTAL_DOES_NOT_MATCH_PAGES")
            return {"read_access": "AVAILABLE",
                    "visible_pages_complete": True,
                    "visible_sample": "NONEMPTY" if observed else "EMPTY",
                    "scope": "VISIBLE_TECHNICAL_USER_ONLY",
                    "global_visibility_proven": False}
        position = following
    return {"read_access": "AVAILABLE",
            "visible_pages_complete": False,
            "visible_sample": "NONEMPTY" if observed else "UNKNOWN",
            "scope": "OVER_BUDGET_VISIBLE_ONLY",
            "global_visibility_proven": False}


def run(client, env):
    need(env.get("GITHUB_REPOSITORY") == REPO
         and env.get("GITHUB_REF") == "refs/heads/main",
         "CENSUS_REPOSITORY_GATE")
    owner_event(env)
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(PORTAL), "CENSUS_CORPORATE_PORTAL_INVALID")
    # These are published as only bounded status enums, no identifying data.
    scope = {}
    for domain in LIMITS:
        try:
            scope[domain] = inspect_domain(client, base, domain)
        except Exception:
            scope[domain] = {"read_access": "UNAVAILABLE_OR_UNVERIFIED",
                             "visible_pages_complete": False,
                             "visible_sample": "UNKNOWN",
                             "scope": "UNKNOWN",
                             "global_visibility_proven": False}
    all_readable = all(v["read_access"] == "AVAILABLE"
                       for v in scope.values())
    all_exhausted = all(v["visible_pages_complete"]
                        for v in scope.values())
    return {
        "status": ("COMPLETE_VISIBLE_SCOPE_ONLY" if all_exhausted
                   else "VISIBLE_SCOPE_INCOMPLETE_OR_UNKNOWN"),
        "all_selected_domains_readable": all_readable,
        "all_selected_visible_pages_exhausted": all_exhausted,
        "domains": scope,
        "global_corporate_crm_visibility_proven": False,
        "formal_score_authorized": False,
        "crm_writes": 0,
        "outreach": 0,
    }


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            outcome = run(client, dict(os.environ))
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
        return 0 if outcome["all_selected_domains_readable"] else 2
    except Exception:
        print(json.dumps({"status": "CENSUS_READONLY_BLOCKED",
                          "global_corporate_crm_visibility_proven": False,
                          "formal_score_authorized": False,
                          "crm_writes": 0, "outreach": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
