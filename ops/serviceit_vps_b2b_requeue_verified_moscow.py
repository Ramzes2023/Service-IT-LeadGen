"""Requeue one real unposted B2B notice after the public Moscow gate passes."""
import json
import os

import httpx

from leadpilot.serviceit_b2b_moscow_geo import check_public_procurement_location
from leadpilot.serviceit_vps_b2b_bridge import db, enabled, migrate_geo_retry


def main():
    env = dict(os.environ)
    if not enabled(env, writer=True) or env.get("SERVICEIT_B2B_MOSCOW_ONLY_ENABLED") != "YES":
        raise SystemExit("GEO_REQUEUE_DISABLED")
    source = "B2B-CENTER-4677923"
    with db(env) as conn, httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        row = conn.execute("""
            SELECT notice_json, state FROM serviceit_vps_b2b_inbox
            WHERE source_id=%s AND write_started_at IS NULL AND card_id IS NULL
        """, (source,)).fetchone()
        if not row or row[1] != "REVIEW":
            print("GEO_REQUEUE=ALREADY_HANDLED")
            return
        decision = check_public_procurement_location(row[0], client)
        print("GEO_REQUEUE_PUBLIC_LOCATION=" + decision)
        if decision != "MOSCOW":
            print("GEO_REQUEUE=EVIDENCE_UNAVAILABLE")
            return
        migrate_geo_retry(conn)
        with conn.transaction():
            updated = conn.execute("""
                UPDATE serviceit_vps_b2b_inbox
                SET state='PENDING', completed_at=NULL, geo_retry_at=NULL, geo_attempts=0
                WHERE source_id=%s AND state='REVIEW' AND write_started_at IS NULL
                  AND card_id IS NULL AND notice_json=%s::jsonb
                RETURNING source_id
            """, (source, json.dumps(row[0]))).fetchone()
        print("GEO_REQUEUE=" + ("QUEUED" if updated else "CHANGED_CONCURRENTLY"))


if __name__ == "__main__":
    main()
