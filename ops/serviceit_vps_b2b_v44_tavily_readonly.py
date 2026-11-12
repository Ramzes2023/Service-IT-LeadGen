#!/usr/bin/env python3
"""V44 VPS-only Tavily readiness audit (NO Tavily queries, DB or CRM writes).

Never prints API key, file path, source content, URL, or client information.
"""
from __future__ import annotations
import os
import re
import sys

from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_b2b_tavily_fallback_v44 import (
    TABLE,MAX_MONTHLY_REQUESTS,permitted,
)


def out(k,v):
    assert re.fullmatch(r"[A-Z0-9_]{3,60}",k)
    assert re.fullmatch(r"(YES|NO|[0-9]{1,5})",str(v))
    print("V44_TAVILY_AUDIT_"+k+"="+str(v),flush=True)


def main():
    if len(sys.argv)!=1:
        return 2
    env=dict(os.environ)
    out("SOURCE_POLICY_READY","YES" if permitted(env) else "NO")
    out("SEARCH_OPTED_IN","YES" if
        env.get("SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED")=="YES" else "NO")
    out("SECRET_FILE_CONFIGURED","YES" if
        (env.get("SERVICEIT_TAVILY_SEARCH_API_KEY_FILE") or "").startswith("/run/secrets/")
        else "NO")
    try:
        key=secret_file(env,"SERVICEIT_TAVILY_SEARCH_API_KEY",optional=True)
    except Exception:
        key=""
    out("SECRET_VALID","YES" if
        isinstance(key,str) and re.fullmatch(r"tvly-[!-~]{16,200}",key)
        else "NO")
    with db(env) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            row=conn.execute("SELECT to_regclass(%s)",("public."+TABLE,)).fetchone()
            exists=row and row[0] is not None
            out("LEDGER_EXISTS","YES" if exists else "NO")
            if exists:
                used=conn.execute(f"""
                  SELECT count(*) FROM {TABLE}
                  WHERE attempted_at >= date_trunc('month',now())
                """).fetchone()[0]
                out("MONTHLY_RESERVED",min(99999,int(used)))
                out("MONTHLY_AVAILABLE",max(0,MAX_MONTHLY_REQUESTS-int(used)))
    out("PROVIDER_CALLS",0)
    out("CRM_WRITES",0)
    out("PG_WRITES",0)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        out("FAILED_SAFE","YES")
        raise SystemExit(2)
