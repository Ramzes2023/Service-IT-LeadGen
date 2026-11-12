"""Apply the manager format to the nine source-attested cards inventoried live."""
import os

import httpx

from leadpilot.serviceit_b2b_dossier_consistency_v37 import (
    BRIEF, MANAGER_HEADER, SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, DECISION, INN,
)
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import _get, run_once
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment

TARGETS = (27, 25, 23, 21, 19, 17, 15, 13, 11)


def emit(key, value):
    print(f"MANAGER_{key}={value}", flush=True)


def main():
    env = exact_environment(dict(os.environ))
    with db(env) as conn, httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        rest = Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        all_ready = True
        for card in TARGETS:
            try:
                result = run_once(env,client,conn,target_ids={card},refresh=True)
                emit(f"CARD_{card}_RESULT",result["status"])
                item = _get(rest,card)
                ready = (item.get(BRIEF,"").startswith(MANAGER_HEADER)
                         and item.get(SCORE_FIELD) not in (None,"")
                         and bool(item.get(VERSION_FIELD)) and bool(item.get(BADGE_FIELD)))
                emit(f"CARD_{card}_READY","YES" if ready else "NO")
                emit(f"CARD_{card}_INN","YES" if item.get(INN) else "NO")
                emit(f"CARD_{card}_DECISION","PRESERVED" if item.get(DECISION) else "EMPTY")
                if card == 27:
                    emit("CARD_27_RELATED","YES" if "4672286" in item.get(BRIEF,"") else "NO")
                all_ready &= ready
            except Exception as exc:
                emit(f"CARD_{card}_ERROR",type(exc).__name__.upper())
                all_ready = False
        emit("ALL_READY","YES" if all_ready else "NO")
        return 0 if all_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
