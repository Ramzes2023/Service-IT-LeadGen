"""One safe Kanban-facing header for every new ServiceIT signal.

Only presentation in an EXISTING AI_SUMMARY field. No title edits, no CRM/UI
configuration writes, no Score calculation and no source verification claims.
"""
from __future__ import annotations

import re
from typing import Mapping

from .serviceit_three_signal_triage import BADGES, DIRECT

DISPLAY_BADGES = {**BADGES, DIRECT: "🎯 ПРЯМАЯ УСЛУГА"}
ALL_PREFIXES = tuple(sorted(set(BADGES.values()) | set(DISPLAY_BADGES.values()), key=len, reverse=True))
DEFAULT_TITLE = "Сигнал требует проверки"


def tile_heading_for_signal(assessment: Mapping, title: object) -> str:
    """Build a compact, escaped-text-only headline from preliminary triage.

    A signal label is *not* evidence that this is a qualified purchase lead.
    Conflicting/missing type always receives the neutral REVIEW badge.
    """
    if not isinstance(assessment, Mapping):
        raise ValueError("KANBAN_ASSESSMENT_INVALID")
    kind = assessment.get("type")
    if kind not in DISPLAY_BADGES:
        raise ValueError("KANBAN_SIGNAL_TYPE_INVALID")
    badge = DISPLAY_BADGES[kind]
    if type(title) is not str or len(title) > 260 or any(
        ord(ch) < 32 or ord(ch) == 127 for ch in title
    ) or any(x in title for x in ("<", ">", "@", "{", "}")):
        safe = DEFAULT_TITLE
    else:
        safe = " ".join(title.strip().split())
        if "://" in safe or not safe:
            safe = DEFAULT_TITLE
        for mark in ALL_PREFIXES:
            if safe.startswith(mark):
                safe = safe[len(mark):].lstrip(" ·:—-")
                break
        safe = safe or DEFAULT_TITLE
    if len(safe) > 95:
        safe = safe[:94].rstrip() + "…"
    return badge + " · " + safe
