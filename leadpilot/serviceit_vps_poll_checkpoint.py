"""Durable VPS poll heartbeat: detect missed seven-day recovery windows.

Bitrix's reviewed ID-only scanner intentionally examines at most 7 days.
A VPS outage longer than that MUST NOT be treated as an empty successful
poll, since older cards may have become invisible to the scanner.  Fail
closed and require an explicit scoped operator recovery plan.  No CRM
writes, no secrets, no customer data persisted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .serviceit_vps_pg_queue import VpsBlocked
from .serviceit_bitrix_poll_fallback import date_value

MAX_RECOVERY = timedelta(days=7)
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_poll_state (
    singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
    activation_at TIMESTAMPTZ NOT NULL,
    last_success_at TIMESTAMPTZ NOT NULL
);
"""


def migrate_poll(connection):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)


def check_gap(connection, activation_iso: str, now: datetime):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise VpsBlocked("VPS_POLL_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    activation = date_value(activation_iso)
    if activation > now:
        raise VpsBlocked("VPS_POLL_ACTIVATION_IN_FUTURE")
    with connection.cursor() as cur:
        cur.execute(
            "SELECT activation_at, last_success_at "
            "FROM serviceit_vps_poll_state WHERE singleton=1")
        row = cur.fetchone()
    if row is None:
        if now - activation > MAX_RECOVERY:
            raise VpsBlocked("VPS_POLL_INITIAL_GAP_TOO_OLD")
        return
    existing_activation, last = row
    if existing_activation.astimezone(timezone.utc) != activation:
        raise VpsBlocked("VPS_POLL_ACTIVATION_CHANGED_MANUAL_REVIEW")
    if (last > now + timedelta(minutes=2)
            or now - last > MAX_RECOVERY):
        raise VpsBlocked("VPS_POLL_RECOVERY_WINDOW_EXCEEDED")


def mark_success(connection, activation_iso: str, now: datetime):
    """Committed only after ID-only list and all queue inserts succeed."""
    activation = date_value(activation_iso)
    if now.tzinfo is None or not activation <= now:
        raise VpsBlocked("VPS_POLL_CHECKPOINT_TIME_INVALID")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO serviceit_vps_poll_state "
                "(singleton, activation_at, last_success_at) "
                "VALUES(1, %s, %s) "
                "ON CONFLICT (singleton) DO UPDATE "
                "SET last_success_at=EXCLUDED.last_success_at "
                "WHERE serviceit_vps_poll_state.activation_at=EXCLUDED.activation_at "
                "AND serviceit_vps_poll_state.last_success_at<=EXCLUDED.last_success_at "
                "RETURNING singleton",
                (activation, now))
            if cur.fetchone() is None:
                raise VpsBlocked("VPS_POLL_CHECKPOINT_CONFLICT")
