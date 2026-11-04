"""Stage 4 live read-only CRM evidence binding.

One independently signed nine-gate corporate proof can be evaluated only
against a fresh EXACT crm.item.get before-image in Bitrix24 1062/category 21.
No source crawler, no CRM mutation, no task, no contact; no auto-LIVE without
a separately authorized corporate proof issuer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import re

import httpx

from .bitrix_1062_mapping import FIELD_MAP
from .bitrix_metadata_audit import validate_webhook
from .serviceit_enrichment_worker import normalized, fingerprint, validate_identity, Blocked
from .serviceit_qualification_proof_v2 import (
    _validate_signed, qualify_signed, crm_inn_visibility_readonly,
    ProofBlocked, HOLD,
)

_MAX_RESPONSE = 300_000


def review_signed_card_readonly(client, webhook, card_id, receipt, verifier_key, *, now=None):
    """Return only HOLD if any proof/portal/company/snapshot mismatch.

    A valid signature proves the receipt came from the configured holder
    of verifier_key, NOT that this holder collected truthful external
    registry/STOP/legal evidence. That independent issuer remains required.
    """
    now = now or datetime.now(timezone.utc)
    try:
        if (not isinstance(client, httpx.Client)
                or type(card_id) is not int or not 8 <= card_id <= 2_000_000_000):
            return dict(HOLD)
        # Reject missing, forged or stale evidence without exposing customer data.
        snap = _validate_signed(receipt, verifier_key, now.astimezone(timezone.utc))
        if snap.get("item_id") != card_id:
            return dict(HOLD)
        base = validate_webhook(webhook)
        if not base.startswith("https://serviseit.bitrix24.ru/rest/"):
            return dict(HOLD)
        res = client.post(base + "/crm.item.get.json", json={
            "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y"},
            timeout=20, follow_redirects=False)
        if res.status_code != 200 or not 0 < len(res.content) <= _MAX_RESPONSE:
            return dict(HOLD)
        body = res.json()
        if (type(body) is not dict or "error" in body
                or type(body.get("result")) is not dict
                or type(body["result"].get("item")) is not dict):
            return dict(HOLD)
        item = normalized(body["result"]["item"])
        validate_identity(item, card_id)
        company = snap["company"]
        signal = snap["signals"][0]
        for value, code in (
            (company["inn"], FIELD_MAP["company_inn"]),
            (company["name"], FIELD_MAP["company_name"]),
            (company["region"], FIELD_MAP["region"]),
            (signal["source_url"], FIELD_MAP["signal_url"]),
            (signal["signal_date"], FIELD_MAP["signal_date"]),
        ):
            if item.get(code) != value:
                return dict(HOLD)
        # Existing corporate STOP or any prior assigned score must not be ignored.
        if item.get("UF_CRM_13_SVCIT_STOP_REASON") not in (None, ""):
            return dict(HOLD)
        if item.get(FIELD_MAP["lead_score"]) not in (None, ""):
            return dict(HOLD)
        if item.get(FIELD_MAP["score_version"]) not in (None, ""):
            return dict(HOLD)
        # A valid corporate signature cannot override a positive exact-INN
        # match visible in Bitrix requisite records. Missing permissions
        # must fail closed. Zero matches does NOT prove full corporate dedup;
        # the independent signed global-CRM evidence remains mandatory.
        visible = crm_inn_visibility_readonly(client, webhook, company["inn"])
        if visible.get("status") != "NO_MATCH_IN_VISIBLE_REQUISITES_ONLY":
            return dict(HOLD)
        before_hash = fingerprint(item)
        if not hmac.compare_digest(before_hash, snap["before_hash"]):
            return dict(HOLD)
        return qualify_signed(receipt, verifier_key, now=now,
                              current_crm_before_hash=before_hash)
    except (ProofBlocked, Blocked, KeyError, ValueError, TypeError,
            AttributeError, OverflowError, httpx.HTTPError):
        return dict(HOLD)
