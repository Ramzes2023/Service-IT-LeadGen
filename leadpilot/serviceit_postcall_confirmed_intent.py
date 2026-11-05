"""Manager-confirmed post-call action contract for real ServiceIT 1062/21.

This is a deterministic proof/intent adapter. Corporate REST writer MUST verify
an exact completed activity live and a signed manager approval before writing.
AI transcript is never authoritative. No calls, messages, tasks, deal or card
writes happen in this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from .serviceit_b2b_hold_ai_fields import PREFIX

_ALLOWED = {
    "YES": ("SEND_PROPOSAL", "ASK_DETAILS"),
    "NO": ("DO_NOT_CONTACT",),
    "UNKNOWN": ("MANAGER_REVIEW",),
}
_FIELDS = frozenset({
    PREFIX + "MANAGER_NEXT_ACTION",
    PREFIX + "REVIEW_DECISION",
})
CUTOFF = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)


class PostCallBlocked(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise PostCallBlocked(message)


def _dt(s):
    need(type(s) is str and len(s) <= 40, "POSTCALL_DATE_MISSING")
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise PostCallBlocked("POSTCALL_DATE_INVALID") from None
    need(t.tzinfo is not None, "POSTCALL_DATE_TIMEZONE_REQUIRED")
    return t.astimezone(timezone.utc)


def _id(v):
    need(type(v) is int and 0 < v < 2_000_000_000, "POSTCALL_ID_INVALID")
    return v


def draft_verified_intent(card, activity, approval, *, now=None):
    """Strongly scoped intent; output is NOT permission to write CRM."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    need(type(card) is dict and type(activity) is dict
         and type(approval) is dict, "POSTCALL_INPUT_INVALID")
    cid = _id(card.get("id"))
    need(cid >= 8 and cid not in (3, 7)
         and card.get("entityTypeId") == 1062
         and card.get("categoryId") == 21, "POSTCALL_OUTSIDE_SCOPE")
    created = _dt(card.get("createdTime"))
    need(CUTOFF <= created <= now, "POSTCALL_CARD_NOT_NEW")
    uid = _id(card.get("assignedById"))
    need(approval.get("manager_confirmed") is True
         and approval.get("manager_user_id") == uid
         and approval.get("activity_id") == activity.get("ID"),
         "POSTCALL_MANUAL_CONFIRMATION_MISSING")
    _id(activity.get("ID"))
    need(str(activity.get("OWNER_ID")) == str(cid)
         and str(activity.get("OWNER_TYPE_ID")) == "1062"
         and str(activity.get("COMPLETED")) in ("Y", "1", "True")
         and str(activity.get("TYPE_ID")) == "2",  # Bitrix call activity
         "COMPLETED_CALL_NOT_PROVEN")
    finished = _dt(activity.get("LAST_UPDATED"))
    need(created <= finished <= now, "POSTCALL_OUTSIDE_ALLOWED_WINDOW")
    interest = approval.get("interest")
    need(interest in _ALLOWED, "POSTCALL_INTEREST_UNKNOWN")
    action = approval.get("next_action")
    need(action in _ALLOWED[interest], "POSTCALL_ACTION_CONFLICT")
    summary = approval.get("summary")
    need(type(summary) is str and 5 <= len(summary.strip()) <= 500
         and not any(ord(ch) < 32 or ord(ch) == 127 for ch in summary),
         "POSTCALL_SUMMARY_INVALID")
    source = approval.get("confirmation_ref")
    need(type(source) is str
         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:#-]{5,159}", source)
         is not None, "MANAGER_PROOF_REF_MISSING")
    date = approval.get("next_due_at")
    if action == "DO_NOT_CONTACT":
        need(date in (None, ""), "POSTCALL_NO_CONTACT_MUST_NOT_SCHEDULE")
        due = None
    else:
        due = _dt(date)
        need(now <= due <= now + timedelta(days=90),
             "POSTCALL_NEXT_ACTION_DATE_INVALID")
    # Do not turn completed-call metadata into a confirmed sale or proof of
    # consent. The future writer requires separately approved 1062/21 gates.
    payload = {
        PREFIX + "MANAGER_NEXT_ACTION": (
            action + (" · " + due.isoformat() if due else "")
        ),
        PREFIX + "REVIEW_DECISION": (
            "POSTCALL_MANAGER_CONFIRMED_" + interest
        ),
    }
    need(set(payload) == _FIELDS, "POSTCALL_FIELD_ALLOWLIST_MISMATCH")
    return {
        "status": "MANAGER_POSTCALL_INTENT_VALIDATED_NOT_WRITTEN",
        "card_id": cid,
        "activity_id": activity["ID"],
        "manager_user_id": uid,
        "interest": interest,
        "action": action,
        "next_due_at": due.isoformat() if due else None,
        "next_action_fields": payload,
        "summary": summary.strip(),
        "payment_verified": False,
        "deal_conversion_authorized": False,
        "outreach_authorized": False,
        "crm_writes": 0,
        "tasks_created": 0,
    }
