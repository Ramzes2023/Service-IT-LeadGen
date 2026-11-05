"""Corporate STOP precheck by keyed INN hashes: positive HOLDs only.

The full all-channel corporate STOP review remains a separate independent
gate. A missing INN in this list NEVER means permission to contact, cleared
STOP, a customer-free CRM or a formally qualified lead.

No raw INNs or private customer names are committed to the public repository;
an approved administrator must securely provision the versioned HMAC manifest
and its independent secret into GitHub Secrets, not workflow vars.
"""
from __future__ import annotations
from hashlib import sha256
import hmac
import json
import re

from .bitrix_1062_mapping import _is_valid_inn

VERSION = "serviceit-corporate-stop-inn-v1"
SCOPE = "serviseit.bitrix24.ru:1062/21"
ENABLED = "YES_1062_21_CORPORATE_INN_HOLD_ONLY"
MAX_ITEMS = 25000
MAX_MANIFEST_BYTES = 2_000_000

class CorporateStopBlocked(ValueError):
    pass

def need(condition, code):
    if not condition:
        raise CorporateStopBlocked(code)

def inn_digest(inn: str, key: str) -> str:
    need(type(inn) is str and _is_valid_inn(inn), "STOP_INN_INVALID")
    need(type(key) is str and 32 <= len(key) <= 512,
         "STOP_KEY_NOT_SEPARATELY_APPROVED")
    return hmac.new(key.encode("utf-8"),
                    (VERSION + "|inn|" + inn).encode("ascii"),
                    sha256).hexdigest()

def validated_manifest(raw: str) -> list[str]:
    need(type(raw) is str and 0 < len(raw) <= MAX_MANIFEST_BYTES,
         "STOP_MANIFEST_MISSING_OR_TOO_LARGE")
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        raise CorporateStopBlocked("STOP_MANIFEST_JSON_INVALID") from None
    need(type(obj) is dict and set(obj) == {"version","scope","digests"}
         and obj.get("version") == VERSION and obj.get("scope") == SCOPE,
         "STOP_MANIFEST_SCOPE_OR_VERSION_INVALID")
    items = obj["digests"]
    need(type(items) is list and len(items) <= MAX_ITEMS,
         "STOP_MANIFEST_ITEMS_INVALID")
    need(all(type(d) is str and re.fullmatch(r"[0-9a-f]{64}",d)
             for d in items), "STOP_MANIFEST_DIGEST_INVALID")
    need(items == sorted(set(items)), "STOP_MANIFEST_NOT_DEDUPED_SORTED")
    return items

def precheck(inn: str, env: dict) -> str:
    """Return HIT, NO_MATCH_IN_INN_SCOPE_ONLY, or NOT_CONFIGURED.

    Any broken enabled configuration raises; caller MUST HOLD rather than
    treating the broken list as an empty set. No network or CRM writes.
    """
    need(type(env) is dict, "STOP_ENV_INVALID")
    flag = env.get("SERVICEIT_CORPORATE_STOP_INN_ENABLED","")
    if not flag:
        # Even if a manifest/key exists, no positive check until owner
        # expressly enables this exact scope.
        return "NOT_CONFIGURED"
    need(flag == ENABLED, "STOP_OWNER_SCOPE_NOT_APPROVED")
    manifest = validated_manifest(
        env.get("SERVICEIT_CORPORATE_STOP_INN_MANIFEST",""))
    secret = env.get("SERVICEIT_CORPORATE_STOP_INN_KEY","")
    value = inn_digest(inn,secret)
    # Compare exact digests without leaking input or manifest values.
    return ("HIT" if any(hmac.compare_digest(value, candidate)
                         for candidate in manifest)
            else "NO_MATCH_IN_INN_SCOPE_ONLY")

def make_approved_manifest(inns: list[str], key: str) -> dict:
    """Offline-only preparation helper; do not log the raw list or key."""
    need(type(inns) is list and len(inns) <= MAX_ITEMS,
         "STOP_INPUT_LIST_INVALID")
    return {"version": VERSION, "scope": SCOPE,
            "digests": sorted({inn_digest(inn,key) for inn in inns})}
