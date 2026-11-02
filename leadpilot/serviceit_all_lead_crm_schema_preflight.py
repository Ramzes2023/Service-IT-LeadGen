"""Corporate smart-process 1062 field readiness: strictly READ ONLY.

This is the missing safe gate before a future all-lead create-and-readback
adapter. It does NOT invoke GigaChat or inspect/add/update any CRM item.
Only type and field metadata are read, using the exact existing approved
corporate webhook. Results never expose raw field values or company records.
"""
from __future__ import annotations

import argparse
import json
import os
import re

import httpx

from .bitrix_1062_ai_cockpit import Rest, CockpitBlocked
from .bitrix_1062_mapping import FIELD_MAP
from .serviceit_kanban_presentation import BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, score_label
from .serviceit_source_batch_pipeline import plan_source_batch

FIELDS = (SCORE_FIELD, VERSION_FIELD, BADGE_FIELD)
NORMALIZED = {re.sub("[^a-z0-9]", "", code.lower()): code for code in FIELDS}
MAX_BADGE_LENGTH = len(score_label(100))
SAFE_TYPE = {"string", "integer", "double", "crm_userfield"}
ALLOWED_METHODS = frozenset({
    "crm.type.getByEntityTypeId", "crm.item.fields",
    "userfieldconfig.list", "userfieldconfig.get",
})


class SchemaPreflightBlocked(ValueError):
    pass


def _require(ok: bool, code: str):
    if not ok:
        raise SchemaPreflightBlocked(code)


def _normalize(name: str) -> str:
    return re.sub("[^a-z0-9]", "", name.lower())


def _read(rest, method: str, params: dict) -> dict:
    _require(method in ALLOWED_METHODS, "SCHEMA_METHOD_FORBIDDEN")
    data = rest(method, params)
    _require(isinstance(data, dict), "SCHEMA_RESPONSE_INVALID")
    return data


def _field_mode(rest, mode):
    _require(mode in ("Y", "N"), "SCHEMA_MODE_INVALID")
    result = _read(rest, "crm.item.fields",
                   {"entityTypeId": 1062, "useOriginalUfNames": mode})
    fields = result.get("fields")
    _require(isinstance(fields, dict), "SCHEMA_FIELDS_INVALID")
    found = {}
    for key, detail in fields.items():
        if isinstance(key,str) and _normalize(key) in NORMALIZED:
            _require(isinstance(detail,dict), "SCHEMA_FIELD_DESCRIPTION_INVALID")
            orig = NORMALIZED[_normalize(key)]
            _require(orig not in found, "SCHEMA_FIELD_ALIAS_AMBIGUOUS")
            found[orig] = {
                "api_key": key,
                "type": detail.get("type"),
                "is_read_only": detail.get("isReadOnly"),
                "is_immutable": detail.get("isImmutable"),
                "is_multiple": detail.get("isMultiple"),
                "is_required": detail.get("isRequired"),
                "is_dynamic": detail.get("isDynamic"),
                "upper_name_matches": (detail.get("upperName") in (None,orig)),
            }
    _require(set(found) == set(FIELDS), "SCHEMA_REQUIRED_FIELD_MISSING")
    for code, info in found.items():
        _require(info["type"] in SAFE_TYPE
                 and info["is_read_only"] is False
                 and info["is_immutable"] in (None,False)
                 and info["is_multiple"] in (None,False)
                 and info["upper_name_matches"],
                 "SCHEMA_FIELD_NOT_WRITABLE_OR_ALIAS_INVALID")
    return found


