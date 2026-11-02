"""Demo-only colored Kanban score label. No auto-outreach, no GigaChat calls.

Offline default. Live mode is read-only until an explicit one-shot ApplyDemo.
Only exact corporate smart process 1062 / category 21 / item 3.
No public API exists for configuring *which* fields appear on Kanban cards;
this module never pretends to perform that portal UI operation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
import os

import httpx

from .bitrix_1062_ai_cockpit import (
    CockpitBlocked, Rest, State, demo_assessment, inspect_schema, require,
    ITEM_SCOPE, PREFIX,
)
from .bitrix_1062_mapping import FIELD_MAP

CODE = PREFIX + "AI_SCORE_BADGE"
LABEL = "AI — цветной приоритет"
BASE = "ServiceIT Kanban badge v1"
VERSION = "mvp-2026-10-v1"

# Only three presentation bands, NOT contact authorization.
def score_badge(score: int) -> str:
    require(type(score) is int and 0 <= score <= 100, "INVALID_SCORE")
    if score >= 80:
        return f"🟢 {score}/100 — высокий приоритет проверки"
    if score >= 60:
        return f"🟡 {score}/100 — средний приоритет проверки"
    return f"🔴 {score}/100 — низкий приоритет проверки"


def field_detail_ok(field):
    require(isinstance(field, dict) and field.get("entityId") == "CRM_13"
            and field.get("fieldName") == CODE
            and field.get("userTypeId") == "string"
            and field.get("multiple") in ("N", False),
            "BADGE_FIELD_SCHEMA_MISMATCH")
    settings = field.get("settings", {})
    require(isinstance(settings, dict), "BADGE_FIELD_SETTINGS_INVALID")
    try:
        maximum = int(settings.get("MAX_LENGTH", 0) or 0)
    except (TypeError, ValueError):
        raise CockpitBlocked("BADGE_FIELD_SETTINGS_INVALID") from None
    require(maximum == 0 or maximum >= 80, "BADGE_FIELD_TOO_SHORT")


def inspect(rest):
    original = inspect_schema(rest)  # typeId=13, original fields, automation off
    require(not original["missing"], "ORIGINAL_AI_FIELDS_MISSING")
    item = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
    assessment = demo_assessment(item, date.today())
    score = assessment["lead_score"]
    require(score == 90 and str(item.get(FIELD_MAP["lead_score"])) == "90"
            and item.get(FIELD_MAP["score_version"]) == VERSION,
            "DEMO_SCORE_VERSION_DRIFT")
    require(isinstance(item.get(PREFIX + "AI_FULL_REPORT"), str)
            and len(item[PREFIX + "AI_FULL_REPORT"]) >= 100,
            "DEMO_REPORT_NOT_PRESENT")
    result = rest("userfieldconfig.list",
                  {"moduleId": "crm", "select": ["*"], "filter": {"entityId": "CRM_13"}})
    require(isinstance(result, dict) and isinstance(result.get("fields"), list)
            and not result.get("next"), "BADGE_FIELD_LIST_AMBIGUOUS")
    matches = [f for f in result["fields"] if f.get("fieldName") == CODE]
    require(len(matches) <= 1, "BADGE_FIELD_DUPLICATE")
    existing = None
    if matches:
        detail = rest("userfieldconfig.get", {"moduleId": "crm", "id": matches[0]["id"]})
        require(isinstance(detail, dict), "BADGE_FIELD_GET_INVALID")
        field_detail_ok(detail.get("field"))
        existing = detail["field"]
    current = item.get(CODE)
    expected = score_badge(score)
    require(current in (None, "", expected), "BADGE_EXISTING_VALUE_CONFLICT")
    return {"existing": existing, "item": item, "expected": expected, "current": current,
            "field_needed": existing is None, "item_write_needed": current != expected,
            "score": score}


def provision_badge(rest, state):
    view = inspect(rest)
    if not view["field_needed"]:
        return view
    require(state is not None, "STATE_REQUIRED")
    payload = {"moduleId": "crm", "field": {"entityId": "CRM_13",
               "fieldName": CODE, "userTypeId": "string",
               "multiple": "N", "mandatory": "N",
               "editFormLabel": {"ru": LABEL},
               "settings": {"ROWS": 1, "SIZE": 48, "MAX_LENGTH": 120}}}
    state.backup("badge-schema-before", {"name": CODE, "present": False})
    state.reserve("badge:1062:13:field:v1", payload)
    added = rest("userfieldconfig.add", payload)
    require(isinstance(added, dict), "BADGE_ADD_UNCERTAIN")
    field_detail_ok(added.get("field"))
    fresh = inspect(rest)
    require(not fresh["field_needed"], "BADGE_FIELD_READBACK_UNCERTAIN")
    state.verified("badge:1062:13:field:v1")
    return fresh


def apply_demo_badge(rest, *, state):
    require(state is not None, "STATE_REQUIRED")
    view = provision_badge(rest, state)
    if not view["item_write_needed"]:
        return {"status": "KANBAN_BADGE_ALREADY_PRESENT", "score": view["score"],
                "expected": view["expected"], "card_3_only": True,
                "kanban_display_configured": False}
    # CRM write is bounded to one NEW field, never the old score/title/AI report.
    require(view["existing"] is not None, "BADGE_FIELD_MISSING")
    fresh = inspect(rest)
    require(fresh["item"] == view["item"] and fresh["existing"] == view["existing"],
            "BADGE_CONCURRENT_CHANGE")
    payload = {**deepcopy(ITEM_SCOPE), "fields": {CODE: view["expected"]}}
    state.backup("badge-item3-before",
                 {"itemId": 3, "field": CODE, "oldValue": view["current"],
                  "newValue": view["expected"]})
    state.reserve("badge:1062:21:3:v1", payload)
    result = rest("crm.item.update", payload)
    require(isinstance(result, dict), "BADGE_WRITE_UNCERTAIN")
    readback = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
    require(isinstance(readback, dict) and readback.get(CODE) == view["expected"],
            "BADGE_READBACK_UNCERTAIN")
    require(all(readback.get(k) == v for k, v in view["item"].items()
                if k != CODE and k not in {"updatedTime", "updatedBy", "movedTime"}),
            "BADGE_PROTECTED_FIELD_CHANGED")
    state.verified("badge:1062:21:3:v1")
    return {"status": "KANBAN_BADGE_DEMO_VERIFIED", "score": view["score"],
            "expected": view["expected"], "card_3_only": True,
            "kanban_display_configured": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Safe ServiceIT score badge")
    parser.add_argument("--inspect-live", action="store_true")
    parser.add_argument("--apply-demo", action="store_true")
    parser.add_argument("--confirm-demo-only", action="store_true")
    parser.add_argument("--webhook-from-env", action="store_true")
    parser.add_argument("--state-dir", default="")
    args = parser.parse_args(argv)
    if not args.inspect_live:
        require(not args.apply_demo and not args.webhook_from_env,
                "LIVE_FLAGS_WITHOUT_INSPECT")
        print(json.dumps({"status": "KANBAN_BADGE_OFFLINE",
                          "examples": [score_badge(90), score_badge(65), score_badge(35)],
                          "crm_writes": 0}, ensure_ascii=False))
        return 0
    # Exactly the protected PowerShell SecureString mechanism (no Python getpass).
    try:
        require(args.webhook_from_env, "POWERSHELL_SECURE_WEBHOOK_REQUIRED")
        hook = os.environ.pop("SERVICEIT_AI_COCKPIT_WEBHOOK", "")
        require(bool(hook), "SECURE_WEBHOOK_MISSING")
        if args.apply_demo:
            require(args.confirm_demo_only and bool(args.state_dir),
                    "EXPLICIT_DEMO_BADGE_APPROVAL_REQUIRED")
        with httpx.Client() as http:
            rest = Rest(hook, http)
            view = inspect(rest)
            if args.apply_demo:
                outcome = apply_demo_badge(rest, state=State(args.state_dir))
            else:
                outcome = {"status": "KANBAN_BADGE_READONLY_PREVIEW",
                           "score": view["score"], "expected": view["expected"],
                           "field_needed": view["field_needed"],
                           "item_write_needed": view["item_write_needed"],
                           "crm_writes": 0, "kanban_display_configured": False}
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    except CockpitBlocked as exc:
        # Only stable, curated internal reason codes; never print remote bodies.
        codes = frozenset({
            "BADGE_FIELD_SCHEMA_MISMATCH","BADGE_FIELD_SETTINGS_INVALID",
            "BADGE_FIELD_TOO_SHORT","BADGE_FIELD_LIST_AMBIGUOUS","BADGE_FIELD_DUPLICATE",
            "BADGE_FIELD_GET_INVALID","BADGE_EXISTING_VALUE_CONFLICT",
            "DEMO_SCORE_VERSION_DRIFT","DEMO_REPORT_NOT_PRESENT",
            "ORIGINAL_AI_FIELDS_MISSING","BADGE_CONCURRENT_CHANGE",
            "BADGE_FIELD_MISSING","BADGE_ADD_UNCERTAIN","BADGE_FIELD_READBACK_UNCERTAIN",
            "BADGE_WRITE_UNCERTAIN","BADGE_READBACK_UNCERTAIN",
            "BADGE_PROTECTED_FIELD_CHANGED","ONE_SHOT_ALREADY_RESERVED_NO_RETRY",
            "EXPLICIT_DEMO_BADGE_APPROVAL_REQUIRED","SECURE_WEBHOOK_MISSING",
            "POWERSHELL_SECURE_WEBHOOK_REQUIRED",
        })
        reason = str(exc) if str(exc) in codes else "CRM_METADATA_OR_TRANSPORT_BLOCKED"
        print(json.dumps({"status": "BADGE_BLOCKED_OR_UNCERTAIN",
                          "safe_reason_code": reason, "blind_retry": "FORBIDDEN",
                          "secrets_in_output": False}))
        return 2
    except (ValueError, TypeError, KeyError, httpx.HTTPError):
        print(json.dumps({"status": "BADGE_BLOCKED_OR_UNCERTAIN",
                          "safe_reason_code": "UNEXPECTED_SHAPE_OR_TRANSPORT",
                          "blind_retry": "FORBIDDEN", "secrets_in_output": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
