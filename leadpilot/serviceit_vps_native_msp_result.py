"""VPS-native official FNS-MSP HOLD result -> exactly one Bitrix field.

Never infers legal active status, sales authorization, or formal Lead Score.
The official snapshot has already been checked against a true new card and
stored in the native PostgreSQL table. Writes only the approved membership
string with a durable native reservation and exact crm.item.get readback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets

from .serviceit_vps_pg_queue import VpsBlocked
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_fns_msp_crm_result_writer import (
    FIELD, receipt_label, result_field_verified, rest,
)
from .serviceit_enrichment_worker import (
    PREFIX, fingerprint, normalized, validate_identity,
)
from .serviceit_stage4_fns_dedupe_watch import read_item
from .serviceit_bitrix_poll_fallback import date_value, after_activation
from .serviceit_own_signal_stop import (
    read_rules as own_stop_rules, check as own_stop_check,
)
from .bitrix_1062_mapping import _is_valid_inn


def verified_receipt(connection, card, inn, now):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT publication, inn_salt, inn_sha256, msp_category, "
            "source_zip_sha256 FROM serviceit_vps_msp_snapshot "
            "WHERE card_id=%s AND publication >= %s "
            "ORDER BY publication DESC LIMIT 1",
            (card, now.date() - __import__("datetime").timedelta(days=45)))
        row = cur.fetchone()
    if not row:
        return None
    publication, salt, digest, category, sha = row
    actual = hashlib.sha256(bytes(salt) + inn.encode("ascii")).digest()
    if not secrets.compare_digest(actual, bytes(digest)):
        raise VpsBlocked("NATIVE_FNS_RECEIPT_INN_MISMATCH")
    if not (type(sha) is str and len(sha) == 64):
        raise VpsBlocked("NATIVE_FNS_RECEIPT_UNVERIFIED")
    return {
        "published": publication.isoformat(),
        "msp_category": category,
        "fns_egrul_verified": False,
        "formal_score_authorized": False,
    }


def process_one(client, env, connection, base, card, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (env.get("SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED") != "YES"
            or env.get("SERVICEIT_VPS_WRITER_MODE") != "LIVE"
            or env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED") != "YES"
            or env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") != "YES"
            or not env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")):
        raise VpsBlocked("NATIVE_FNS_RESULT_GATE_REQUIRED")
    before = read_item(client, base, card)
    activation = after_activation({
        **env, "SERVICEIT_BITRIX_POLL_START_UTC":
            env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"],
    }, now)
    if not activation <= date_value(before.get("createdTime")) <= now:
        raise VpsBlocked("NATIVE_FNS_RESULT_HISTORICAL_CARD")
    inn = before.get(PREFIX + "COMPANY_INN")
    own_status, _ = own_stop_check(inn, own_stop_rules(), card_id=card)
    if own_status == "HOLD_OWN_SIGNAL_STOP":
        return "NATIVE_FNS_OWN_STOP_HOLD"
    if type(inn) is not str or not _is_valid_inn(inn):
        return "NATIVE_FNS_INVALID_INN_HOLD"
    receipt = verified_receipt(connection, card, inn, now)
    if receipt is None:
        return "NATIVE_FNS_AWAITING_OFFICIAL_SNAPSHOT"
    value = receipt_label(receipt)
    if before.get(FIELD) == value:
        return "NATIVE_FNS_RESULT_ALREADY_PRESENT"
    if before.get(FIELD) not in (None, ""):
        return "NATIVE_FNS_RESULT_EXISTING_PROTECTED"
    result_field_verified(client, base)
    latest = read_item(client, base, card)
    if fingerprint(latest) != fingerprint(before):
        raise VpsBlocked("NATIVE_FNS_CARD_CHANGED")
    fence = PostgresNativeFence(connection, "FNS_CRM_V1")
    if fence.reserve(card) != "RESERVED":
        return "NATIVE_FNS_FENCED_NO_REPLAY"
    fence.receipt = {"expected": {"field": FIELD, "value": value}}
    if not fence.finalize(card, "UNCERTAIN"):
        raise VpsBlocked("NATIVE_FNS_PREWRITE_CHECKPOINT_MISSING")
    try:
        rest(client, base, "crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": {FIELD: value}})
        after = normalized(rest(client, base, "crm.item.get", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y"})["item"])
        validate_identity(after, card)
        if after.get(FIELD) != value:
            raise VpsBlocked("NATIVE_FNS_RESULT_READBACK_MISMATCH")
        ignored = {FIELD, "updatedTime", "updatedBy",
                   "lastActivityTime", "lastActivityBy"}
        if not all(after.get(k) == v for k, v in latest.items()
                   if k not in ignored):
            raise VpsBlocked("NATIVE_FNS_NON_TARGET_CHANGED")
    except Exception:
        return "NATIVE_FNS_WRITE_UNCERTAIN_NO_RETRY"
    return ("NATIVE_FNS_CRM_READBACK_VERIFIED"
            if fence.finalize(card, "VERIFIED")
            else "NATIVE_FNS_READBACK_OK_LEDGER_UNCERTAIN")
