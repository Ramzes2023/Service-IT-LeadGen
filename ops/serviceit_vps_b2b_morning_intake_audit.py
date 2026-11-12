"""Read-only trace of public B2B tender IDs from morning mail."""
import os
import re

import httpx

from leadpilot.serviceit_vps_b2b_bridge import (
    db, enabled, exact_duplicate, positive_duplicate_control, read_candidate,
    secret_file, validate_webhook,
)

TENDERS = (4678279, 4677923, 4677404, 4677160, 4676643, 4677837, 4674919, 4679263)


def emit(key, value):
    value = str(value)
    if re.fullmatch(r"[A-Z0-9_]+", value):
        print(f"INTAKE_{key}={value}", flush=True)


def main():
    env = dict(os.environ)
    emit("WRITER_ENABLED", "YES" if enabled(env, writer=True) else "NO")
    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT state,count(*)
            FROM serviceit_vps_b2b_inbox
            WHERE received_at >= TIMESTAMPTZ '2026-11-12 00:00:00 Europe/Rome'
            GROUP BY state
        """).fetchall()
        emit("TODAY_RECEIVED", sum(count for _, count in rows))
        for state, count in rows:
            emit("TODAY_" + state, count)
        for tid in TENDERS:
            row = conn.execute("SELECT state,card_id FROM serviceit_vps_b2b_inbox WHERE source_id=%s",
                               (f"B2B-CENTER-{tid}",)).fetchone()
            emit(f"TENDER_{tid}_PG", row[0] if row else "ABSENT")
            if row and row[1]:
                emit(f"TENDER_{tid}_PG_CARD", row[1])
        emit("PENDING", "YES" if read_candidate(conn) else "NO")
    base = validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        emit("BITRIX_DUPLICATE_CONTROL", "YES" if positive_duplicate_control(client, base) else "NO")
        for tid in TENDERS:
            try:
                matches = exact_duplicate(client, base, f"B2B-CENTER-{tid}")
            except Exception as exc:
                emit(f"TENDER_{tid}_CRM", "READ_UNVERIFIED")
                emit(f"TENDER_{tid}_CRM_ERROR", type(exc).__name__.upper())
                continue
            emit(f"TENDER_{tid}_CRM_COUNT", len(matches))
            if len(matches) == 1:
                emit(f"TENDER_{tid}_CRM_CARD", matches[0]["id"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("AUDIT_ERROR", str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{3,70}", str(exc))
             else type(exc).__name__.upper())
        raise SystemExit(2) from None
