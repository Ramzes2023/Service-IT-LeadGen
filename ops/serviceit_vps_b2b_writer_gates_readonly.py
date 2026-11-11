"""Run the actual installed writer checks in a read-only transaction."""
import os
import re
import httpx
from leadpilot import serviceit_b2b_positive_control as control

from leadpilot.serviceit_vps_b2b_bridge import (
    enabled, db, secret_file, preflight, ReadOnlyBitrix, validate_webhook,
    positive_duplicate_control, read_candidate, attempted_24h, MAX_PER_24H,
)

def out(key, value):
    print(f"DOSSIER_READBACK_{key}={value}", flush=True)

def main():
    phase = "CONFIG"
    responses = []
    def capture(response):
        method = response.request.url.path.rsplit("/", 1)[-1].split(".json")[0]
        if method in ("crm.item.get", "crm.item.list"):
            responses.append((method.replace(".", "_").upper(), response.status_code))
    try:
        env = dict(os.environ)
        assert enabled(env, writer=True)
        out("WRITER_CONFIG", "YES")
        phase = "POSTGRESQL"
        with db(env) as conn, conn.transaction(), httpx.Client(
                trust_env=False, follow_redirects=False, timeout=20,
                event_hooks={"response": [capture]}) as client:
            conn.execute("SET TRANSACTION READ ONLY")
            out("PENDING_VISIBLE", "YES" if read_candidate(conn) else "NO")
            out("BUDGET_AVAILABLE", "YES" if attempted_24h(conn) < MAX_PER_24H else "NO")
            phase = "CRM_PREFLIGHT"
            webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
            preflight(ReadOnlyBitrix(webhook, client=client))
            out("CRM_PREFLIGHT", "YES")
            phase = "CRM_DUPLICATE_CONTROL"
            for card in (3, 17, 15, 13, 11):
                control.CANARY_ID = card
                responses.clear()
                try:
                    assert positive_duplicate_control(client, validate_webhook(webhook))
                    out(f"CANARY_{card}", "YES")
                except Exception as error:
                    out(f"CANARY_{card}", "NO")
                    code = str(error)
                    out(f"CANARY_{card}_CODE", code if re.fullmatch(r"[A-Z][A-Z0-9_]{4,70}", code)
                        else type(error).__name__.upper())
                for index, (method, status) in enumerate(responses, 1):
                    out(f"CANARY_{card}_{index}_{method}", status)
    except Exception as error:
        out("BLOCKED_AT", phase)
        code = str(error)
        out("BLOCK_CODE", code if re.fullmatch(r"[A-Z][A-Z0-9_]{4,70}", code)
            else type(error).__name__.upper())
    out("CRM_WRITES", 0)

if __name__ == "__main__":
    main()
