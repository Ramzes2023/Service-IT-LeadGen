"""One-shot, operator-approved common ServiceIT detail form layout.

Unlike Kanban tile configuration this is the officially documented REST
crm.item.details.configuration.get/set (scope C, category 21). A full
existing non-null saved baseline is required because set REPLACES it.
Never modifies actual CRM item content or touches historical one-shot ledgers.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path

from .bitrix_1062_ai_cockpit import validate_layout_sections
from .serviceit_enrichment_worker import FIELDS, PREFIX
from .serviceit_manager_ai_card import manager_layout_manifest

SCOPE = {"entityTypeId": 1062, "scope": "C", "extras": {"categoryId": 21}}
TITLES = (
    ("serviceit_ai_decision_v2", "Сервис ИТ | AI и приоритет", (
        "AI_SCORE_BADGE", "LEAD_SCORE", "SCORE_VERSION", "AI_STATUS",
        "AI_SUMMARY", "AI_NEXT_ACTION")),
    ("serviceit_ai_evidence_v2", "Сервис ИТ | Доказательства", (
        "AI_QUOTE", "AI_LIMITATIONS")),
    ("serviceit_ai_manager_v2", "Сервис ИТ | План менеджера", (
        "AI_HYPOTHESIS", "AI_RECOMMENDATION", "AI_MANAGER_BRIEF")),
    ("serviceit_ai_full_v2", "Сервис ИТ | Полный AI-отчёт", (
        "AI_FULL_REPORT",)),
)


class LayoutBlocked(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise LayoutBlocked(reason)


def common_layout_plan(previous, available):
    validate_layout_sections(previous)
    require(isinstance(available, dict), "SCHEMA_MISSING")
    wanted = [PREFIX + code for _, _, codes in TITLES for code in codes]
    require(len(wanted) == 12 and len(set(wanted)) == 12
            and set(wanted) == FIELDS and set(wanted).issubset(available),
            "TWELVE_FIELD_CONTRACT_MISSING")
    require(len(previous) <= 100, "TOO_MANY_EXISTING_SECTIONS")
    planned = deepcopy(previous)
    reserved = {name: title for name, title, _ in TITLES}
    for section in planned:
        if section["name"] in reserved:
            require(section.get("title") == reserved[section["name"]],
                    "LAYOUT_SECTION_OWNERSHIP_CONFLICT")
    current_elements = {}
    for section in planned:
        kept = []
        for element in section["elements"]:
            require(isinstance(element, dict) and isinstance(element.get("name"), str),
                    "EXISTING_ELEMENT_UNTRUSTED")
            name = element["name"]
            if name in wanted:
                require(name not in current_elements, "DUPLICATE_AI_FIELD")
                current_elements[name] = element
            else:
                kept.append(element)
        section["elements"] = kept
    # Remove only OUR known v2 sections; retain all unrelated sections/fields.
    planned = [s for s in planned if s["name"] not in reserved]
    added = []
    for name, title, codes in TITLES:
        added.append({
            "name": name,
            "title": title,
            "type": "section",
            "elements": [current_elements.get(PREFIX + code,
                        {"name": PREFIX + code, "optionFlags": 1}) for code in codes],
        })
    result = added + planned
    validate_layout_sections(result)
    require(len([el["name"] for sec in result for el in sec["elements"]])
            == len(set(el["name"] for sec in result for el in sec["elements"])),
            "POST_LAYOUT_HAS_DUPLICATE_FIELDS")
    return result


def apply_common_once(rest, fence_dir: Path, *, authorized=False):
    """One-shot, exact-before-image guarded common layout write and readback."""
    require(authorized is True and callable(rest), "ADMIN_LAYOUT_AUTHORIZATION_REQUIRED")
    require(isinstance(fence_dir, Path) and fence_dir.is_absolute()
            and fence_dir.is_dir(), "PRIVATE_FENCE_DIRECTORY_REQUIRED")
    saved = rest("crm.item.details.configuration.get", dict(SCOPE))
    # Null means no saved common baseline; cannot reconstruct default safely.
    require(saved is not None, "NO_SAVED_COMMON_LAYOUT_SAFE_STOP")
    fields = rest("crm.item.fields",
                  {"entityTypeId": 1062, "useOriginalUfNames": "Y"})["fields"]
    desired = common_layout_plan(saved, fields)
    if saved == desired:
        return {"status": "COMMON_DETAIL_LAYOUT_ALREADY_CONFIGURED",
                "sections": len(desired), "crm_item_writes": 0}
    backup = fence_dir / "common-detail-1062-21-before.json"
    record = fence_dir / "common-detail-1062-21-write-fence.json"
    require(not backup.exists() and not record.exists(),
            "PREVIOUS_LAYOUT_OPERATION_FENCED_NO_RETRY")
    with backup.open("x", encoding="utf-8") as output:
        json.dump({"scope": SCOPE, "before": saved, "proposed": desired},
                  output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    require(rest("crm.item.details.configuration.get", dict(SCOPE)) == saved,
            "COMMON_LAYOUT_CONCURRENT_CHANGE")
    digest = sha256(json.dumps(desired, ensure_ascii=False,
                               sort_keys=True).encode()).hexdigest()
    with record.open("x", encoding="utf-8") as output:
        json.dump({"digest": digest, "state": "POST_UNCERTAIN"},
                  output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    # No retry even if the transport times out or readback is ambiguous.
    rest("crm.item.details.configuration.set", {**SCOPE, "data": desired})
    require(rest("crm.item.details.configuration.get", dict(SCOPE)) == desired,
            "COMMON_DETAIL_READBACK_UNCERTAIN_NO_RETRY")
    # Best effort only after exact readback; failure is not a reason to replay.
    with record.open("w", encoding="utf-8") as output:
        json.dump({"digest": digest, "state": "EXACT_READBACK"}, output)
    return {"status": "COMMON_DETAIL_LAYOUT_APPLIED_EXACT_READBACK",
            "sections": len(desired), "ai_fields": 9, "score_fields": 3,
            "kanban_tile_changed": False, "crm_item_writes": 0}


def main(argv=None):
    import argparse
    import httpx
    from .bitrix_1062_ai_cockpit import Rest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-common", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply_common or os.environ.get("SERVICEIT_COMMON_LAYOUT_APPROVED") != "YES_1062_21":
        print(json.dumps({"status": "LAYOUT_PREVIEW_ONLY", "common_saved": False,
                          "crm_item_writes": 0}))
        return 2
    secret = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    fence = Path(os.environ.get("SERVICEIT_AUTOFILL_STATE_DIR", "")) / "layout-fence"
    if not secret or not fence.parent.is_absolute():
        print(json.dumps({"status": "LAYOUT_CONFIGURATION_BLOCKED",
                          "crm_item_writes": 0}))
        return 2
    fence.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
            output = apply_common_once(Rest(secret, client), fence, authorized=True)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"status": "LAYOUT_BLOCKED_OR_UNCERTAIN_NO_RETRY",
                          "crm_item_writes": 0, "secrets_exposed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
