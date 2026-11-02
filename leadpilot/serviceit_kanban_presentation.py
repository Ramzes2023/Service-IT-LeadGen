"""Source-agnostic score color for every qualified ServiceIT signal.

Presentation only. No LLM run, network, Bitrix writes, phone calls, or
marketing consent. Derives from the versioned deterministic score, never
from a GigaChat confidence value. The Bitrix field exists in CRM_13 but
this module does NOT activate a live delivery worker or mutate any record.
"""
from __future__ import annotations

from typing import Mapping

BADGE_FIELD = "UF_CRM_13_SVCIT_AI_SCORE_BADGE"
SCORE_FIELD = "UF_CRM_13_SVCIT_LEAD_SCORE"
VERSION_FIELD = "UF_CRM_13_SVCIT_SCORE_VERSION"
BANDS = (
    (80, "🟢", "высокий приоритет проверки"),
    (60, "🟡", "средний приоритет проверки"),
    (0, "🔴", "низкий приоритет проверки"),
)


class ScorePresentationRejected(ValueError):
    pass


def score_color(score: int) -> str:
    if type(score) is not int or not 0 <= score <= 100:
        raise ScorePresentationRejected("SCORE_OUT_OF_RANGE_OR_NOT_INT")
    return next(icon for cutoff, icon, _ in BANDS if score >= cutoff)


def score_label(score: int) -> str:
    if type(score) is not int or not 0 <= score <= 100:
        raise ScorePresentationRejected("SCORE_OUT_OF_RANGE_OR_NOT_INT")
    for cutoff, icon, title in BANDS:
        if score >= cutoff:
            return f"{icon} {score}/100 — {title}"
    raise ScorePresentationRejected("SCORE_OUT_OF_RANGE_OR_NOT_INT")


def fields_from_assessment(report: Mapping) -> dict:
    """A field map for a *future approved* CRM upsert, NOT an API request.

    This function has no eligibility gate; the upstream caller MUST first
    enforce corporate global dedupe, customer exclusion, source and legal
    verification, and CRM write approval. Formatting is not authorization.
    """
    if not isinstance(report, Mapping):
        raise ScorePresentationRejected("ASSESSMENT_INVALID")
    score = report.get("lead_score")
    version = report.get("score_version")
    if type(score) is not int or not 0 <= score <= 100:
        raise ScorePresentationRejected("SCORE_OUT_OF_RANGE_OR_NOT_INT")
    if not isinstance(version, str) or not 1 <= len(version) <= 60:
        raise ScorePresentationRejected("VERSION_MISSING")
    if report.get("outreach_authorized_by_this_module") is not False:
        raise ScorePresentationRejected("OUTREACH_SAFETY_NOT_PROVEN")
    if report.get("human_decision_required") is not True:
        raise ScorePresentationRejected("HUMAN_REVIEW_NOT_REQUIRED")
    if report.get("mode") not in ("SYNTHETIC_DEMO", "RESEARCH_OR_REVIEW"):
        raise ScorePresentationRejected("ASSESSMENT_MODE_INVALID")
    return {
        SCORE_FIELD: score,
        VERSION_FIELD: version,
        BADGE_FIELD: score_label(score),
    }


def decorate_assessment(report: Mapping) -> dict:
    """Add a ready-to-display badge to ALL assessments, including HOLD cases.

    The badge always displays priority of *checking evidence*, not permission
    to approach the company. Preserves all original report keys.
    """
    fields = fields_from_assessment(report)
    if "kanban_score_badge" in report and report["kanban_score_badge"] != fields[BADGE_FIELD]:
        raise ScorePresentationRejected("BADGE_CONFLICT")
    return {**dict(report), "kanban_score_badge": fields[BADGE_FIELD]}
