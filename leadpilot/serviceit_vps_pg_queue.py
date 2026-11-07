"""ServiceIT VPS Postgres event queue. No CRM writes, no AI and no lead sources.

Postgres is localhost-only on Mihail's dedicated host. The durable queue is
independent of old GitHub and SQLite ledgers. Every queued ID is scoped to
smart process 1062; actual CRM category 21 is re-checked by the exact worker.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import re

DB_HOST = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "leadgen"
DB_USER = "leadgen"
CARD_MIN = 8
CARD_MAX = 2_000_000_000

DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_new_cards (
    card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
    entity_type_id INTEGER NOT NULL DEFAULT 1062 CHECK(entity_type_id = 1062),
    status TEXT NOT NULL DEFAULT 'PENDING'
      CHECK(status IN ('PENDING', 'RUNNING', 'DONE', 'REVIEW', 'UNCERTAIN')),
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    last_result TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS serviceit_vps_status_idx
    ON serviceit_vps_new_cards(status, received_at);
"""


class VpsBlocked(ValueError):
    pass


def secret_file(env: dict, name: str, *, optional=False) -> str:
    """Read secrets from a mounted file, not Compose env or shell history."""
    value = env.get(name + "_FILE", "")
    if not value and optional:
        return ""
    if not isinstance(value, str) or not value.startswith("/run/secrets/"):
        raise VpsBlocked("SECRET_PATH_REQUIRED_" + name)
    path = Path(value)
    if path.is_symlink() or not path.is_file():
        raise VpsBlocked("SECRET_FILE_MISSING_" + name)
    if path.stat().st_size > 10000:
        raise VpsBlocked("SECRET_FILE_OVERSIZED_" + name)
    content = path.read_text("utf-8").strip()
    if not content or "\x00" in content or "\n" in content:
        raise VpsBlocked("SECRET_FILE_INVALID_" + name)
    return content


def postgres_settings(env: dict) -> dict:
    # Explicitly bound to the LOCAL database; never enable external
    # postgres or a Docker default bridge route by accident.
    for key, expected in (
        ("SERVICEIT_PG_HOST", DB_HOST),
        ("SERVICEIT_PG_PORT", str(DB_PORT)),
        ("SERVICEIT_PG_DB", DB_NAME),
        ("SERVICEIT_PG_USER", DB_USER),
    ):
        if env.get(key, expected) != expected:
            raise VpsBlocked("PG_LOCAL_ONLY_CONFIGURATION_REQUIRED")
    return {
        "host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME,
        "user": DB_USER, "password": secret_file(env, "SERVICEIT_PG_PASSWORD"),
        "connect_timeout": 5, "application_name": "serviceit_vps",
    }


@contextmanager
def db(env: dict):
    # psycopg is in server-only requirements, not the Windows user tools.
    import psycopg
    with psycopg.connect(**postgres_settings(env)) as connection:
        yield connection


def migrate(connection):
    """Schema creation only, with no reads or changes to existing tables."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)


def insert_new(connection, card_id: int) -> bool:
    if type(card_id) is not int or not CARD_MIN <= card_id <= CARD_MAX:
        raise VpsBlocked("CARD_SCOPE_INVALID")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO serviceit_vps_new_cards(card_id) VALUES (%s) "
                "ON CONFLICT(card_id) DO NOTHING RETURNING card_id",
                (card_id,))
            return cur.fetchone() is not None


def claim_one(connection):
    """Postgres atomic cross-process claim; no implicit retry after crash."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                WITH selected AS (
                  SELECT card_id FROM serviceit_vps_new_cards
                  WHERE status='PENDING' AND attempts=0
                  ORDER BY received_at, card_id
                  FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE serviceit_vps_new_cards q
                SET status='RUNNING', attempts=q.attempts+1,
                    claimed_at=now(), last_result='RESERVED_NO_BLIND_RETRY'
                FROM selected
                WHERE q.card_id=selected.card_id
                RETURNING q.card_id
            """)
            row = cur.fetchone()
            return int(row[0]) if row else None


def finalize(connection, card_id: int, status: str, reason: str):
    if status not in ("DONE", "REVIEW", "UNCERTAIN"):
        raise VpsBlocked("INVALID_FINAL_STATE")
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{2,90}", reason):
        raise VpsBlocked("UNSAFE_FINAL_REASON")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE serviceit_vps_new_cards SET status=%s, finished_at=now(), "
                "last_result=%s WHERE card_id=%s AND status='RUNNING' "
                "AND attempts=1 RETURNING card_id",
                (status, reason, card_id))
            if cur.fetchone() is None:
                raise VpsBlocked("PG_RESULT_STATE_UNCERTAIN")


def metrics(connection) -> dict:
    with connection.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM serviceit_vps_new_cards GROUP BY status")
        rows = cur.fetchall()
    return {str(key): int(value) for key, value in rows}


def reconcile_stale(connection):
    """Crash recovery NEVER re-runs external model calls or CRM updates."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_new_cards
                SET status='UNCERTAIN', finished_at=now(),
                    last_result='CRASH_OR_WORKER_TIMEOUT_MANUAL_REVIEW'
                WHERE status='RUNNING' AND claimed_at < now()-INTERVAL '20 minutes'
            """)
            return cur.rowcount
