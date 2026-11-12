"""Read-only, bounded verification of original public source #4677923.

No CRM writes, no external paid search, no secrets or raw evidence in logs.
"""
import os
import re
from hashlib import sha256
from pathlib import Path

import httpx

from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_b2b_routerai_deep import b2b_primary_detail_facts, public_documents
from leadpilot.serviceit_vps_b2b_deep_research import verified_original_b2b_details
from leadpilot.serviceit_vps_worker import exact_environment

FILES = ("serviceit_b2b_public_mirrors.py", "serviceit_b2b_routerai_deep.py",
         "serviceit_vps_b2b_deep_research.py", "serviceit_vps_worker.py")


def emit(name, value):
    value = str(value).upper()
    if re.fullmatch(r"[A-Z0-9_]{1,85}", value):
        print("PRIMARY_DETAIL_" + name + "=" + value, flush=True)


def main():
    for name in FILES:
        source = Path("/app/leadpilot") / name
        emit("SHA_" + name.replace(".py", "").upper(),
             sha256(source.read_bytes()).hexdigest().upper())

    env = exact_environment(dict(os.environ))
    emit("LIVE_FLAG", env.get("SERVICEIT_VPS_B2B_PRIMARY_DETAIL_ENABLED", "NO"))
    with db(dict(os.environ)) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute("""
          SELECT state,card_id,notice_json FROM serviceit_vps_b2b_inbox
          WHERE source_id=%s
        """, ("B2B-CENTER-4677923",)).fetchone()
    if row is None or row[0] != "VERIFIED" or row[1] != 29:
        emit("CARD29_SOURCE", "NOT_VERIFIED")
        return
    emit("CARD29_SOURCE", "VERIFIED")
    notice = row[2]
    candidate = {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []}
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=16) as client:
        result = verified_original_b2b_details(candidate, notice, client)
    emit("CARD29_ORIGINAL", "VERIFIED" if result.get("original_b2b_detail_independently_read") else "NOT_VERIFIED")
    docs = public_documents(result)
    facts = b2b_primary_detail_facts(docs)
    cat = {x["category"] for x in facts}
    emit("CARD29_DEADLINE", "YES" if "DEADLINE" in cat else "NO")
    emit("CARD29_PAYMENT", "YES" if "PAYMENT" in cat else "NO")
    emit("CARD29_DELIVERY", "YES" if "LOCATION" in cat else "NO")
    emit("CARD29_FACT_COUNT", min(len(facts), 30))
    emit("CARD29_DOC_COUNT", min(len(docs), 8))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("ERROR", type(exc).__name__.upper())
        raise SystemExit(2) from None