def inspect_all_lead_schema(rest):
    _require(callable(rest), "SCHEMA_TRANSPORT_INVALID")
    entity = _read(rest, "crm.type.getByEntityTypeId", {"entityTypeId":1062})
    typ=entity.get("type")
    _require(isinstance(typ,dict) and str(typ.get("id"))=="13"
             and str(typ.get("entityTypeId"))=="1062",
             "SMART_PROCESS_SCOPE_INVALID")
    _require(typ.get("isAutomationEnabled") in (False,"N")
             and typ.get("isBizProcEnabled") in (False,"N"),
             "SMART_PROCESS_AUTOMATION_NOT_CONFIRMED_OFF")
    modes={x:_field_mode(rest,x) for x in ("Y","N")}
    listing=_read(rest,"userfieldconfig.list",
                  {"moduleId":"crm", "select":["*"],"filter":{"entityId":"CRM_13"}})
    field_list=listing.get("fields")
    _require(isinstance(field_list,list) and "next" not in listing,
             "SCHEMA_USERFIELDS_PAGINATED_OR_INVALID")
    matching=[f for f in field_list if isinstance(f,dict)
              and f.get("fieldName") in FIELDS]
    _require(len(matching)==3
             and {f["fieldName"] for f in matching} == set(FIELDS),
             "SCHEMA_CONFIG_MISSING_OR_DUPLICATE")
    settings={}
    for entry in matching:
        code=entry["fieldName"]
        detail=_read(rest,"userfieldconfig.get",
                     {"moduleId":"crm","id":entry["id"]}).get("field")
        _require(isinstance(detail,dict) and detail.get("fieldName")==code
                 and detail.get("entityId")=="CRM_13"
                 and detail.get("multiple") in ("N",False),
                 "SCHEMA_USERFIELD_CONFLICT")
        raw=detail.get("settings") or {}
        _require(isinstance(raw,dict), "SCHEMA_SETTINGS_INVALID")
        try:
            maxlength=int(raw.get("MAX_LENGTH",0) or 0)
            minlength=int(raw.get("MIN_LENGTH",0) or 0)
        except (TypeError,ValueError):
            raise SchemaPreflightBlocked("SCHEMA_LENGTH_INVALID") from None
        regexp=raw.get("REGEXP")
        _require(type(maxlength) is int and maxlength >= 0
                 and type(minlength) is int and minlength >= 0,
                 "SCHEMA_LENGTH_INVALID")
        settings[code] = {
            "user_field_type":detail.get("userTypeId"),
            "max_length":maxlength,
            "min_length":minlength,
            "regexp_present":bool(regexp),
            "field_mandatory":detail.get("mandatory"),
        }
    _require(settings[BADGE_FIELD]["user_field_type"]=="string"
             and settings[VERSION_FIELD]["user_field_type"]=="string",
             "SCHEMA_BADGE_OR_VERSION_TYPE_INVALID")
    _require(settings[BADGE_FIELD]["max_length"]==0
             or settings[BADGE_FIELD]["max_length"]>=MAX_BADGE_LENGTH,
             "SCHEMA_BADGE_TOO_SHORT")
    _require(settings[BADGE_FIELD]["min_length"]<=len(score_label(0)),
             "SCHEMA_BADGE_MIN_LENGTH_CONFLICT")
    # If regex is configured the syntax/policy has to be reviewed separately
    # without printing the actual expression or writing a real CRM item.
    _require(not settings[BADGE_FIELD]["regexp_present"],
             "SCHEMA_BADGE_REGEXP_REQUIRES_REVIEW")
    fields={}
    for code in FIELDS:
        fields[code] = {
            "original_key": modes["Y"][code]["api_key"],
            "camel_key": modes["N"][code]["api_key"],
            "original_writable": True,
            "camel_writable": True,
            "field_type":modes["Y"][code]["type"],
            **settings[code],
        }
    _require(all(fields[f]["original_key"]==f for f in FIELDS),
             "SCHEMA_ORIGINAL_KEYS_NOT_EXACT")
    return {
        "status":"SERVICEIT_ALL_LEAD_SCHEMA_READONLY_PASS",
        "entity_type_id":1062,"category_id_expected":21,
        "smart_process_id":13,"checked_field_count":len(fields),
        "fields":fields,
        "new_items_checked":0,"crm_writes":0,"schema_mutations":0,
        "production_writer_activated":False,
        "existing_two_uncertain_ledgers_untouched":True,
        "kanban_display_configured":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Strictly read-only all-lead schema readiness")
    p.add_argument("--inspect-live",action="store_true")
    p.add_argument("--webhook-from-env",action="store_true")
    args=p.parse_args(argv)
    try:
        if not args.inspect_live:
            _require(not args.webhook_from_env,"SCHEMA_LIVE_FLAG_REQUIRED")
            print(json.dumps({"status":"SERVICEIT_ALL_LEAD_SCHEMA_OFFLINE",
                              "required_fields":list(FIELDS), "crm_writes":0},
                              ensure_ascii=False))
            return 0
        _require(args.webhook_from_env,"SCHEMA_SECURE_WEBHOOK_REQUIRED")
        secret=os.environ.pop("SERVICEIT_AI_COCKPIT_WEBHOOK","")
        _require(bool(secret),"SCHEMA_WEBHOOK_MISSING")
        with httpx.Client() as client:
            result=inspect_all_lead_schema(Rest(secret,client))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (SchemaPreflightBlocked,CockpitBlocked,TypeError,ValueError,
            KeyError,AttributeError,httpx.HTTPError):
        print(json.dumps({"status":"SERVICEIT_ALL_LEAD_SCHEMA_BLOCKED",
                          "crm_writes":0,"secrets_in_output":False,
                          "no_write_authorization":True}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
