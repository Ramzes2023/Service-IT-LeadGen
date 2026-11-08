"""Native scanner cannot silently discard cards after a seven-day VPS outage.

Disposable CI PostgreSQL only; no Bitrix, model, key or corporate VPS.
"""
from datetime import datetime, timedelta, timezone
import os

import pytest
from leadpilot import serviceit_vps_pg_queue as pg
from leadpilot import serviceit_vps_poll_checkpoint as cp
from leadpilot.serviceit_vps_pg_queue import VpsBlocked


def test_missing_or_future_activation_cannot_report_success():
    now=datetime(2026,11,8,12,0,tzinfo=timezone.utc)
    class FakeConn:
        def cursor(self):
            raise AssertionError("NO_DB_ACCESS_REQUIRED")
    with pytest.raises(VpsBlocked, match="FUTURE"):
        cp.check_gap(FakeConn(), (now+timedelta(minutes=1)).isoformat(), now)


@pytest.mark.skipif(os.environ.get("SERVICEIT_PG_CI_EPHEMERAL")!="YES",
                    reason="requires disposable GitHub Actions PostgreSQL")
def test_real_postgres_checkpoint_never_hides_recovery_gap(monkeypatch):
    monkeypatch.setattr(pg, "secret_file",
                        lambda env,name: "synthetic-ci-pg-password")
    start=datetime(2026,11,8,12,0,tzinfo=timezone.utc)
    act=start-timedelta(minutes=5)
    with pg.db({}) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_poll_state")
        cp.migrate_poll(conn)
        cp.check_gap(conn, act.isoformat(), start)
        cp.mark_success(conn, act.isoformat(), start)
        cp.check_gap(conn, act.isoformat(), start+timedelta(days=6))
        with pytest.raises(VpsBlocked, match="WINDOW_EXCEEDED"):
            cp.check_gap(conn, act.isoformat(), start+timedelta(days=8))
        with pytest.raises(VpsBlocked, match="ACTIVATION_CHANGED"):
            cp.check_gap(conn, start.isoformat(), start+timedelta(hours=1))
        with pytest.raises(VpsBlocked, match="CHECKPOINT_CONFLICT"):
            cp.mark_success(conn, start.isoformat(), start+timedelta(hours=1))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM serviceit_vps_poll_state")
            assert cur.fetchone()[0] == 1
            cur.execute("DROP TABLE serviceit_vps_poll_state")
