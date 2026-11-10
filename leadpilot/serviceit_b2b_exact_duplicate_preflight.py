"""Fail-closed read-only proof that Bitrix can detect known B2B source duplicates.

Before enabling ANY unattended Gmail -> Bitrix card creation, demonstrate
both positive and negative exact-source search. A response like [[]] MUST
not be mistaken for an empty CRM: it may hide existing records.
"""
from __future__ import annotations

import os
import re

import httpx

from .b2b_github_to_bitrix import BridgeBlocked
from .serviceit_b2b_positive_control import positive_duplicate_control
from .bitrix_metadata_audit import validate_webhook


class DuplicateSearchBlocked(ValueError):
    pass


def verified_duplicate_lookup(client: httpx.Client, webhook: str) -> dict:
    if not isinstance(client, httpx.Client):
        raise DuplicateSearchBlocked("B2B_READONLY_CLIENT_REQUIRED")
    base = validate_webhook(webhook)
    positive_duplicate_control(client, base)
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
    except (DuplicateSearchBlocked, BridgeBlocked) as exc:
        # Only our fixed error codes, not remote REST error descriptions.
        reason = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{4,96}", reason):
            reason = "UNTRUSTED_DIAGNOSTIC"
        print("B2B_EXACT_DUPLICATE_READONLY=UNVERIFIED")
        print("B2B_EXACT_DUPLICATE_REASON=" + reason)
        print("NO_NEW_CARDS_OR_AI_ACTIVATED=YES")
        return 2
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        # Never reveal request URLs (they embed the incoming REST token),
        # response bodies, customer data or source keys in console logs.
        print("B2B_EXACT_DUPLICATE_READONLY=UNVERIFIED")
        print("B2B_EXACT_DUPLICATE_REASON=TRANSPORT_OR_RESPONSE")
        print("NO_NEW_CARDS_OR_AI_ACTIVATED=YES")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
