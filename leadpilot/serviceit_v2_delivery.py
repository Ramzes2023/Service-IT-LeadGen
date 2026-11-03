"""Postgres outbox -> guarded Bitrix24 HOLD writer for Service IT V2.

No Gmail secrets, no autonomous lead promotion, no mass backfill. Staging
requires separate explicit approval. Delivery is one-shot under PostgreSQL
row lock; uncertain HTTP result is NEVER blindly retried.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .b2b_email_opportunity_bridge import classify_services, clean_tender_url
from .b2b_github_to_bitrix import hold_payload, SIGNAL, KEY, HOLD_STAGE
from .bitrix_metadata_audit import validate_webhook

SOURCE = "B2B-Center"
OP_PREFIX = "serviceit:v2:b2b-hold:"
STATES = {"PENDING", "RESERVED", "POST_UNCERTAIN", "VERIFIED", "BLOCKED"}


class V2DeliveryBlocked(ValueError):
    pass


def require(ok, error):
    if not ok:
        raise V2DeliveryBlocked(error)


def clean_name(value, limit):
    return (isinstance(value, str) and
            1 <= len(value) <= limit and
            not re.search(r"[\x00-\x1f\x7f<>]|https?://|mailto:|@", value, re.I))


def row_to_payload(row):
    """Build safe unverified tender HOLD from exact PostgreSQL observation."""
    require(isinstance(row, dict), "ROW_INVALID")
    source, tender_id = row.get("source"), row.get("tender_id")
    require(source == SOURCE and isinstance(tender_id, str)
            and re.fullmatch(r"\d{5,12}", tender_id), "SOURCE_ID_MISMATCH")
    url = row.get("canonical_url")
    require(clean_tender_url(url) == (url, tender_id), "CANONICAL_SOURCE_URL_INVALID")
    subject = row.get("subject")
    organizer = row.get("organizer")
    require(clean_name(subject, 240) and clean_name(organizer, 120),
            "UNTRUSTED_OR_INCOMPLETE_TENDER")
    services = classify_services(subject)
    require(services and row.get("review_status") == "TENDER_OPPORTUNITY_HOLD"
            and row.get("qualified_lead") is False
            and row.get("buyer_inn") is None
            and row.get("fns_verification") == "NOT_CHECKED",
            "NOT_APPROVED_FOR_TENDER_HOLD")
    notice = {
        "source": SOURCE, "tender_id": tender_id, "source_url": url,
        "subject": subject, "organizer": organizer,
        "buyer_inn": None, "fns_msp": None, "region": None,
        "original_publication_verified": False,
        "contact_approved": False, "qualified_lead": False,
    }
    return hold_payload(notice, services)


def op_key(tender_id):
    require(isinstance(tender_id, str)
            and re.fullmatch(r"\d{5,12}", tender_id), "TENDER_ID_INVALID")
    return OP_PREFIX + tender_id


def stage_one_approved(conn, tender_id, *, approval_ref):
    """Explicit approved source → deterministic outbox; NOT a lead."""
    key = op_key(tender_id)
    require(isinstance(approval_ref, str)
            and re.fullmatch(r"SVCIT-[A-Z0-9-]{6,60}", approval_ref),
            "APPROVAL_REFERENCE_REQUIRED")
    with conn.transaction():
        raw = conn.execute("""
            SELECT source,tender_id,canonical_url,subject,organizer,
                   review_status,qualified_lead,buyer_inn,fns_verification
            FROM serviceit_v2_opportunities
            WHERE source=%s AND tender_id=%s FOR UPDATE
            """, (SOURCE, tender_id)).fetchone()
        require(raw is not None, "PROCUREMENT_NOT_FOUND")
        names = ("source", "tender_id", "canonical_url", "subject", "organizer",
                 "review_status", "qualified_lead", "buyer_inn", "fns_verification")
        row_to_payload(dict(zip(names, raw)))
        existing = conn.execute("""
           SELECT state FROM serviceit_v2_crm_outbox WHERE operation_key=%s
           """, (key,)).fetchone()
        if existing is not None:
            return {"status": "ALREADY_STAGED", "state": existing[0],
                    "tender_id": tender_id, "crm_writes": 0}
        # Approval reference remains in a restricted separate audit, rather
        # than adding unchecked comment text or mail PII into the CRM.
        conn.execute("""
          INSERT INTO serviceit_v2_crm_outbox
            (operation_key,source,tender_id,state,approval_ref)
          VALUES (%s,%s,%s,'PENDING',%s)
        """, (key, SOURCE, tender_id, approval_ref))
    return {"status": "STAGED_FOR_APPROVED_HOLD", "tender_id": tender_id,
            "crm_writes": 0}


def _rest(client, base, method, body):
    require(method in {"crm.item.list", "crm.item.add", "crm.item.get"},
            "DISALLOWED_BITRIX_METHOD")
    try:
        res = client.post(base + "/" + method + ".json", json=body,
                          timeout=15, follow_redirects=False)
        require(res.status_code == 200 and len(res.content) <= 256000,
                "CRM_RESPONSE_UNCERTAIN")
        data = res.json()
    except (httpx.HTTPError, ValueError, TypeError):
        raise V2DeliveryBlocked("CRM_RESPONSE_UNCERTAIN") from None
    require(isinstance(data, dict) and not data.get("error")
            and isinstance(data.get("result"), dict)
            and "next" not in data, "CRM_RESPONSE_UNCERTAIN")
    return data["result"]


def existing_source_cards(client, base, source_id):
    result = _rest(client, base, "crm.item.list", {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
        "filter": {"categoryId": 21, SIGNAL: source_id},
        "select": ["id", "entityTypeId", "categoryId", SIGNAL],
        "start": 0,
    })
    rows = result.get("items")
    require(isinstance(rows, list) and len(rows) <= 49
            and result.get("total") == len(rows),
            "CRM_DEDUP_NOT_EXHAUSTIVE")
    for row in rows:
        require(isinstance(row, dict)
                and row.get(SIGNAL) == source_id
                and str(row.get("entityTypeId")) == "1062"
                and str(row.get("categoryId")) == "21"
                and isinstance(row.get("id"), int)
                and row["id"] > 0, "CRM_DEDUP_SHAPE_UNCERTAIN")
    return rows


def _readback(client, base, card_id, payload):
    result = _rest(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y"})
    actual = result.get("item")
    require(isinstance(actual, dict)
            and str(actual.get("entityTypeId")) == "1062"
            and str(actual.get("categoryId")) == "21"
            and all(actual.get(k) == v for k, v in payload["fields"].items()),
            "CRM_READBACK_MISMATCH")
    return actual


def _verify_existing_identity(client, base, card_id, source_id):
    """Existing Bitrix card may predate v2; verify only stable identity."""
    result = _rest(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y"})
    item = result.get("item")
    require(isinstance(item, dict) and
            str(item.get("entityTypeId")) == "1062" and
            str(item.get("categoryId")) == "21" and
            item.get(SIGNAL) == source_id and
            item.get("UF_CRM_13_SVCIT_SIGNAL_KIND") == "tender-opportunity",
            "EXISTING_CARD_IDENTITY_UNCERTAIN")
    return item


def _mark(conn, key, state, card_id=None):
    require(state in STATES - {"PENDING"}, "STATE_INVALID")
    conn.execute("""
        UPDATE serviceit_v2_crm_outbox
        SET state=%s,bitrix_item_id=%s,last_attempt_at=now()
        WHERE operation_key=%s
        """, (state, card_id, key))


def process_one(conn, client, webhook, *, commit_live=False):
    """One bounded approved operation; never retries uncertain POST.

    Caller supplies a separate CRM-enabled HTTP client and approved webhook.
    Default read-only preview. Adopts existing matching CRM card on dedup.
    """
    if not commit_live:
        return {"status": "CRM_WRITER_DISABLED", "crm_writes": 0}
    require(os.environ.get("SERVICEIT_V2_CRM_HOLD_ENABLED") == "YES"
            and os.environ.get("SERVICEIT_V2_CRM_WRITES_APPROVED") == "YES",
            "CRM_APPROVAL_NOT_ENABLED")
    require(isinstance(client, httpx.Client), "HTTP_CLIENT_MISSING")
    base = validate_webhook(webhook)
    with conn.transaction():
        entry = conn.execute("""
            SELECT operation_key,tender_id
            FROM serviceit_v2_crm_outbox
            WHERE source=%s AND state='PENDING'
            ORDER BY operation_key FOR UPDATE SKIP LOCKED LIMIT 1
        """, (SOURCE,)).fetchone()
        if entry is None:
            return {"status": "NO_PENDING_APPROVED_HOLDS", "crm_writes": 0}
        key, tender_id = entry
        require(key == op_key(tender_id), "LEDGER_IDENTITY_UNCERTAIN")
        raw = conn.execute("""
            SELECT source,tender_id,canonical_url,subject,organizer,
                   review_status,qualified_lead,buyer_inn,fns_verification
            FROM serviceit_v2_opportunities
            WHERE source=%s AND tender_id=%s
            FOR SHARE
        """, (SOURCE, tender_id)).fetchone()
        require(raw is not None, "ORIGINAL_OPPORTUNITY_MISSING")
        names = ("source", "tender_id", "canonical_url", "subject", "organizer",
                 "review_status", "qualified_lead", "buyer_inn", "fns_verification")
        payload = row_to_payload(dict(zip(names, raw)))
        # Commitment happens BEFORE any external network POST, which may have
        # caused a mutation even if the response was lost.
        _mark(conn, key, "RESERVED")
    source_id = payload["fields"][SIGNAL]
    # After the RESERVED state, all failures remain uncertain and can only be
    # reconciled by READs. Never automatically stage/retry this operation.
    try:
        preflight(ReadOnlyBitrix(base, client=client))
        present = existing_source_cards(client, base, source_id)
        if present:
            require(len(present) == 1, "MULTIPLE_PREEXISTING_CARDS")
            card_id = present[0]["id"]
            _verify_existing_identity(client, base, card_id, source_id)
            _mark(conn, key, "VERIFIED", card_id)
            return {"status": "PREEXISTING_CARD_VERIFIED", "card_id": card_id,
                    "tender_id": tender_id, "crm_writes": 0}
        added = _rest(client, base, "crm.item.add", payload)
        item = added.get("item")
        require(isinstance(item, dict) and type(item.get("id")) is int
                and item["id"] > 0, "CRM_POST_UNCERTAIN")
        card_id = item["id"]
        _readback(client, base, card_id, payload)
        _mark(conn, key, "VERIFIED", card_id)
        return {"status": "ONE_HOLD_VERIFIED", "card_id": card_id,
                "tender_id": tender_id, "crm_writes": 1,
                "qualified_leads": 0}
    except (V2DeliveryBlocked, ValueError, TypeError, httpx.HTTPError):
        _mark(conn, key, "POST_UNCERTAIN")
        return {"status": "CRM_POST_UNCERTAIN_NEEDS_READONLY_RECONCILIATION",
                "tender_id": tender_id, "crm_writes": "UNKNOWN",
                "never_blind_retry": True}


def reconcile_one(conn, client, webhook, tender_id):
    """Read-only CRM check of one reserved/uncertain operation: never POST add."""
    key = op_key(tender_id)
    base = validate_webhook(webhook)
    row = conn.execute("""
        SELECT state FROM serviceit_v2_crm_outbox WHERE operation_key=%s
    """, (key,)).fetchone()
    require(row is not None and row[0] in ("RESERVED", "POST_UNCERTAIN"),
            "NOT_AN_UNCERTAIN_OPERATION")
    source_id = "B2B-CENTER-" + tender_id
    cards = existing_source_cards(client, base, source_id)
    if not cards:
        # Do not auto-retry even if not found: remote eventual consistency.
        return {"status": "NOT_FOUND_YET_MANUAL_REVIEW", "crm_writes": 0}
    require(len(cards) == 1, "DUPLICATES_NEED_MANUAL_REVIEW")
    card_id = cards[0]["id"]
    _verify_existing_identity(client, base, card_id, source_id)
    conn.execute("""
        UPDATE serviceit_v2_crm_outbox SET bitrix_item_id=%s,
          state='VERIFIED',last_attempt_at=now()
        WHERE operation_key=%s AND state IN ('RESERVED','POST_UNCERTAIN')
    """, (card_id, key))
    return {"status": "EXISTING_CARD_RECONCILED", "card_id": card_id,
            "crm_writes": 0}
