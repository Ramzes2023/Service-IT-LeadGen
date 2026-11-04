"""Stage 4: evidence-locked formal qualification; offline/CRM-read-only only.

A supplier notice, CRM text or GigaChat hypothesis CANNOT certify a gate.
Only independently signed, short-lived CORPORATE verification receipts can
unlock a formal 0..100 score. The signing/verifier service is not bundled and
must be approved/deployed by the customer. No CRM writes or outreach here.
"""
from __future__ import annotations

from datetime import datetime, timezone, date, timedelta
from hashlib import sha256
import hmac
import json
import re

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_ai_lead_engine import assess_lead

VERSION = "serviceit-proof-gate-2026-11-v2"
GATE_ORIGINS = {
    "company_registry": "FNS_EGRUL_OR_EGRIP",
    "source": "PRIMARY_SOURCE_DIRECT_READ",
    "existing_customer": "CORPORATE_CRM_FULL_CUSTOMERS",
    "crm_dedup": "CORPORATE_CRM_GLOBAL_DEDUP",
    "stop_list": "CORPORATE_ALL_CHANNEL_STOP_LIST",
    "legal_basis": "CORPORATE_LEGAL_APPROVAL",
    "contact": "OFFICIAL_CONTACT_VERIFICATION",
    "private_small_moscow": "FNS_MSP_AND_REGION_CHECK",
    "manager_criteria": "CORPORATE_SALES_POLICY",
}
HOLD = {"status": "HOLD_VERIFICATION_REQUIRED", "formal_lead_score": None,
        "crm_handoff_eligible": False, "task_creation_authorized": False,
        "outreach_authorized": False, "crm_writes": 0, "model_calls": 0}


class ProofBlocked(ValueError):
    pass


def _check(ok, code):
    if not ok:
        raise ProofBlocked(code)


def _dt(raw):
    _check(isinstance(raw, str) and len(raw) <= 40, "EVIDENCE_TIME_INVALID")
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ProofBlocked("EVIDENCE_TIME_INVALID") from None
    _check(t.tzinfo is not None and t.utcoffset() is not None, "EVIDENCE_TIME_NAIVE")
    return t.astimezone(timezone.utc)


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_signed(receipt, key, now):
    _check(type(receipt) is dict and set(receipt) == {
           "version", "snapshot", "checks", "signature"}, "RECEIPT_SCHEMA_INVALID")
    _check(receipt["version"] == VERSION, "RECEIPT_VERSION_INVALID")
    _check(type(key) is str and len(key) >= 32, "VERIFIER_SECRET_MISSING")
    signature = receipt["signature"]
    _check(type(signature) is str and re.fullmatch(r"sha256=[0-9a-f]{64}", signature),
           "RECEIPT_SIGNATURE_INVALID")
    unsigned = {k: receipt[k] for k in ("version", "snapshot", "checks")}
    calculated = "sha256=" + hmac.new(key.encode(), _canonical(unsigned), sha256).hexdigest()
    _check(hmac.compare_digest(signature, calculated), "UNTRUSTED_CORPORATE_EVIDENCE")
    snap, checks = receipt["snapshot"], receipt["checks"]
    _check(type(snap) is dict and type(checks) is dict
           and set(checks) == set(GATE_ORIGINS), "EVIDENCE_GATES_INCOMPLETE")
    _check(snap.get("portal") == "serviseit.bitrix24.ru"
           and snap.get("entity_type_id") == 1062
           and snap.get("category_id") == 21
           and type(snap.get("item_id")) is int
           and snap["item_id"] >= 8
           and isinstance(snap.get("before_hash"), str)
           and re.fullmatch("[0-9a-f]{64}", snap["before_hash"]),
           "RECEIPT_CARD_IDENTITY_INVALID")
    captured = _dt(snap.get("captured_at"))
    _check(timedelta(0) <= now-captured <= timedelta(minutes=60),
           "RECEIPT_SNAPSHOT_STALE")
    company = snap.get("company")
    signals = snap.get("signals")
    _check(type(company) is dict and _is_valid_inn(company.get("inn",""))
           and type(signals) is list and len(signals) == 1,
           "RECEIPT_COMPANY_OR_SIGNAL_INVALID")
    for gate, origin in GATE_ORIGINS.items():
        proof = checks[gate]
        _check(type(proof) is dict and proof.get("state") == "VERIFIED"
               and proof.get("finding") == "CLEAR"
               and proof.get("origin") == origin
               and proof.get("subject_inn") == company["inn"]
               and type(proof.get("evidence_digest")) is str
               and re.fullmatch(r"[0-9a-f]{64}", proof["evidence_digest"]),
               "RECEIPT_" + gate.upper() + "_UNVERIFIED")
        checked = _dt(proof.get("checked_at"))
        _check(timedelta(0) <= now-checked <= timedelta(minutes=60),
               "RECEIPT_" + gate.upper() + "_STALE")
    return snap


