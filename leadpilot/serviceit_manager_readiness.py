"""Manager readiness contract for ServiceIT 1062/21.

Implements requirements from the owner's 15-page lead generation specification:
manager receives qualified evidence, reproducible Score, service rationale,
legal/contact checks and next action; a B2B supplier-email notice is only HOLD.

Pure planning only. Real stage writes, tasks, calls and messages belong to a
separately authorized write-fenced adapter after readback and manager assignment.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import date
from urllib.parse import urlsplit
import json
import re

from .serviceit_b2b_hold_ai_fields import PREFIX
from .serviceit_ai_lead_engine import assess_lead
from .serviceit_kanban_presentation import BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, score_label

TYPE = 1062
CATEGORY = 21
MANAGER_STAGE = "DT1062_21:CLIENT"  # Real historical read-only metadata, verify again before live.
NEW_STAGE = "DT1062_21:NEW"
MANAGER_NEXT = PREFIX + "MANAGER_NEXT_ACTION"
REVIEW_DECISION = PREFIX + "REVIEW_DECISION"
STOP_REASON = PREFIX + "STOP_REASON"
SIGNAL_ID = PREFIX + "SIGNAL_ID"
SIGNAL_URL = PREFIX + "SIGNAL_URL"
COMPANY_INN = PREFIX + "COMPANY_INN"
COMPANY_NAME = PREFIX + "COMPANY_NAME"
REGION = PREFIX + "REGION"
AI_STATUS = PREFIX + "AI_STATUS"

REQUIRED_GATES = (
    "company_registry", "source", "existing_customer", "crm_dedup",
    "stop_list", "legal_basis", "contact", "private_small_moscow",
    "manager_criteria",
)
HOLD_STATES = (
    "NOT_RUN_SOURCE_UNVERIFIED", "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED",
)
QUALIFIED_AI_STATUS = "HYPOTHESIS_UNVERIFIED"
SAFE_REVIEW_DECISIONS = ("", "Ожидает проверки менеджером")
FREQUENCY_LIMIT = 20


class ManagerReadinessBlocked(ValueError):
    pass


def _guard(condition, reason):
    if not condition:
        raise ManagerReadinessBlocked(reason)


def preliminary_manager_action(notice, services):
    """An honest next action for source-only HOLD, never a marketing call."""
    _guard(isinstance(notice, dict)
           and notice.get("source") == "B2B-Center"
           and notice.get("original_publication_verified") is False
           and notice.get("qualified_lead") is False
           and notice.get("contact_approved") is False
           and notice.get("buyer_inn") is None
           and notice.get("fns_msp") is None
           and isinstance(services, list)
           and 1 <= len(services) <= 5
           and all(x in {"1C", "IT_SUPPORT", "CRM", "KKT_EDO",
                         "SERVERS", "INTEGRATION"} for x in services),
           "HOLD_SOURCE_UNVERIFIED")
    return ("Проверить первоисточник B2B-Center, дату и актуальность; "
            "установить фактического заказчика, ИНН/ФНС и регион; "
            "проверить клиентов CRM, дубли, стоп-лист и право связи. "
            "До подтверждения менеджером не контактировать.")


def preliminary_manager_packet(notice, services):
    """No numeric score, no prospect role/phone or automatic task on HOLD."""
    action = preliminary_manager_action(notice, services)
    return {
        "status": "HOLD_SOURCE_RESEARCH",
        "stage": NEW_STAGE,
        "manager_section": {
            "company_verification": "НЕ ПРОВЕРЕНА",
            "region": "НЕ УСТАНОВЛЕН",
            "buyer_inn": "НЕ УСТАНОВЛЕН",
            "lead_score": None,
            "score_version": None,
            "who_to_call": "НЕ ОПРЕДЕЛЁН",
            "contact_allowed": False,
            "service_to_check": ", ".join(services),
            "why_now": "Есть уведомление поставщика, но потребность не подтверждена",
            "next_action": action,
        },
        "fields_on_create": {MANAGER_NEXT: action},
        "crm_updates": 0, "task_creates": 0,
        "outbound": 0, "qualified_lead": False,
    }


def _exact_card(card, item_id):
    _guard(type(item_id) is int and 8 <= item_id <= 2_000_000_000
           and item_id not in (3, 7), "HISTORICAL_OR_INVALID_ID")
    _guard(isinstance(card, dict)
           and str(card.get("id")) == str(item_id)
           and str(card.get("entityTypeId")) == str(TYPE)
           and str(card.get("categoryId")) == str(CATEGORY),
           "MANAGER_CARD_IDENTITY_UNVERIFIED")


def manager_readiness(card, item_id, assessment, approval):
    """Readiness to manager REVIEW only, never authorization to call.

    All nine corporate checks must have independently supplied evidence_ref.
    Score comes solely from the deterministic assessment and has a version;
    AI confidence never supplies or overrides Lead Score.
    """
    _exact_card(card, item_id)
    _guard(isinstance(assessment, dict) and isinstance(approval, dict),
           "MANAGER_EVIDENCE_MISSING")
    _guard(assessment.get("mode") == "RESEARCH_OR_REVIEW"
           and assessment.get("status") == "MANAGER_REVIEW_REQUIRED"
           and assessment.get("crm_handoff_eligible") is True
           and assessment.get("human_decision_required") is True
           and assessment.get("outreach_authorized_by_this_module") is False
           and assessment.get("blocking_checks") == []
           and assessment.get("unverified_checks") == [],
           "NOT_QUALIFIED_FOR_MANAGER_REVIEW")
    checks = approval.get("checks")
    _guard(isinstance(checks, dict), "MANAGER_CHECKS_MISSING")
    for name in REQUIRED_GATES:
        record = checks.get(name)
        _guard(isinstance(record, dict)
               and record.get("state") == "VERIFIED"
               and type(record.get("evidence_ref")) is str
               and re.fullmatch(r"[A-Za-z0-9_.:/-]{6,200}",
                                record["evidence_ref"]) is not None,
               "CORPORATE_" + name.upper() + "_UNVERIFIED")
    # Recalculate with the actual versioned deterministic scorer. Neither
    # model confidence nor an arbitrary score in approval can set priority.
    try:
        scoring_date = date.fromisoformat(approval["score_calculated_on"])
    except Exception:
        raise ManagerReadinessBlocked("MANAGER_SCORING_DATE_INVALID") from None
    _guard(0 <= (date.today() - scoring_date).days <= 2,
           "MANAGER_SCORING_TOO_OLD")
    try:
        recomputed = assess_lead(
            approval["company"], approval["signals"],
            checks={gate: "VERIFIED" for gate in REQUIRED_GATES},
            today=scoring_date,
        )
    except Exception:
        raise ManagerReadinessBlocked("MANAGER_SCORING_RECOMPUTE_FAILED") from None
    for key in ("lead_score", "score_version", "company_inn", "company_name",
                "region", "evidence_url", "signal_type", "why_now",
                "suggested_service", "identity_fingerprint"):
        _guard(assessment.get(key) == recomputed.get(key),
               "MANAGER_DETERMINISTIC_SCORE_MISMATCH")
    _guard(approval.get("manager_route_authorized") is True
           and approval.get("automated_outreach_allowed") is False
           and approval.get("source_review_authorized") is True
           and approval.get("global_dedup_verified") is True
           and approval.get("stop_list_verified") is True,
           "MANAGER_APPROVAL_MISSING")
    _guard(card.get(STOP_REASON) in (None, "")
           and card.get("stageId") == NEW_STAGE
           and card.get(REVIEW_DECISION) in SAFE_REVIEW_DECISIONS,
           "MANAGER_EDIT_OR_STOP_CONFLICT")
    _guard(str(card.get(COMPANY_INN)) == str(assessment.get("company_inn"))
           and card.get(COMPANY_NAME) == assessment.get("company_name")
           and card.get(REGION) == assessment.get("region")
           and card.get(SIGNAL_URL) == assessment.get("evidence_url"),
           "MANAGER_SOURCE_OR_COMPANY_MISMATCH")
    # Block synthetic hosts and injected URL credentials even when the
    # supplied approval JSON asserts a source was checked.
    try:
        url = urlsplit(card.get(SIGNAL_URL, ""))
        source_ok = (url.scheme == "https" and bool(url.hostname)
                     and not url.username and not url.password
                     and not url.hostname.endswith((".invalid", ".localhost"))
                     and url.port in (None, 443))
    except (ValueError, TypeError):
        source_ok = False
    _guard(source_ok, "MANAGER_PRIMARY_SOURCE_URL_INVALID")
    _guard(assessment.get("region") in ("Москва", "Московская область"),
           "MANAGER_MVP_REGION_NOT_ELIGIBLE")
    _guard(isinstance(card.get(SIGNAL_ID), str)
           and card[SIGNAL_ID] != "DEMO-ITREQ-001",
           "MANAGER_SIGNAL_NOT_VERIFIED")
    score = assessment.get("lead_score")
    version = assessment.get("score_version")
    _guard(type(score) is int and 60 <= score <= 100
           and type(version) is str and
           re.fullmatch(r"[A-Za-z0-9._-]{1,60}", version) is not None,
           "MANAGER_SCORE_NOT_ELIGIBLE")
    _guard(card.get(SCORE_FIELD) == score
           and card.get(VERSION_FIELD) == version
           and card.get(BADGE_FIELD) == score_label(score)
           and card.get(AI_STATUS) == QUALIFIED_AI_STATUS,
           "MANAGER_VERIFIED_SCORE_READBACK_REQUIRED")
    _guard(type(assessment.get("manager_script")) is str
           and 12 <= len(assessment["manager_script"]) <= 1500
           and type(assessment.get("suggested_service")) is str
           and 1 <= len(assessment["suggested_service"]) <= 150
           and type(assessment.get("why_now")) is str
           and 8 <= len(assessment["why_now"]) <= 500,
           "MANAGER_BRIEF_INCOMPLETE")
    target_role = approval.get("target_role")
    _guard(target_role in ("Руководитель", "Главный бухгалтер",
                           "IT-руководитель", "Менеджер по закупкам",
                           "Не определён"),
           "MANAGER_CONTACT_ROLE_NOT_VERIFIED")
    action = (
        "Проверить доказательства и подтвердить допустимый канал связи; "
        "обсудить потребность: " + assessment["suggested_service"] + ". "
        "Рекомендуемый первый сценарий: " + assessment["manager_script"]
    )
    _guard(len(action) <= 2000, "MANAGER_ACTION_TOO_LONG")
    return {
        "status": "READY_FOR_MANAGER_REVIEW",
        "crm_entity_type_id": TYPE, "category_id": CATEGORY,
        "item_id": item_id, "target_stage": MANAGER_STAGE,
        "lead_score": score, "score_version": version,
        "priority": "HIGH" if score >= 80 else "MEDIUM",
        "manager_card": {
            "company": assessment["company_name"],
            "region": assessment["region"],
            "signal": assessment["signal_type"],
            "why_now": assessment["why_now"],
            "service_to_offer": assessment["suggested_service"],
            "target_role": target_role,
            "next_action": action,
            "script": assessment["manager_script"],
            "can_contact_without_human_approval": False,
        },
        "proposed_fields": {
            "stageId": MANAGER_STAGE,
            MANAGER_NEXT: action,
            REVIEW_DECISION: "Готово к проверке менеджером; контакт только после его решения",
        },
        "creates_task": False,
        "crm_writes": 0,
        "outbound": 0,
    }


def manager_handoff_fingerprint(card, fields):
    """Immutable before-image for an eventual separately fenced write."""
    if not isinstance(card, dict) or not isinstance(fields, dict):
        raise ManagerReadinessBlocked("MANAGER_FINGERPRINT_INPUT_INVALID")
    return sha256(json.dumps(
        {"card": card, "fields": fields}, sort_keys=True,
        ensure_ascii=False, default=str, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
