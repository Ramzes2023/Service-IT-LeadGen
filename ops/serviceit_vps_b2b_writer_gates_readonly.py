"""Run the actual installed writer checks in a read-only transaction."""
import os
import re
import httpx

from leadpilot.serviceit_vps_b2b_bridge import (
    enabled, db, secret_file, preflight, ReadOnlyBitrix, validate_webhook,
    positive_duplicate_control, read_candidate, attempted_24h, MAX_PER_24H,
)

def out(key, value):
    print(f"DOSSIER_READBACK_{key}={value}", flush=True)

def main():
    phase = "CONFIG"
    try:
        env = dict(os.environ)
        assert enabled(env, writer=True)
        out("WRITER_CONFIG", "YES")
        phase = "POSTGRESQL"
        with db(env) as conn, conn.transaction(), httpx.Client(
                trust_env=False, follow_redirects=False, timeout=20) as client:
            conn.execute("SET TRANSACTION READ ONLY")
            out("PENDING_VISIBLE", "YES" if read_candidate(conn) else "NO")
            out("BUDGET_AVAILABLE", "YES" if attempted_24h(conn) < MAX_PER_24H else "NO")
            phase = "CRM_PREFLIGHT"
            webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
            preflight(ReadOnlyBitrix(webhook, client=client))
            out("CRM_PREFLIGHT", "YES")
            phase = "CRM_DUPLICATE_CONTROL"
            assert positive_duplicate_control(client, validate_webhook(webhook))
            out("CRM_DUPLICATE_CONTROL", "YES")
    except Exception as error:
        out("BLOCKED_AT", phase)
        code = str(error)
        out("BLOCK_CODE", code if re.fullmatch(r"[A-Z][A-Z0-9_]{4,70}", code)
            else type(error).__name__.upper())
    out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
