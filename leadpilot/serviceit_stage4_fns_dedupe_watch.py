"""Automated READ-ONLY FNS/CRM duplicate prequalification of ALL new 1062/21 cards.

This is an active evidence collector for the future signed Lead Score writer.
Never writes a formal score or asserts global CRM clearance. STOP-list is
explicitly deferred: its absence cannot authorize outreach or VERIFIED status.
Never log company names, CRM IDs, INNs, webhook, full source URLs or raw records.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, discover, date_value,
)
from .serviceit_enrichment_worker import normalized, validate_identity, PREFIX
from .serviceit_qualification_proof_v2 import crm_inn_visibility_readonly
from .serviceit_stage4_fns_msp_snapshot import verify as verify_msp_snapshot
from .serviceit_three_signal_triage import classify_signal

PORTAL = "https://serviseit.bitrix24.ru/rest/"
MAX_CARDS_PER_RUN = 6
COMPANY_FIELD = PREFIX + "COMPANY_INN"
NO_WRITES = {"crm_writes": 0, "score_writes": 0, "tasks_created": 0,
             "global_crm_clear": False, "stop_list_status": "DEFERRED_NOT_CONFIGURED"}


class WatchBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise WatchBlocked(code)


def read_item(client, base, card):
    need(type(card) is int and 8 <= card <= 2_000_000_000
         and card not in (3, 7), "CARD_OUT_OF_SCOPE")
    response = client.post(base + "/crm.item.get.json", json={
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    }, follow_redirects=False, timeout=22)
    need(response.status_code == 200 and 0 < len(response.content) <= 400000,
         "CARD_HTTP_UNVERIFIED")
    body = response.json()
    need(type(body) is dict and "error" not in body
         and type(body.get("result")) is dict
         and type(body["result"].get("item")) is dict,
         "CARD_RESPONSE_INVALID")
    item = normalized(body["result"]["item"])
    validate_identity(item, card)
    return item


def same_process_visible_duplicates(client, base, inn, card):
    """Exhaust bounded visible 1062/21 exact-INN pages without changing cards.

    A found collision is a HOLD. No collision means only 'not seen by this
    webhook': no global CRM clearance, including hidden employees' records.
    """
    need(type(inn) is str and _is_valid_inn(inn), "DUPLICATE_INN_INVALID")
    seen, start, total_expected = set(), 0, None
    for _ in range(20):
        params = {
            "entityTypeId": 1062, "useOriginalUfNames": "Y",
            "filter": {"categoryId": 21, COMPANY_FIELD: inn},
            "select": ["id", "categoryId", COMPANY_FIELD], "start": start,
        }
        response = client.post(base + "/crm.item.list.json", json=params,
                               timeout=22, follow_redirects=False)
        need(response.status_code == 200
             and 0 < len(response.content) <= 350000,
             "PROCESS_DUPLICATE_HTTP_UNKNOWN")
        body = response.json()
        need(type(body) is dict and "error" not in body
             and type(body.get("result")) is dict,
             "PROCESS_DUPLICATE_RESULT_INVALID")
        result = body["result"]
        rows = result.get("items")
        need(type(rows) is list and len(rows) <= 50,
             "PROCESS_DUPLICATE_PAGE_INVALID")
        # Bitrix can return top-level or nested pagination. Contradictions
        # are an access/integrity uncertainty, not an empty match set.
        for key in ("next", "total"):
            need(key not in body or key not in result
                 or body[key] == result[key],
                 "PROCESS_DUPLICATE_PAGINATION_CONFLICT")
        following = body.get("next", result.get("next"))
        total = body.get("total", result.get("total"))
        need(total is None or type(total) is int and total >= 0,
             "PROCESS_DUPLICATE_TOTAL_INVALID")
        if total is not None:
            if total_expected is None:
                total_expected = total
            need(total_expected == total, "PROCESS_DUPLICATE_TOTAL_CHANGED")
        for raw in rows:
            need(type(raw) is dict, "PROCESS_DUPLICATE_ITEM_INVALID")
            row = normalized(raw)
            item_id = row.get("id")
            need((type(item_id) is int or type(item_id) is str
                  and item_id.isascii() and item_id.isdecimal())
                 and str(row.get("categoryId")) == "21"
                 and row.get(COMPANY_FIELD) == inn,
                 "PROCESS_DUPLICATE_FILTER_NOT_ENFORCED")
            identifier = int(item_id)
            need(identifier not in seen and identifier > 0,
                 "PROCESS_DUPLICATE_DUPLICATED_ROW")
            seen.add(identifier)
        if following is None:
            need(total_expected is None or total_expected == len(seen),
                 "PROCESS_DUPLICATE_TOTAL_UNVERIFIED")
            return {
                "process_visible_duplicate": any(i != card for i in seen),
                "process_visible_matches": len(seen),
                "visible_pages_complete": True,
                "global_crm_clear": False,
            }
        need(type(following) is int and following == start + 50
             and len(rows) == 50,
             "PROCESS_DUPLICATE_PAGINATION_UNKNOWN")
        start = following
    raise WatchBlocked("PROCESS_DUPLICATE_TOO_MANY_PAGES")


def msp_archive_status(env, inn, *, today):
    """Optional verified-digest FNS open-data snapshot, NEVER EGRUL status.

    The downloaded archive and its independently verified digest must be made
    available by a separately authorized corporate FNS data ingestion process.
    An absent archive is UNKNOWN, not a negative business-registry result.
    """
    keys = ("SERVICEIT_FNS_MSP_ARCHIVE_PATH",
            "SERVICEIT_FNS_MSP_PUBLIC_URL",
            "SERVICEIT_FNS_MSP_SHA256")
    values = [env.get(k) for k in keys]
    if not any(values):
        return "MSP_OFFICIAL_SNAPSHOT_NOT_CONNECTED"
    need(all(type(v) is str and v for v in values),
         "FNS_SNAPSHOT_CONFIG_PARTIAL")
    result = verify_msp_snapshot(
        Path(values[0]), inn, values[1], values[2], today=today)
    need(result.get("origin_independently_verified") is False
         and result.get("formal_score_authorized") is False,
         "FNS_SNAPSHOT_ASSURANCE_MISMATCH")
    return result["status"]


def assess_card(client, base, card, env, *, now):
    """One real new card; no contact/score/FNS identity details are emitted."""
    row = read_item(client, base, card)
    created = date_value(row.get("createdTime"))
    need(created <= now and created >= after_activation(env, now),
         "CARD_NOT_NEW_ENOUGH")
    inn = row.get(COMPANY_FIELD)
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_MISSING_VALID_COMPANY_INN"
    process = same_process_visible_duplicates(client, base, inn, card)
    requisites = crm_inn_visibility_readonly(client, env["SERVICEIT_BITRIX_WEBHOOK"], inn)
    need(requisites.get("global_crm_customer_clear") is False
         and requisites.get("global_dedup_verified") is False,
         "GLOBAL_CLEARANCE_FABRICATED")
    if process["process_visible_duplicate"] or requisites["visible_matches"] > 0:
        return "HOLD_POTENTIAL_VISIBLE_CRM_DUPLICATE"

    # This provisional classification never implies the tender is current.
    kind = row.get(PREFIX + "SIGNAL_KIND") or ""
    source = row.get(PREFIX + "SIGNAL_SOURCE") or ""
    title = row.get("title") or ""
    region = row.get(PREFIX + "REGION") or ""
    need(all(type(v) is str for v in (kind, source, title, region)),
         "SIGNAL_CLASSIFIER_INPUT_UNVERIFIED")
    triage = classify_signal({"title": title, "description": "",
                              "signal_kind": kind, "source": source,
                              "region": region, "company": ""},
                             now=now)
    need(type(triage.get("indicative_priority")) is int
         and 0 <= triage["indicative_priority"] <= 100
         and triage.get("formal_lead_score") is None,
         "PRELIMINARY_SCORE_SAFETY_INVALID")

    fns = msp_archive_status(env, inn, today=now.date())
    if fns == "MSP_OFFICIAL_SNAPSHOT_NOT_CONNECTED":
        return "HOLD_FNS_SNAPSHOT_NOT_CONNECTED"
    if fns == "NO_MATCH_IN_SUPPLIED_SNAPSHOT":
        return "HOLD_FNS_MSP_NOT_MATCHED"
    need(fns == "MATCH_IN_SUPPLIED_MSP_SNAPSHOT", "FNS_RESULT_UNVERIFIED")
    # MSP membership alone does not prove EGRUL active, legal/contact rights,
    # full CRM visibility or that a STOP list is clean.
    return "DRAFT_ONLY_FNS_MSP_MATCH_GLOBAL_CHECKS_PENDING"


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(isinstance(client, httpx.Client)
         and env.get("GITHUB_REPOSITORY") == "Ramzes2023/Service-IT-LeadGen"
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") in ("schedule", "issue_comment"),
         "GITHUB_CONTEXT_NOT_APPROVED")
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(PORTAL), "CORPORATE_PORTAL_NOT_VERIFIED")
    lower = after_activation(env, now)
    scoped_list = lambda method, params: list_only(client, base, method, params)
    calibrate_known_card(scoped_list)
    cards = discover(scoped_list, lower, now)
    need(len(cards) <= 200, "DISCOVERY_LIMIT_EXCEEDED")
    # Fair rotation over all discovered cards; no permanent first-page starvation.
    windows = max(1, (len(cards) + MAX_CARDS_PER_RUN - 1) // MAX_CARDS_PER_RUN)
    slot = (int(now.timestamp()) // 1200) % windows
    selected = cards[slot * MAX_CARDS_PER_RUN:(slot + 1) * MAX_CARDS_PER_RUN]
    outcomes = Counter()
    for card in selected:
        try:
            result = assess_card(client, base, card, env, now=now)
        except Exception:
            # No raw company data or INNs are allowed in diagnostics.
            result = "HOLD_EVIDENCE_OR_ACCESS_UNVERIFIED"
        need(result in {
            "HOLD_MISSING_VALID_COMPANY_INN",
            "HOLD_POTENTIAL_VISIBLE_CRM_DUPLICATE",
            "HOLD_FNS_SNAPSHOT_NOT_CONNECTED",
            "HOLD_FNS_MSP_NOT_MATCHED",
            "DRAFT_ONLY_FNS_MSP_MATCH_GLOBAL_CHECKS_PENDING",
            "HOLD_EVIDENCE_OR_ACCESS_UNVERIFIED",
        }, "WATCH_RESULT_UNTRUSTED")
        outcomes[result] += 1
    return {
        "status": "SERVICEIT_FNS_DUPLICATES_AUTO_READONLY",
        "eligible_new_cards": len(cards),
        "checked_this_run": sum(outcomes.values()),
        "remaining": max(0, len(cards) - sum(outcomes.values())),
        "outcomes": dict(outcomes),
        "fns_egrul_active_verified": False,
        "global_crm_customer_clear": False,
        "formal_score_authorized": False,
        **NO_WRITES,
    }


def main():
    env = dict(os.environ)
    env.pop("GITHUB_TOKEN", None)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            outcome = run(client, env)
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except Exception:
        print(json.dumps({
            "status": "SERVICEIT_FNS_DUPLICATES_READONLY_BLOCKED",
            "formal_score_authorized": False, **NO_WRITES,
        }, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
