"""Integration test against *disposable* GitHub Actions postgres:16 only.

Never connects to corporate VPS, real Bitrix, Github ledger or external AI.
Uses synthetic database password and reuses same SQL DDL/transactions.
"""
import os
import pytest

from leadpilot import serviceit_vps_pg_queue as pg


@pytest.mark.skipif(os.environ.get("SERVICEIT_PG_CI_EPHEMERAL") != "YES",
                    reason="only run with explicitly disposable Postgres service")
def test_real_postgresql_schema_unique_queue_claim_and_uncertain(monkeypatch):
    monkeypatch.setattr(pg, "secret_file", lambda env, name: "synthetic-ci-pg-password")
    with pg.db({}) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_new_cards")
        pg.migrate(conn)
        assert pg.insert_new(conn, 101) is True
        assert pg.insert_new(conn, 101) is False
        assert pg.insert_new(conn, 102) is True
        assert pg.metrics(conn)["PENDING"] == 2

        claimed = pg.claim_one(conn)
        assert claimed == 101
        # Atomic claim is terminal once handed out, even if no CRM call made.
        assert pg.claim_one(conn) == 102
        assert pg.claim_one(conn) is None

        pg.finalize(conn, 101, "DONE", "EXACT_WORKER_ACK")
        pg.finalize(conn, 102, "UNCERTAIN", "EXTERNAL_OUTCOME_UNKNOWN")
        assert pg.metrics(conn)["DONE"] == 1
        assert pg.metrics(conn)["UNCERTAIN"] == 1

        with pytest.raises(pg.VpsBlocked):
            pg.finalize(conn, 101, "DONE", "EXACT_WORKER_ACK")
        assert pg.claim_one(conn) is None

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_new_cards")
