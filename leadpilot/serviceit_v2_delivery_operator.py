"""Safe operator CLI for Service IT V2 outbox: disabled by default.

No background deployment is performed by this CLI. Never prints credentials,
sends mail or promotes a qualified lead.
"""
from __future__ import annotations
import argparse
import json
import os

import httpx

from .serviceit_v2_store import init_schema
from .serviceit_v2_worker import _connect, V2StoreBlocked
from .serviceit_v2_delivery import (
    V2DeliveryBlocked, stage_one_approved, process_one, reconcile_one,
)

def run(argv=None):
    p = argparse.ArgumentParser(description="ServiceIT V2 approved HOLD outbox")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--stage-one", metavar="TENDER_ID")
    mode.add_argument("--deliver-once", action="store_true")
    mode.add_argument("--reconcile-one", metavar="TENDER_ID")
    p.add_argument("--approval-ref", default="")
    args = p.parse_args(argv)
    try:
        with _connect() as conn:
            init_schema(conn)
            if args.status:
                states = conn.execute("""
                    SELECT state, count(*) FROM serviceit_v2_crm_outbox
                    GROUP BY state ORDER BY state
                """).fetchall()
                result = {"mode": "READ_ONLY_STATUS", "states": dict(states),
                          "crm_writes": 0}
            elif args.stage_one:
                if os.environ.get("SERVICEIT_V2_HOLD_STAGING_APPROVED") != "YES":
                    raise V2DeliveryBlocked("HOLD_STAGING_NOT_APPROVED")
                result = stage_one_approved(
                    conn, args.stage_one, approval_ref=args.approval_ref)
            else:
                if os.environ.get("SERVICEIT_V2_CRM_WRITES_APPROVED") != "YES":
                    raise V2DeliveryBlocked("CORPORATE_CRM_ACCESS_NOT_APPROVED")
                webhook = os.environ.pop("SERVICEIT_V2_BITRIX_WEBHOOK", "")
                with httpx.Client(trust_env=False, follow_redirects=False) as http:
                    if args.deliver_once:
                        result = process_one(
                            conn, http, webhook, commit_live=True)
                    else:
                        result = reconcile_one(conn, http, webhook,
                                               args.reconcile_one)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if not str(result["status"]).startswith(
            "CRM_POST_UNCERTAIN") else 3
    except (V2StoreBlocked, V2DeliveryBlocked, httpx.HTTPError,
            ValueError, TypeError, OSError):
        print(json.dumps({"status": "V2_CRM_OPERATION_BLOCKED_OR_UNCERTAIN",
                          "never_blind_retry": True,
                          "secret_exposed": False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
