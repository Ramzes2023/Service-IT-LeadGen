"""Recognize only the exact generated provisional Lead Score contract.

This is a labelled *unverified* signal rank, never contact authorization.
Used by the future nine-gate signed verifier solely to allow controlled
replacement of our own score; any partial/foreign/manual score stays frozen.
"""
from __future__ import annotations

from collections.abc import Mapping

from .serviceit_kanban_presentation import (
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD,
)

VERSION = "svc-prelim-v1-FNS-UNVERIFIED"
DUP_STATUSES = ("ИНН НЕТ", "ТОЛЬКО ВИДИМЫЕ", "НЕ ПРОВЕРЕНЫ")


def provisional_payload(score, duplicate_status):
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("PROVISIONAL_NUMBER_INVALID")
    if duplicate_status not in DUP_STATUSES:
        raise ValueError("PROVISIONAL_DUPLICATE_STATUS_INVALID")
    text = (
        f"🔎 {score}/100 · ПРЕДВАРИТЕЛЬНО · "
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
    return (type(badge) is str and any(
        badge in (
            provisional_payload(score, kind)[BADGE_FIELD],
            provisional_payload(score, kind)[BADGE_FIELD].replace(
                "STOP: СВОЙ СПИСОК", "STOP: НЕ НАСТРОЕН"),
        ) for kind in DUP_STATUSES
    ))


def is_score_empty_or_ours(item):
    if not isinstance(item, Mapping):
        return False
    # Missing fields are NOT evidence of empty and must never be overwritten.
    keys = (SCORE_FIELD, VERSION_FIELD, BADGE_FIELD)
    return (all(k in item and item[k] in (None, "") for k in keys)
            or is_exact_own_provisional(item))
