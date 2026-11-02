"""Read-only incident diagnosis for the one-shot Bitrix24 Kanban badge write.

NO crm.item.update, userfieldconfig.add, ledger reservations or GigaChat calls.
Do not normalize or rewrite an ambiguous CRM value. Never expose CRM contents.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3

import httpx

from .bitrix_1062_ai_cockpit import Rest, CockpitBlocked
from .serviceit_kanban_score_badge import CODE, VERSION, score_badge
from .bitrix_1062_mapping import FIELD_MAP
from .bitrix_1062_ai_cockpit import PREFIX

SCOPE_Y = {"entityTypeId": 1062, "id": 3, "useOriginalUfNames": "Y"}
SCOPE_N = {"entityTypeId": 1062, "id": 3, "useOriginalUfNames": "N"}
LEDGER_KEYS = ("badge:1062:13:field:v1", "badge:1062:21:3:v1",
               "badge:1062:21:3:reconciliation-v1")
FINAL_STATES = frozenset(("VERIFIED", "UNCERTAIN", "NOT_RESERVED"))
TARGET_NORMALIZED = re.sub(r"[^a-z0-9]", "", CODE.lower())


def describe_badge_value(item):
    if not isinstance(item, dict):
        raise CockpitBlocked("ITEM_SHAPE_INVALID")
    keys = [key for key in item
            if isinstance(key, str) and
            re.sub(r"[^a-z0-9]", "", key.lower()) == TARGET_NORMALIZED]
    if len(keys) > 1:
        return {"state": "MULTIPLE_ALIASES", "matched_keys": len(keys)}
    if not keys:
        return {"state": "KEY_ABSENT", "matched_keys": 0}
    val = item[keys[0]]
    if val is None:
        return {"state": "NULL", "matched_keys": 1}
    if not isinstance(val, str):
        return {"state": "NONSTRING", "type": type(val).__name__, "matched_keys": 1}
    expected = score_badge(90)
    return {
        "state": "EMPTY" if val == "" else "STRING",
        "matched_keys": 1,
        "length": len(val),
        "exact_expected": val == expected,
        "equals_after_outer_whitespace_trim": val.strip() == expected,
        "has_90_out_of_100": "90/100" in val,
        "has_green_emoji": "🟢" in val,
        "digest_12": sha256(val.encode("utf-8")).hexdigest()[:12],
    }


def inspect_rest(rest):
    # Read only; do not call badge.inspect() because it rejects non-exact values.
    original = rest("crm.item.get", SCOPE_Y).get("item")
    alternate = rest("crm.item.get", SCOPE_N).get("item")
    if not isinstance(original, dict) or not isinstance(alternate, dict):
        raise CockpitBlocked("ITEM_SHAPE_INVALID")
    if not (str(original.get("id")) == "3"
            and str(original.get("entityTypeId")) == "1062"
            and str(original.get("categoryId")) == "21"
            and original.get("title") == "DEMO ServiceIT AI — DEMO-ITREQ-001"):
        raise CockpitBlocked("DEMO_IDENTITY_DRIFT")
    score = original.get(FIELD_MAP["lead_score"])
    if str(score) != "90" or original.get(FIELD_MAP["score_version"]) != VERSION:
        raise CockpitBlocked("DEMO_SCORE_DRIFT")
    summary = original.get(PREFIX + "AI_FULL_REPORT")
    if not isinstance(summary, str) or len(summary) < 100:
        raise CockpitBlocked("GIGACHAT_REPORT_ABSENT")
    list_result = rest("userfieldconfig.list", {
        "moduleId": "crm", "select": ["*"], "filter": {"entityId": "CRM_13"}
    })
    if not isinstance(list_result, dict) or not isinstance(list_result.get("fields"), list) or list_result.get("next"):
        raise CockpitBlocked("FIELD_LIST_AMBIGUOUS")
    matches = [x for x in list_result["fields"]
               if isinstance(x, dict) and x.get("fieldName") == CODE]
    if len(matches) > 1:
        raise CockpitBlocked("BADGE_FIELD_DUPLICATE")
    if matches:
        detail = rest("userfieldconfig.get", {"moduleId": "crm", "id": matches[0]["id"]})
        field = detail.get("field") if isinstance(detail, dict) else None
        field_valid = (isinstance(field, dict)
                       and field.get("entityId") == "CRM_13"
                       and field.get("fieldName") == CODE
                       and field.get("userTypeId") == "string"
                       and field.get("multiple") in ("N", False))
    else:
        field_valid = False
    metadata = {}
    for mode in ("Y", "N"):
        desc = rest("crm.item.fields", {"entityTypeId": 1062, "useOriginalUfNames": mode})
        fields = desc.get("fields") if isinstance(desc, dict) else None
        if not isinstance(fields, dict):
            raise CockpitBlocked("FIELDS_SHAPE_INVALID")
        candidates = [v for k,v in fields.items()
                      if isinstance(k, str) and re.sub(r"[^a-z0-9]", "", k.lower()) == TARGET_NORMALIZED]
        metadata[mode] = {
            "badge_declared_in_item_fields": len(candidates) == 1,
            "badge_ambiguous": len(candidates) > 1,
            "type": candidates[0].get("type") if len(candidates) == 1 and isinstance(candidates[0],dict) else None,
            "is_read_only": candidates[0].get("isReadOnly") if len(candidates) == 1 and isinstance(candidates[0],dict) else None,
        }
    return {
        "status": "BADGE_READONLY_DIAGNOSTIC",
        "demo_id_verified": True,
        "score_unchanged": True,
        "report_present": True,
        "badge_schema_exists": len(matches) == 1,
        "badge_schema_valid": bool(field_valid),
        "badge_item_fields": metadata,
        "readback_with_original_names": describe_badge_value(original),
        "readback_with_camelcase_names": describe_badge_value(alternate),
        "expected_indicator": score_badge(90),
        "crm_writes": 0,
        "new_reservations": 0,
        "safe_to_blind_retry_apply": False,
    }


def ledger_readonly(state_dir):
    if not state_dir:
        raise CockpitBlocked("STATE_DIR_REQUIRED")
    path = Path(state_dir) / "cockpit-report-v1.sqlite3"
    if not path.is_file():
        return {"state": "LEDGER_NOT_FOUND", "items": {}}
    # mode=ro cannot create or overwrite a SQLite database.
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            rows = db.execute(
                "SELECT key,state FROM cockpit_ops WHERE key IN (?,?,?)", LEDGER_KEYS
            ).fetchall()
    except sqlite3.Error:
        raise CockpitBlocked("LEDGER_READ_BLOCKED") from None
    statuses = {k: v for k,v in rows if k in LEDGER_KEYS}
    if any(v not in FINAL_STATES for v in statuses.values()):
        raise CockpitBlocked("LEDGER_STATE_UNEXPECTED")
    return {"state": "LEDGER_READ_ONLY",
            "items": {key: statuses.get(key,"NOT_RESERVED") for key in LEDGER_KEYS}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Bitrix24 score-badge recovery audit")
    parser.add_argument("--inspect-live", action="store_true")
    parser.add_argument("--webhook-from-env", action="store_true")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    # Inspect local ledger even if REST fails; does not create a ledger.
    try:
        ledger = ledger_readonly(args.state_dir)
        if not args.inspect_live:
            print(json.dumps({"status": "BADGE_LOCAL_LEDGER_AUDIT",
                              "ledger": ledger, "crm_writes": 0}, ensure_ascii=False))
            return 0
        if not args.webhook_from_env:
            raise CockpitBlocked("SECURE_WEBHOOK_FLAG_REQUIRED")
        hook = os.environ.pop("SERVICEIT_AI_COCKPIT_WEBHOOK", "")
        if not hook:
            raise CockpitBlocked("SECURE_WEBHOOK_MISSING")
        with httpx.Client() as client:
            result = inspect_rest(Rest(hook, client))
        result["ledger"] = ledger
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CockpitBlocked, KeyError, TypeError, ValueError, OSError):
        # Mask all data/content/path/remote exception strings.
        print(json.dumps({"status":"BADGE_DIAGNOSTIC_BLOCKED",
                          "crm_writes":0,"new_reservations":0,
                          "secrets_in_output":False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