def qualify_signed(receipt, key, *, now=None, current_crm_before_hash=None):
    """Return formal score only after all 9 signed independent attestations.

    Even a successful result is MANAGER_REVIEW_REQUIRED, not contact approval.
    Never promote a vacancy alone to a direct, ready-to-buy opportunity.
    """
    now = now or datetime.now(timezone.utc)
    try:
        _check(type(now) is datetime and now.tzinfo is not None, "CLOCK_INVALID")
        now = now.astimezone(timezone.utc)
        snap = _validate_signed(receipt, key, now)
        _check(type(current_crm_before_hash) is str
               and re.fullmatch(r"[0-9a-f]{64}", current_crm_before_hash)
               and hmac.compare_digest(current_crm_before_hash, snap["before_hash"]),
               "LIVE_CRM_SNAPSHOT_NOT_INDEPENDENTLY_RECHECKED")
        raw = assess_lead(snap["company"], snap["signals"],
                          checks={g:"VERIFIED" for g in GATE_ORIGINS},
                          today=now.date(), ai_hypothesis=None, demo=False)
        _check(not raw["blocking_checks"] and not raw["unverified_checks"]
               and raw["status"] in ("MANAGER_REVIEW_REQUIRED", "LOW_PRIORITY"),
               "ENGINE_QUALIFICATION_BLOCKED")
        vacancy = raw["signal_type"] in ("1c_vacancy", "it_vacancy")
        return {
            "status": "MANAGER_REVIEW_REQUIRED" if not vacancy
                      else "VACANCY_ONLY_MANAGER_REVIEW",
            "formal_lead_score": raw["lead_score"],
            "score_version": raw["score_version"],
            "verified_evidence_gates": len(GATE_ORIGINS),
            "crm_handoff_eligible": bool(raw["crm_handoff_eligible"] and not vacancy),
            "task_creation_authorized": False,
            "outreach_authorized": False,
            "crm_writes": 0, "model_calls": 0,
        }
    except (ProofBlocked, KeyError, TypeError, ValueError, OverflowError):
        return dict(HOLD)


def crm_inn_visibility_readonly(client: httpx.Client, webhook: str, inn: str):
    """Read ALL bounded visible exact-INN requisite pages, never infer global clear.

    The webhook can be restricted to a subset of the corporate CRM. Even if all
    visible pages are exhausted, this remains only a visible-object check.
    Missing or inconsistent pagination cannot imply 'no duplicate'.
    """
    _check(type(inn) is str and _is_valid_inn(inn), "INN_CHECKSUM_INVALID")
    base = validate_webhook(webhook)
    _check(base.startswith("https://serviseit.bitrix24.ru/rest/"),
           "PORTAL_MISMATCH")
    seen, start, total_expected = set(), 0, None
    try:
        for _ in range(20):
            args = {"filter": {"RQ_INN": inn},
                    "select": ["ID", "ENTITY_TYPE_ID", "ENTITY_ID", "RQ_INN"],
                    "start": start}
            resp = client.post(base + "/crm.requisite.list.json", json=args,
                               follow_redirects=False, timeout=20)
            _check(resp.status_code == 200 and 0 < len(resp.content) <= 256000,
                   "CRM_REQUISITE_SCOPE_OR_HTTP")
            payload = resp.json()
            _check(type(payload) is dict and "error" not in payload
                   and type(payload.get("result")) is list
                   and len(payload["result"]) <= 50, "CRM_REQUISITE_SHAPE_INVALID")
            rows = payload["result"]
            total = payload.get("total")
            _check(total is None or (type(total) is int and total >= 0),
                   "CRM_REQUISITE_TOTAL_UNVERIFIED")
            if total is not None:
                if total_expected is None:
                    total_expected = total
                _check(total_expected == total, "CRM_REQUISITE_TOTAL_CHANGED")
            for r in rows:
                _check(type(r) is dict and str(r.get("RQ_INN")) == inn
                       and str(r.get("ENTITY_TYPE_ID")) in ("3", "4")
                       and str(r.get("ENTITY_ID", "")).isdigit()
                       and str(r.get("ID", "")).isdigit(),
                       "CRM_REQUISITE_IDENTITY_MISMATCH")
                rid = str(r["ID"])
                _check(rid not in seen, "CRM_REQUISITE_REPEATED_PAGE")
                seen.add(rid)
            following = payload.get("next")
            if following is None:
                _check(total_expected is None or total_expected == len(seen),
                       "CRM_REQUISITE_INCOMPLETE_TOTAL")
                return {
                    "status": ("POTENTIAL_EXISTING_RECORD_BLOCK" if seen
                               else "NO_MATCH_IN_VISIBLE_REQUISITES_ONLY"),
                    "visible_matches": len(seen),
                    "visible_pages_complete": True,
                    "global_crm_customer_clear": False,
                    "global_dedup_verified": False,
                    "stop_list_verified": False,
                    "crm_writes": 0,
                }
            _check(type(following) is int and following == start + 50
                   and len(rows) == 50 and len(seen) <= 1000,
                   "CRM_REQUISITE_PAGINATION_UNVERIFIED")
            start = following
        raise ProofBlocked("CRM_REQUISITE_TOO_MANY_PAGES")
    except ProofBlocked:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise ProofBlocked("CRM_REQUISITE_RESPONSE_UNVERIFIED") from None

