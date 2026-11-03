"""Fail-closed read-only proof that Bitrix can detect known B2B source duplicates.

Before enabling ANY unattended Gmail -> Bitrix card creation, demonstrate
both positive and negative exact-source search. A response like [[]] MUST
not be mistaken for an empty CRM: it may hide existing records.
"""
from __future__ import annotations

import os

import httpx

from .b2b_github_to_bitrix import exact_duplicate, BridgeBlocked
from .bitrix_metadata_audit import validate_webhook

KNOWN_EXISTING = "B2B-CENTER-4665948"
PROBE_NONEXISTENT = "B2B-CENTER-999999999999"


class DuplicateSearchBlocked(ValueError):
    pass


def verified_duplicate_lookup(client: httpx.Client, webhook: str) -> dict:
    if not isinstance(client, httpx.Client):
        raise DuplicateSearchBlocked("B2B_READONLY_CLIENT_REQUIRED")
    base = validate_webhook(webhook)
    known = exact_duplicate(client, base, KNOWN_EXISTING)
    if len(known) != 1 or known[0].get("UF_CRM_13_SVCIT_SIGNAL_ID") != KNOWN_EXISTING:
        raise DuplicateSearchBlocked("B2B_KNOWN_DUPLICATE_NOT_VISIBLE")
    unseen = exact_duplicate(client, base, PROBE_NONEXISTENT)
    if unseen:
        raise DuplicateSearchBlocked("B2B_NEGATIVE_DUPLICATE_PROBE_CONFLICT")
    return {"status": "B2B_EXACT_DUPLICATE_READONLY_PASS", "crm_writes": 0,
            "gigachat_calls": 0, "existing_found": True, "unseen_empty": True}


def main() -> int:
    secret = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not secret:
        print("B2B_EXACT_DUPLICATE_READONLY=SECRET_NOT_PROVIDED")
        return 2
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
            result = verified_duplicate_lookup(client, secret)
        print("B2B_EXACT_DUPLICATE_READONLY=PASS")
        print("CRM_WRITES=0")
        print("GIGACHAT_CALLS=0")
        return 0
    except (DuplicateSearchBlocked, BridgeBlocked, httpx.HTTPError, ValueError,
            TypeError, KeyError, AttributeError):
        # No response bodies, Bitrix IDs, tender subjects, source keys or
        # secret-bearing REST URLs in owner or GitHub logs.
        print("B2B_EXACT_DUPLICATE_READONLY=UNVERIFIED")
        print("NO_NEW_CARDS_OR_AI_ACTIVATED=YES")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
