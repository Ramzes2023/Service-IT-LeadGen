"""Verified-fact-first manager brief from existing offline lead review output.

AI only proposes hypotheses. The deterministic score is never replaced by an
AI confidence. No contact attempts, CRM calls, company lookups or persistence.
"""
from __future__ import annotations

from datetime import date
import math
from urllib.parse import urlsplit


class UnsafeBrief(ValueError):
    """The draft cannot be trusted as a human-review instruction."""


def _plain(value, *, limit=500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise UnsafeBrief("REQUIRED_TEXT_MISSING_OR_TOO_LONG")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise UnsafeBrief("CONTROL_CHARACTERS_FORBIDDEN")
    return value.strip()


def _source(signal: dict, checked: date) -> dict:
    if not isinstance(signal, dict):
        raise UnsafeBrief("SIGNAL_MISSING")
    url = _plain(signal.get("url"), limit=1000)
    u = urlsplit(url)
    if (u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment):
        raise UnsafeBrief("SOURCE_URL_NOT_VERIFIABLE")
    try:
        when = date.fromisoformat(signal["signal_date"])
    except (KeyError, TypeError, ValueError):
        raise UnsafeBrief("SIGNAL_DATE_INVALID") from None
    if not 0 <= (checked - when).days <= 90:
        raise UnsafeBrief("SIGNAL_STALE_OR_FUTURE")
    return {
        "source_name": _plain(signal.get("name"), limit=120),
        "url": url,
        "signal_date": when.isoformat(),
        "evidence": _plain(signal.get("evidence"), limit=2500),
        "kind": _plain(signal.get("signal_type"), limit=80),
        "origin": "SOURCE_TEXT_NOT_PURCHASE_CONFIRMATION",
    }


def _ai_hypothesis(ai: object, evidence: str) -> dict | None:
    if not isinstance(ai, dict) or ai.get("origin") != "AI_hypothesis_not_verified_need":
        return None
    confidence = ai.get("confidence")
    if (type(confidence) not in (float, int) or not math.isfinite(confidence)
            or not 0 < confidence <= 1):
        return None
    try:
        quote = _plain(ai.get("evidence_quote"), limit=300)
        need = _plain(ai.get("possible_need"), limit=400)
        service = _plain(ai.get("service"), limit=80)
    except UnsafeBrief:
        return None
    if quote not in evidence or service == "Требуется проверка":
        return None
    return {
        "origin": "AI_HYPOTHESIS_NOT_FACT", "need": need,
        "quoted_source_fragment": quote, "service_suggestion": service,
        "confidence": confidence, "confirmed_purchase_intent": False,
    }


def build_manager_workspace(brief: dict, *, checked_on: date | None = None) -> dict:
    """Prepare a manager-review card without authorizing any outreach or CRM writes.

    Input shape is leadpilot.manager_briefs.build_manager_briefs(...)["briefs"][i].
    AI classification is optional and NEVER changes deterministic lead score.
    """
    if not isinstance(brief, dict):
        raise UnsafeBrief("BRIEF_MISSING")
    today = checked_on or date.today()
    if not isinstance(today, date):
        raise UnsafeBrief("CHECK_DATE_INVALID")
    if (brief.get("crm_writes") != "NO" or brief.get("crm_ready") is not False
            or brief.get("outbound_authorized") is not False
            or brief.get("outreach") != "NOT_AUTHORIZED_BY_THIS_BRIEF"):
        raise UnsafeBrief("OUTREACH_OR_CRM_GATE_NOT_CLOSED")
    if (brief.get("holds") is None or not isinstance(brief["holds"], list)
            or not brief["holds"] or not all(isinstance(s, str) for s in brief["holds"])):
        raise UnsafeBrief("HUMAN_HOLDS_MISSING")
    if (brief.get("suppressed_channels") or brief.get("purchasing_intent") != "UNVERIFIED"
            or brief.get("business_size_verification") != "NOT_VERIFIED_BY_LEGACY_SCORE"):
        raise UnsafeBrief("MANAGER_BRIEF_POLICY_OR_EVIDENCE_CONFLICT")
    score = brief.get("lead_score")
    if type(score) is not int or not 0 <= score <= 100:
        raise UnsafeBrief("RULE_SCORE_INVALID")
    if brief.get("priority") not in ("high", "medium"):
        raise UnsafeBrief("PRIORITY_OUT_OF_SCOPE")
    source = _source(brief.get("source"), today)
    hypothesis = _ai_hypothesis(brief.get("ai_hypothesis"), source["evidence"])
    company = _plain(brief.get("company"), limit=140)
    inn = brief.get("inn")
    if not isinstance(inn, str) or not inn.isascii() or not inn.isdecimal() or len(inn) not in (10,12):
        raise UnsafeBrief("INN_INVALID")
    region = _plain(brief.get("region"), limit=100)
    service = _plain(brief.get("rule_based_service"), limit=140)
    score_version = _plain(brief.get("score_version"), limit=80)
    if any(x in company for x in ("\n", "@", "http://", "https://")):
        raise UnsafeBrief("COMPANY_NAME_UNSAFE")

    return {
        "mode": "MANAGER_PREPARATION_ONLY", "generated_on": today.isoformat(),
        "company": company, "inn": inn, "region": region,
        "source_evidence": source,
        "rule_based_recommendation": service,
        "score": {"value": score, "version": score_version,
                  "origin": "DETERMINISTIC_RULES_NOT_AI", "conversion_probability": "NOT_ESTIMATED"},
        "ai": hypothesis,
        "what_may_be_needed": hypothesis["need"] if hypothesis else "Требуется проверка менеджером; спрос не подтверждён",
        "why_now": "Публичный сигнал, а не подтверждённая покупка: " + source["evidence"][:300],
        "who_to_contact": "ЛПР не подтверждён; определять только из разрешённых данных",
        "first_conversation_outline": [
            "Представиться от имени Сервис ИТ и установить допустимость обращения",
            "Уточнить, актуальна ли возможная потребность, не утверждая её наличие",
            "Если интерес подтверждён, предложить оценку задач по направлению: " + service,
            "Зафиксировать согласованные итоги и следующее действие в CRM",
        ],
        "after_call_template": {
            "actual_contact": "NOT_RECORDED", "actual_interest": "NOT_RECORDED",
            "objections": "NOT_RECORDED", "next_action": "REQUIRES_MANAGER_INPUT",
            "next_due_on": None, "origin": "MANAGER_MUST_CONFIRM",
        },
        "required_checks_before_outreach": sorted(set(brief["holds"])),
        "manager_approval": "NOT_GRANTED", "customer_contact": "NO",
        "crm_writes": "NO", "source_connections": 0, "production_ready": False,
    }
