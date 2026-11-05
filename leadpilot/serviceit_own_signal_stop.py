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
                and rule["kind"] == "inn"
                and type(rule["value"]) is str
                and _is_valid_inn(rule["value"])
                and rule["reason"] in ALLOWED_REASONS,
                "OWN_STOP_RULE_INVALID")
        require(rule["value"] not in unique,
                "OWN_STOP_DUPLICATE_INN_RULE")
        unique.add(rule["value"])
    return payload["rules"]


def check(inn, rules):
    require(type(rules) is list, "OWN_STOP_UNVERIFIED_RULES")
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_MISSING_VALID_INN", None
    for rule in rules:
        require(type(rule) is dict
                and rule.get("kind") == "inn"
                and rule.get("reason") in ALLOWED_REASONS
                and type(rule.get("value")) is str
                and _is_valid_inn(rule["value"]),
                "OWN_STOP_UNTRUSTED_RULE")
        if inn == rule["value"]:
            return "HOLD_OWN_SIGNAL_STOP", rule["reason"]
    return ("OWN_SIGNAL_STOP_EMPTY" if not rules
            else "NOT_IN_OWN_SIGNAL_STOP"), None
