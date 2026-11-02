"""Independent Gmail -> Postgres source worker for Service IT Lead Engine 2.0.

Worker never contacts Bitrix24, GitHub issues or GigaChat. It only populates a
durable, unqualified review store. Auto-poll requires an explicit environment
switch and separate owner-granted Gmail OAuth readonly credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

from .b2b_email_opportunity_bridge import InputRejected
from .serviceit_v2_gmail_api import GmailSourceBlocked, from_environment
from .serviceit_v2_store import (
    V2StoreBlocked, contains_message, ingest_message, init_schema, read_status,
    record_message_failure,
)

def _connect():
    try:
        import psycopg
    except ImportError:
        raise V2StoreBlocked("PSYCOPG_NOT_INSTALLED") from None
    dsn = os.environ.get("SERVICEIT_V2_DB_DSN", "")
    if not dsn or len(dsn) > 2048:
        raise V2StoreBlocked("DATABASE_CONFIGURATION_MISSING")
    try:
        return psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    except psycopg.Error:
        raise V2StoreBlocked("DATABASE_CONNECTION_UNAVAILABLE") from None


def poll_once(conn, gmail, *, max_messages=60, days=14):
    init_schema(conn)
    totals = {"new_messages": 0, "duplicate_messages": 0,
              "new_opportunities": 0, "revised_opportunities": 0,
              "it_opportunities_new_or_revised": 0, "out_of_scope": 0,
              "crm_writes": 0, "qualified_leads": 0, "rejected_messages": 0}
    for mid, raw in gmail.recent_messages(
            max_messages=max_messages, lookback_days=days,
            should_fetch=lambda mid: not contains_message(conn, mid)):
        try:
            one = ingest_message(conn, mid, raw)
        except InputRejected:
            # Bad sender/body is not an approved IT source; do not mark as read
            # or mark as committed. Retry and investigate format on next poll.
            totals["rejected_messages"] += 1
            record_message_failure(conn, mid)
            continue
        for key, val in one.items():
            totals[key] += val
    conn.execute("""
      INSERT INTO serviceit_v2_runtime(name,value)
      VALUES ('gmail_last_success_utc',to_char(now() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'))
      ON CONFLICT(name) DO UPDATE SET value=EXCLUDED.value,updated_at=now()
    """)
    totals["status"] = "GMAIL_TO_POSTGRES_RESEARCH_ONLY"
    totals["crm_writes"] = 0
    return totals


def main(argv=None):
    p = argparse.ArgumentParser(description="ServiceIT V2 Gmail readonly worker")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    mode.add_argument("--status", action="store_true")
    p.add_argument("--every-seconds", type=int, default=300)
    p.add_argument("--lookback-days", type=int, default=14)
    p.add_argument("--max-messages", type=int, default=60)
    args = p.parse_args(argv)
    try:
        if args.status:
            with _connect() as conn:
                init_schema(conn)
                print(json.dumps(read_status(conn), ensure_ascii=False), flush=True)
            return 0
        if os.environ.get("SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED") != "YES":
            raise V2StoreBlocked("SOURCE_NOT_EXPLICITLY_ENABLED")
        if not (60 <= args.every_seconds <= 3600):
            raise V2StoreBlocked("POLL_PERIOD_UNSAFE")
        if not (1 <= args.lookback_days <= 30 and 1 <= args.max_messages <= 100):
            raise V2StoreBlocked("POLL_BUDGET_UNSAFE")
        with _connect() as conn, httpx.Client(trust_env=False, follow_redirects=False) as http:
            gmail = from_environment(http)
            while True:
                try:
                    result = poll_once(conn, gmail, max_messages=args.max_messages,
                                       days=args.lookback_days)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                except (GmailSourceBlocked, V2StoreBlocked, InputRejected,
                        httpx.HTTPError, ValueError):
                    print(json.dumps({"status": "SOURCE_POLL_BLOCKED",
                                      "crm_writes": 0, "source_secret_exposed": False}),
                          flush=True)
                    if args.once:
                        return 2
                if args.once:
                    return 0
                time.sleep(args.every_seconds)
    except (GmailSourceBlocked, V2StoreBlocked, httpx.HTTPError, ValueError):
        print(json.dumps({"status": "SERVICEIT_V2_CORE_BLOCKED",
                          "crm_writes": 0, "source_secret_exposed": False}),
              flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
