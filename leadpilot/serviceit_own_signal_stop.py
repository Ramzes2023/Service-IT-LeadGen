"""Independent ServiceIT LeadGen-only STOP-list. No corporate STOP data.

Approved rules live in a dedicated versioned JSON config, starting empty.
A rule only blocks a FUTURE 1062/21 signal with an exact valid INN; unmatched
is never corporate STOP clearance. No contact, score, stage or client changes.
"""
from __future__ import annotations

import json
from pathlib import Path

from .bitrix_1062_mapping import _is_valid_inn

VERSION = "serviceit-own-signal-stop-v1"
SCOPE = "serviseit.bitrix24.ru:1062/21"
PATH = Path(__file__).resolve().parent.parent / "config" / "serviceit_own_signal_stop.json"
ALLOWED_REASONS = frozenset((
    "REJECTED_BY_SERVICEIT", "NOT_RELEVANT", "INVALID_SIGNAL",
    "ALREADY_REVIEWED", "DO_NOT_REPROCESS",
))
MAX_RULES = 5000


class SignalStopBlocked(ValueError):
    pass


def require(ok, why):
    if not ok:
        raise SignalStopBlocked(why)


def read_rules(path=PATH):
    require(isinstance(path, Path) and path.is_file()
            and path.stat().st_size <= 250_000, "OWN_STOP_FILE_MISSING_OR_LARGE")
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (UnicodeError, ValueError, OSError):
        raise SignalStopBlocked("OWN_STOP_JSON_INVALID") from None
    require(type(payload) is dict
            and set(payload) == {"version", "scope", "rules"}
            and payload["version"] == VERSION and payload["scope"] == SCOPE
            and type(payload["rules"]) is list
            and len(payload["rules"]) <= MAX_RULES,
            "OWN_STOP_SCHEMA_OR_SCOPE_INVALID")
    unique = set()
    for rule in payload["rules"]:
        require(type(rule) is dict
                and set(rule) == {"kind", "value", "reason"}
                and rule["kind"] in ("inn", "card_id")
                and type(rule["value"]) is str
                and (rule["kind"] == "inn" and _is_valid_inn(rule["value"])
                     or rule["kind"] == "card_id"
                     and rule["value"].isascii()
                     and rule["value"].isdecimal()
                     and 8 <= int(rule["value"]) <= 2_000_000_000)
                and rule["reason"] in ALLOWED_REASONS,
                "OWN_STOP_RULE_INVALID")
        key = (rule["kind"], rule["value"])
        require(key not in unique, "OWN_STOP_DUPLICATE_RULE")
        unique.add(key)
    return payload["rules"]


def check(inn, rules, *, card_id=None):
    require(type(rules) is list, "OWN_STOP_UNVERIFIED_RULES")
    if card_id is not None:
        require(type(card_id) is int
                and 8 <= card_id <= 2_000_000_000
                and card_id not in (3, 7), "OWN_STOP_CARD_ID_UNTRUSTED")
    for rule in rules:
        require(type(rule) is dict
                and rule.get("kind") in ("inn", "card_id")
                and rule.get("reason") in ALLOWED_REASONS
                and type(rule.get("value")) is str,
                "OWN_STOP_UNTRUSTED_RULE")
        if rule["kind"] == "card_id":
            require(rule["value"].isascii()
                    and rule["value"].isdecimal()
                    and 8 <= int(rule["value"]) <= 2_000_000_000,
                    "OWN_STOP_CARD_RULE_INVALID")
            if card_id is not None and int(rule["value"]) == card_id:
                return "HOLD_OWN_SIGNAL_STOP", rule["reason"]
        else:
            require(_is_valid_inn(rule["value"]),
                    "OWN_STOP_INN_RULE_INVALID")
            if type(inn) is str and inn == rule["value"] and _is_valid_inn(inn):
                return "HOLD_OWN_SIGNAL_STOP", rule["reason"]
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_MISSING_VALID_INN", None
    return ("OWN_SIGNAL_STOP_EMPTY" if not rules
            else "NOT_IN_OWN_SIGNAL_STOP"), None
