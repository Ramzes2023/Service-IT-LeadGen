"""Recognize only the exact generated provisional Lead Score contract.

This is a labelled *unverified* signal rank, never contact authorization.
Used by the future nine-gate signed verifier solely to allow controlled
replacement of our own score; any partial/foreign/manual score stays frozen.
"""
from __future__ import annotations

from collections.abc import Mapping

from .serviceit_kanban_presentation import (
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, score_color,
)

VERSION = "svc-prelim-v1-FNS-UNVERIFIED"
DUP_STATUSES = ("ИНН НЕТ", "ТОЛЬКО ВИДИМЫЕ", "НЕ ПРОВЕРЕНЫ")

# Bitrix24 field UF_CRM_13_SVCIT_AI_SCORE_BADGE silently drops
# supplemental-plane (four-byte) colored circle emoji on our live portal.
# These distinct BMP symbols survive the same field's text validation.
SAFE_PRIORITY = ((80, "✅"), (60, "⚠"), (0, "❌"))


def safe_score_color(score):
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("PROVISIONAL_NUMBER_INVALID")
    return next(symbol for cutoff, symbol in SAFE_PRIORITY if score >= cutoff)


def provisional_payload(score, duplicate_status):
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("PROVISIONAL_NUMBER_INVALID")
    if duplicate_status not in DUP_STATUSES:
        raise ValueError("PROVISIONAL_DUPLICATE_STATUS_INVALID")
    text = (
        f"{safe_score_color(score)} {score}/100 · ПРЕДВАРИТЕЛЬНО · "
        f"ФНС: НЕ ПРОВЕРЕНА · Дубли: {duplicate_status} · "
        "STOP: СВОЙ СПИСОК · Контакт: НЕ РАЗРЕШЁН"
    )
    if len(text) > 200:
        raise ValueError("PROVISIONAL_BADGE_TOO_LONG")
    return {
        SCORE_FIELD: score,
        VERSION_FIELD: VERSION,
        BADGE_FIELD: text,
    }


def is_exact_own_provisional(item):
    """Neither a forged version alone nor a free-text label is enough."""
    if not isinstance(item, Mapping):
        return False
    score = item.get(SCORE_FIELD)
    if type(score) is not int or not 0 <= score <= 100:
        return False
    if item.get(VERSION_FIELD) != VERSION:
        return False
    badge = item.get(BADGE_FIELD)
    # Historical old-labelled provisional cards stay safely upgradable.
    def aliases(kind):
        colored = provisional_payload(score, kind)[BADGE_FIELD]
        # V1 already exists on live cards with a neutral magnifying-glass
        # icon. Keep exact legacy recognition for future safe upgrades,
        # but display priority colors on every NEW provisional score.
        legacy = colored.replace(safe_score_color(score), "🔎", 1)
        older_color = colored.replace(safe_score_color(score),
                                      score_color(score), 1)
        return (
            colored,
            older_color,
            older_color.replace("STOP: СВОЙ СПИСОК", "STOP: НЕ НАСТРОЕН"),
            colored.replace("STOP: СВОЙ СПИСОК", "STOP: НЕ НАСТРОЕН"),
            legacy,
            legacy.replace("STOP: СВОЙ СПИСОК", "STOP: НЕ НАСТРОЕН"),
        )
    return (type(badge) is str and any(
        badge in aliases(kind) for kind in DUP_STATUSES))


def is_score_empty_or_ours(item):
    if not isinstance(item, Mapping):
        return False
    # Missing fields are NOT evidence of empty and must never be overwritten.
    keys = (SCORE_FIELD, VERSION_FIELD, BADGE_FIELD)
    return (all(k in item and item[k] in (None, "") for k in keys)
            or is_exact_own_provisional(item))
