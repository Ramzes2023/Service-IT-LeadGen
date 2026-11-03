"""Non-destructive *plan* and guarded one-time Bitrix 1062/21 card layout setup.

Official Bitrix24 API crm.item.details.configuration.set REPLACES the ENTIRE
form layout (not a patch!). Never call it with only our two new sections.
Preserve every pre-existing section/field, fail closed on ambiguous settings,
durably reserve an encrypted backup BEFORE a write, and verify exact readback.

Default API: pure plan/dry-run. LIVE apply needs an externally injected
durable, remotely acknowledged reserve callback and administrator authorization.
No credentials, customer records or layout contents printed.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re

from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

ENTITY_TYPE_ID = 1062
CATEGORY_ID = 21
SCOPE = "C"  # shared configuration for managers; requires Bitrix admin.
EXTRAS = {"categoryId": CATEGORY_ID}
AI_SECTION_TITLE = "Сервис ИТ | AI-анализ"
MANAGER_SECTION_TITLE = "Сервис ИТ | Проверка менеджером"
AI_SECTION_NAME = "serviceit_ai_review_1062_21"
MANAGER_SECTION_NAME = "serviceit_manager_review_1062_21"
AI_REQUIRED = (
    PREFIX + "AI_STATUS", PREFIX + "AI_SUMMARY", PREFIX + "AI_QUOTE",
    PREFIX + "AI_HYPOTHESIS", PREFIX + "AI_LIMITATIONS",
    PREFIX + "AI_RECOMMENDATION", PREFIX + "AI_NEXT_ACTION",
    PREFIX + "AI_MANAGER_BRIEF", PREFIX + "AI_FULL_REPORT",
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD,
)
MANAGER_REQUIRED = (
    PREFIX + "MANAGER_NEXT_ACTION", PREFIX + "REVIEW_DECISION",
    PREFIX + "STOP_REASON",
)
assert set(AI_REQUIRED[:9]) == AI_FIELDS
assert set(AI_REQUIRED).isdisjoint(MANAGER_REQUIRED)


class ManagerLayoutBlocked(ValueError):
    pass


def ensure(ok, code):
    if not ok:
        raise ManagerLayoutBlocked(code)


def _digest(obj):
    return sha256(json.dumps(
        obj, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _check_sections(sections):
    """Never silently throw away fields or accept malformed source config."""
    ensure(type(sections) is list and 1 <= len(sections) <= 60,
           "EXISTING_FORM_LAYOUT_MISSING")
    ensure(len(json.dumps(sections, ensure_ascii=False, default=str)) < 250000,
           "EXISTING_FORM_LAYOUT_TOO_LARGE")
    section_names, field_names = set(), set()
    for section in sections:
        ensure(type(section) is dict
               and type(section.get("name")) is str
               and 1 <= len(section["name"]) <= 150
               and type(section.get("title")) is str
               and 1 <= len(section["title"]) <= 250
               and section.get("type", "section") == "section"
               and type(section.get("elements")) is list
               and len(section["elements"]) <= 100,
               "FORM_SECTION_SCHEMA_INVALID")
        ensure(section["name"] not in section_names, "FORM_DUPLICATE_SECTION")
        section_names.add(section["name"])
        for element in section["elements"]:
            ensure(type(element) is dict
                   and type(element.get("name")) is str
                   and re.fullmatch(r"[a-zA-Z0-9_]{1,150}", element["name"]),
                   "FORM_FIELD_SCHEMA_INVALID")
            # A field may appear only once in a CRM detail form; guessing
            # whether duplicates are intentional risks changing other tabs.
            ensure(element["name"] not in field_names, "FORM_DUPLICATE_FIELD")
            field_names.add(element["name"])
    return section_names, field_names


def _target_section(sections, title, suggested_name):
    for section in sections:
        if section["name"] == suggested_name or section["title"] == title:
            # Name must stay unchanged if an administrator already created it.
            return section
    used = {s["name"] for s in sections}
    ensure(suggested_name not in used, "TARGET_NAME_CONFLICT")
    new = {"name": suggested_name, "title": title, "type": "section",
           "elements": []}
    sections.append(new)
    return new


def plan_shared_manager_layout(current, available):
    """Get existing complete sections, append only missing official fields.

    Existing sections, elements, order, flags and custom options stay intact.
    Already-present fields are never duplicated or moved automatically.
    """
    ensure(type(available) is dict, "CRM_FIELDS_CATALOG_UNVERIFIED")
    wanted = set(AI_REQUIRED) | set(MANAGER_REQUIRED)
    ensure(wanted.issubset(available.keys()),
           "MANDATORY_CRM_FIELDS_NOT_FOUND")
    for code in wanted:
        ensure(type(available[code]) is dict, "CRM_FIELD_DEF_INVALID")
    _check_sections(current)
    after = deepcopy(current)
    original_names = {s["name"] for s in current}
    existing = {e["name"] for s in after for e in s["elements"]}
    added = []
    for title, suggested_name, required in (
        (AI_SECTION_TITLE, AI_SECTION_NAME, AI_REQUIRED),
        (MANAGER_SECTION_TITLE, MANAGER_SECTION_NAME, MANAGER_REQUIRED),
    ):
        missing = [x for x in required if x not in existing]
        # If all required fields already appear in the UI, no empty section.
        if not missing:
            continue
        section = _target_section(after, title, suggested_name)
        for field in missing:
            section["elements"].append({"name": field, "optionFlags": 1})
            added.append(field)
            existing.add(field)
    _check_sections(after)
    # Verify all original ordered sections and their original ordered fields
    # survive EXACTLY. No defaults, no hidden sections, no removed values.
    for old in current:
        preserved = next((x for x in after if x["name"] == old["name"]), None)
        ensure(preserved is not None, "EXISTING_SECTION_REMOVED")
        n = len(old["elements"])
        ensure(preserved["elements"][:n] == old["elements"],
               "EXISTING_FIELDS_CHANGED")
        ensure(all(preserved.get(k) == v for k, v in old.items()
                   if k != "elements"), "EXISTING_SECTION_MODIFIED")
    ensure({s["name"] for s in current} == original_names,
           "EXISTING_SECTION_IDENTITY_CHANGED")
    return {
        "before_hash": _digest(current),
        "after_hash": _digest(after),
        "before": deepcopy(current),
        "after": after,
        "added_fields": sorted(added),
        "added_count": len(added),
        "crm_writes": 0,
        "public_or_client_data_export": False,
    }


def guarded_apply_shared_layout(rest, reserve_backup, *, apply=False,
                                admin_approved=False):
    """One irrevocable attempt max, gated on encrypted durable backup.

    reserve_backup(before, after, hashes)->True must ACK durable remote
    storage before an external crm.item.details.configuration.set. A failed
    or ambiguous attempt can never be repeated blindly.
    """
    ensure(callable(rest), "LAYOUT_REST_NOT_AVAILABLE")
    params = {"entityTypeId": ENTITY_TYPE_ID, "scope": SCOPE,
              "extras": EXTRAS}
    current = rest("crm.item.details.configuration.get", params)
    available = rest("crm.item.fields", {
        "entityTypeId": ENTITY_TYPE_ID, "useOriginalUfNames": "Y",
    })
    ensure(type(available) is dict and type(available.get("fields")) is dict,
           "LAYOUT_CRM_FIELDS_RESPONSE_INVALID")
    plan = plan_shared_manager_layout(current, available["fields"])
    if not apply or plan["added_count"] == 0:
        return {
            "status": "PREVIEW_ONLY" if not apply else "ALREADY_PRESENT",
            "added_fields": plan["added_count"],
            "before_hash": plan["before_hash"],
            "after_hash": plan["after_hash"],
            "crm_writes": 0,
        }
    ensure(admin_approved is True and callable(reserve_backup),
           "SHARED_LAYOUT_WRITE_NOT_APPROVED")
    # Recheck after preview, immediately before creating a durable backup.
    before = rest("crm.item.details.configuration.get", params)
    ensure(_digest(before) == plan["before_hash"],
           "MANAGER_LAYOUT_CHANGED_BEFORE_WRITE")
    try:
        confirmed = reserve_backup(plan["before"], plan["after"], {
            "before_hash": plan["before_hash"],
            "after_hash": plan["after_hash"],
        })
    except Exception:
        raise ManagerLayoutBlocked("MANAGER_LAYOUT_BACKUP_NOT_ACKED") from None
    ensure(confirmed is True, "MANAGER_LAYOUT_BACKUP_NOT_ACKED")
    # No exceptions after this line justify a blind retry. Changes to the
    # server or reply timeouts are not proof that the write did not happen.
    try:
        set_result = rest("crm.item.details.configuration.set", {
            **params, "data": plan["after"],
        })
        ensure(set_result is True, "LAYOUT_POST_RESPONSE_UNCONFIRMED")
        readback = rest("crm.item.details.configuration.get", params)
        ensure(readback == plan["after"], "LAYOUT_POST_READBACK_MISMATCH")
    except Exception:
        raise ManagerLayoutBlocked("LAYOUT_WRITE_UNCERTAIN_NO_RETRY") from None
    return {
        "status": "MANAGER_LAYOUT_APPLIED_EXACT",
        "added_fields": plan["added_count"],
        "crm_writes": 1, "before_hash": plan["before_hash"],
        "after_hash": plan["after_hash"],
    }
