"""Compare live fields with immutable intended patches, without printing contents."""
import os
import re
import httpx
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import _get, TABLE
from leadpilot.serviceit_b2b_dossier_consistency_v37 import MANAGER_HEADER, BRIEF, DECISION
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

def emit(card,key,val):
    print(f"MANAGER_CARD_{card}_{key}={val}",flush=True)

def main():
    env=dict(os.environ)
    with db(env) as conn, conn.transaction(), httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        rows=conn.execute(f"""
          SELECT DISTINCT ON(card_id) card_id,status,before_fields,after_fields
          FROM {TABLE} WHERE card_id=ANY(%s) ORDER BY card_id,attempt_no DESC
        """,([11,13,15,17,19,21,23,25,27],)).fetchall()
        for card,state,before,after in rows:
            actual=_get(rest,card)
            emit(card,"STATE",state)
            emit(card,"BRIEF_PRESENT","YES" if actual.get(BRIEF,"").startswith(MANAGER_HEADER) else "NO")
            for k in (SCORE_FIELD,VERSION_FIELD,BADGE_FIELD,DECISION):
                value=actual.get(k)
                emit(card,k.removeprefix(PREFIX)+"_TYPE",type(value).__name__.upper())
                emit(card,k.removeprefix(PREFIX)+"_PRESENT","NO" if value in (None,"") else "YES")
            for k, expected in (after or {}).items():
                seen=actual.get(k)
                if seen == expected:
                    continue
                label=k.removeprefix(PREFIX)
                if not re.fullmatch(r"[A-Z0-9_]+",label):
                    continue
                state=("UNCHANGED" if seen==before.get(k)
                       else "EMPTY" if seen in (None,"")
                       else "TYPE_ONLY" if str(seen)==str(expected)
                       else "NORMALIZED_WHITESPACE" if isinstance(seen,str) and isinstance(expected,str)
                         and seen.split()==expected.split()
                       else "OTHER")
                emit(card,"DIFF_"+label,state)

if __name__=="__main__":
    main()
