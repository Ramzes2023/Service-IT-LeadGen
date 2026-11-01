"""ServiceIT AI Lead: reusable evidence -> score -> safety -> manager brief.

Pure domain engine for *many* source signals; no network, no CRM writes,
no calls, and no fabricated company contacts. The same result can feed a
sandbox smart-process demo or, after a separate corporate authorization and
real-CRM safety review, an operator-managed production adapter.

Only explicit VERIFIED clearance is considered verification; a high score
never overrides an existing client, CRM duplicate, stop list, stale evidence
or missing authorization. A model hypothesis never modifies Lead Score.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import re
from typing import Mapping

from .core import score_company, SCORE_VERSION, SERVICES, normalize_inn
from .bitrix_synthetic_text_guard import synthetic_action_is_safe

REGIONS = ("Москва", "Московская область", "Ингушетия")
APPROVED_STATES = frozenset({"VERIFIED", "BLOCKED", "UNKNOWN"})
MAX_SIGNALS = 12


class QualificationBlocked(ValueError):
    """Untrusted input or contradictory data; no external action occurred."""


def _stop(code: str) -> None:
    raise QualificationBlocked(code)


def _clearance(gates: Mapping, key: str) -> str:
    state = gates.get(key, "UNKNOWN")
    if not isinstance(state, str) or state not in APPROVED_STATES:
        _stop("GATE_STATE_INVALID")
    return state


def assess_lead(
    company: dict, signals: list[dict], *,
    checks: dict, today: date,
    ai_hypothesis: dict | None = None,
    demo: bool = False,
) -> dict:
    """Produce one explainable manager decision per company from its signals.

    All company/stop/CRM checks are caller-provided evidence; the function
    explicitly DOES NOT claim to have read corporate Bitrix client records.
    Unverified real-life check values MUST remain UNKNOWN.
    """
    if not isinstance(company, dict) or not isinstance(checks, dict):
        _stop("INPUT_INVALID")
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS:
        _stop("SIGNALS_MISSING_OR_TOO_MANY")
    if type(today) is not date:
        _stop("TODAY_INVALID")
    try:
        inn = normalize_inn(company.get("inn", ""))
    except (TypeError, ValueError):
        _stop("INN_INVALID")
    if not demo and not re.fullmatch(r"[0-9]{10}|[0-9]{12}", inn):
        _stop("INN_INVALID")
    name = company.get("name")
    region = company.get("region")
    industry = company.get("industry", "")
    if (not isinstance(name, str) or not 2 <= len(name) <= 120
            or not isinstance(industry, str) or len(industry) > 140
            or region not in REGIONS):
        _stop("COMPANY_PROFILE_INVALID")
    base = {
        "region": region, "industry": industry,
        "phone": company.get("phone", "") if isinstance(company.get("phone", ""), str) else "",
        "email": company.get("email", "") if isinstance(company.get("email", ""), str) else "",
    }
    normalized = []
    seen = set()
    for pos,sig in enumerate(signals,1):
        if not isinstance(sig, dict):
            _stop("SIGNAL_INVALID")
        type_name = sig.get("signal_type")
        if type_name not in SERVICES or type_name == "unknown":
            _stop("SIGNAL_TYPE_NOT_QUALIFIABLE")
        proof = sig.get("source_url")
        observed = sig.get("signal_date")
        why = sig.get("evidence")
        if (not isinstance(proof, str) or not proof.startswith("https://")
                or not 9 <= len(proof) <= 512 or not isinstance(why, str)
                or not 8 <= len(why) <= 1200):
            _stop("SIGNAL_EVIDENCE_MISSING")
        try:
            when = date.fromisoformat(observed)
        except (TypeError, ValueError):
            _stop("SIGNAL_DATE_INVALID")
        if when > today or (today - when).days > 365:
            _stop("SIGNAL_DATE_OUT_OF_RANGE")
        key = (proof, type_name)
        if key in seen:
            _stop("SIGNAL_DUPLICATE_IN_BATCH")
        seen.add(key)
        normalized.append({
            "id": pos,
            "signal_type": type_name,
            "signal_date": when.isoformat(),
            "source_url": proof,
            "evidence": why,
        })
    score,service,factors = score_company(base,normalized,today)
    required = ("company_registry", "source", "existing_customer",
                "crm_dedup", "stop_list", "legal_basis", "contact")
    statuses = {k:_clearance(checks,k) for k in required}
    # A BLOCKED existing_customer means this company must never be marketed
    # as a fresh account. BLOCKED can mean an affirmative stop/no legal basis.
    blocking = sorted(k for k,v in statuses.items() if v == "BLOCKED")
    unknown = sorted(k for k,v in statuses.items() if v == "UNKNOWN")
    freshest = min((today-date.fromisoformat(x["signal_date"])).days for x in normalized)
    if freshest > 30:
        blocking.append("stale_signal")
    ai_status = "NOT_RUN"
    hypothesis_text = ""
    if ai_hypothesis is not None:
        if (not isinstance(ai_hypothesis,dict)
                or ai_hypothesis.get("origin") != "AI_hypothesis"
                or ai_hypothesis.get("quality_status") != "complete"
                or not isinstance(ai_hypothesis.get("evidence_quote"),str)
                or not isinstance(ai_hypothesis.get("possible_need"),str)
                or not ai_hypothesis["evidence_quote"].strip()
                or len(ai_hypothesis["possible_need"]) > 400):
            ai_status = "REQUIRES_HUMAN_REVIEW"
        else:
            ai_status = "HYPOTHESIS_NOT_FACT"
            hypothesis_text = ai_hypothesis["possible_need"][:200]
    # Real business handoff requires all seven *independent* verifications.
    verified = not blocking and not unknown and not demo
    qualified = verified and score >= 60
    if blocking:
        status = "HOLD_BLOCKED"
    elif unknown:
        status = "HOLD_UNVERIFIED"
    elif demo:
        status = "DEMO_REVIEW_ONLY"
    elif not qualified:
        status = "LOW_PRIORITY"
    else:
        status = "MANAGER_REVIEW_REQUIRED"
    # A manager brief is safe and useful even if all leads are on HOLD,
    # but cannot be interpreted as an outbound-contact authorization.
    highest = max(normalized,key=lambda s: (
        {"1c_vacancy":25,"new_store":25,"it_vacancy":20,
         "procurement":20,"crm_need":20}[s["signal_type"]],
        s["signal_date"],
    ))
    script = (
        "Сначала проверить источник и право контакта. "
        "При подтверждённой потребности уточнить текущую 1С, сроки, подрядчика и бюджет."
    )
    fp = sha256((inn + "\x00" + highest["source_url"] + "\x00" +
                 highest["signal_type"]).encode()).hexdigest()[:24]
    return {
        "mode": "SYNTHETIC_DEMO" if demo else "RESEARCH_OR_REVIEW",
        "company_inn": inn, "company_name": name,
        "region": region, "evidence_url": highest["source_url"],
        "signal_type": highest["signal_type"],
        "evidence_date": highest["signal_date"],
        "why_now": highest["evidence"][:500],
        "lead_score": score, "score_version": SCORE_VERSION,
        "score_factors": factors, "suggested_service": service,
        "ai_status": ai_status, "ai_possible_need": hypothesis_text,
        "manager_script": script,
        "checks": statuses, "blocking_checks": blocking,
        "unverified_checks": unknown,
        "status": status,
        "identity_fingerprint": fp,
        "source_count": len(normalized),
        "crm_handoff_eligible": qualified,
        "human_decision_required": True,
        "outreach_authorized_by_this_module": False,
        "bitrix_write_performed": False,
        "customer_contacted": False,
    }


def plan_serviceit_ai_signals(assessments: list[dict]) -> dict:
    """Generic 1062 smart-process integration plan (NO network/no writes).

    Real leads have no corporate-global dedupe guarantee merely from this
    planning result. Existing source-specific connector must enforce that
    independently before a production write adapter can be authorized.
    """
    if not isinstance(assessments,list) or len(assessments)>250:
        _stop("BATCH_INVALID")
    plans=[]
    unique=set()
    for report in assessments:
        if not isinstance(report,dict) or report.get("mode") not in (
                "SYNTHETIC_DEMO","RESEARCH_OR_REVIEW"):
            _stop("ASSESSMENT_INVALID")
        fingerprint=report.get("identity_fingerprint")
        if not isinstance(fingerprint,str) or not re.fullmatch(r"[a-f0-9]{24}",fingerprint):
            _stop("ASSESSMENT_FINGERPRINT_INVALID")
        if fingerprint in unique:
            _stop("REPEATED_SIGNAL_IN_BATCH")
        unique.add(fingerprint)
        plans.append({
            "fingerprint":fingerprint, "score":report["lead_score"],
            "score_version":report["score_version"],
            "status":report["status"],
            "proposed_service":report["suggested_service"],
            "crm_entity_type_id":1062, "crm_category_id":21,
            "target_process":"Сервис ИТ — AI-сигналы",
            "crm_operation":"NOT_SENT_REQUIRES_SEPARATE_REVIEW",
            "demo_only":report["mode"]=="SYNTHETIC_DEMO",
            "global_customer_dedup_proven":False,
        })
    return {"status":"SERVICEIT_AI_SIGNALS_PIPELINE_PLANNED",
            "items":plans, "candidates":len(plans),
            "crm_writes":"NO", "outbound_contact":"NO",
            "automatic_production_worker":False}
