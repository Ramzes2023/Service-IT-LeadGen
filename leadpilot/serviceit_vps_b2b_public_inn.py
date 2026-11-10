"""VPS-native B2B public company INN handoff, one-use PG fence.

No RouterAI, no EGRUL-status claim, no Score, deal, task or outreach.
The next independent official FNS MSP cycle reads COMPANY_INN as a
candidate and determines registry inclusion only (not active EGRUL status).
"""
from __future__ import annotations

from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_public_supplement import source_backed_company_inn_update
from .serviceit_enrichment_worker import Rest, fingerprint, normalized, validate_identity
from .serviceit_vps_b2b_deep_research import safe_notice, source_findings
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_vps_pg_queue import secret_file

NAMESPACE = "B2B_PUBLIC_INN_V1"
ENABLED = "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED"
COMPANY_INN = PREFIX + "COMPANY_INN"


class PublicInnBlocked(ValueError):
    pass


def select_one(connection):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT b.card_id,b.source_id,b.notice_json "
            "FROM serviceit_vps_b2b_inbox b "
            "WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL "
            "AND NOT EXISTS(SELECT 1 FROM serviceit_vps_native_receipts r "
            "WHERE r.namespace=%s AND r.card_id=b.card_id) "
            "ORDER BY b.received_at,b.source_id LIMIT 1",
            (NAMESPACE,))
        return cur.fetchone()


def run_once(env, client, connection):
    if env.get(ENABLED, "NO") != "YES":
        return {"status": "B2B_PUBLIC_INN_DISABLED", "writes": 0}
    from .serviceit_vps_worker import live_permitted
    if (not live_permitted(env)
            or env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") != "YES"
            or env.get("SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED") != "YES"
            or env.get("SERVICEIT_VPS_INTAKE_ENABLED", "NO") != "NO"):
        raise PublicInnBlocked("B2B_PUBLIC_INN_GATES_MISSING")
    selected = select_one(connection)
    if selected is None:
        return {"status": "B2B_PUBLIC_INN_IDLE", "writes": 0}
    card, source_id, notice = selected
    PostgresNativeFence.validate_card(card)
    rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
    before = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})["item"])
    validate_identity(before, card)
    safe_notice(notice, card, source_id, before)
    fence = PostgresNativeFence(connection, NAMESPACE)
    if before.get(COMPANY_INN) not in (None, ""):
        if fence.reserve(card) == "RESERVED":
            fence.finalize(card, "HOLD")
        return {"status": "B2B_PUBLIC_INN_EXISTING_PROTECTED", "writes": 0}
    if any(not isinstance(before.get(k), str) or not before[k].strip()
           for k in AI_FIELDS):
        # Do not starve the normal 9-field research if AI is not yet complete.
        return {"status": "B2B_PUBLIC_INN_WAITING_FOR_AI", "writes": 0}
    # Source-only retrieval, no RouterAI/LLM calls.
    try:
        sources = source_findings(before, notice, env, client)
        update = source_backed_company_inn_update(before, sources)
    except Exception:
        if fence.reserve(card) == "RESERVED":
            fence.finalize(card, "HOLD")
        return {"status": "B2B_PUBLIC_INN_RESEARCH_UNVERIFIED", "writes": 0}
    if fence.reserve(card) != "RESERVED":
        return {"status": "B2B_PUBLIC_INN_ALREADY_FENCED", "writes": 0}
    if not update:
        fence.finalize(card, "HOLD")
        return {"status": "B2B_PUBLIC_INN_NOT_CORROBORATED", "writes": 0}
    fresh = normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})["item"])
    validate_identity(fresh, card)
    if (fresh.get(COMPANY_INN) not in (None, "")
            or fingerprint(before) != fingerprint(fresh)
            or any(before.get(k) != fresh.get(k) for k in AI_FIELDS)):
        fence.finalize(card, "UNCERTAIN")
        return {"status": "B2B_PUBLIC_INN_MANAGER_CONFLICT", "writes": 0}
    # Durable salted intent BEFORE CRM call: once uncertain, never replay.
    fence.receipt = {"expected": update}
    if not fence.finalize(card, "UNCERTAIN"):
        raise PublicInnBlocked("B2B_PUBLIC_INN_PREWRITE_NOT_DURABLE")
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y", "fields": update})
        after = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card,
            "useOriginalUfNames": "Y"})["item"])
        validate_identity(after, card)
        if any(after.get(k) != v for k, v in update.items()):
            raise PublicInnBlocked("B2B_PUBLIC_INN_READBACK_MISMATCH")
        ignored = set(update) | {"updatedTime", "updatedBy",
                                "lastActivityTime", "lastActivityBy"}
        if any(after.get(k) != v for k, v in fresh.items()
               if k not in ignored):
            raise PublicInnBlocked("B2B_PUBLIC_INN_OTHER_FIELDS_CHANGED")
    except Exception:
        return {"status": "B2B_PUBLIC_INN_WRITE_UNCERTAIN_NO_RETRY", "writes": 0}
    return {"status": ("B2B_PUBLIC_INN_READBACK_VERIFIED"
                        if fence.finalize(card, "VERIFIED")
                        else "B2B_PUBLIC_INN_READBACK_LEDGER_UNCERTAIN"),
            "writes": 1, "egrul_verified": False,
            "customer_contacts": 0, "formal_score_authorized": False}
