"""Apply the deployed generic reconciler to existing new signed B2B cards."""
import os
import traceback
import re
import httpx
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import run_once, _get
from leadpilot.serviceit_b2b_dossier_consistency_v37 import BRIEF, INN, DECISION, MANAGER_HEADER
from leadpilot.serviceit_enrichment_worker import Rest


def emit(k,v):
    value=str(v).upper()
    if re.fullmatch(r'[A-Z0-9_]{1,100}',value):
        print('DEPTH_APPLY_'+k+'='+value,flush=True)


def main():
    env=exact_environment(dict(os.environ))
    with db(env) as conn, httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
        rows=conn.execute('''SELECT b.card_id FROM serviceit_vps_b2b_inbox b
            JOIN serviceit_vps_b2b_dossier_v11_receipts v ON b.card_id=v.card_id
            WHERE b.state='VERIFIED' AND v.status='VERIFIED' AND b.card_id>=11
            ORDER BY b.card_id LIMIT 20''').fetchall()
        ok=bool(rows)
        for (card,) in rows:
            prior=_get(rest,card)
            try:
                result=run_once(env,client,conn,target_ids={card},refresh=True)
                emit(f'CARD_{card}_RESULT',result['status'])
                item=_get(rest,card)
                brief=item.get(BRIEF) or ''
                ready=(brief.startswith(MANAGER_HEADER)
                       and bool(re.search(r'Срок подачи заявок: \d{2}\.\d{2}\.20\d{2} \d{2}:\d{2}', brief))
                       and item.get(DECISION)==prior.get(DECISION))
                emit(f'CARD_{card}_READBACK','YES' if ready else 'NO')
                emit(f'CARD_{card}_INN','YES' if item.get(INN) else 'NO')
                ok &= ready
            except Exception as exc:
                emit(f'CARD_{card}_ERROR',type(exc).__name__)
                emit(f'CARD_{card}_ERROR_LINE',traceback.extract_tb(exc.__traceback__)[-1].lineno)
                ok=False
        emit('ALL_READBACK','YES' if ok else 'NO')
        return 0 if ok else 2

if __name__=='__main__':
    raise SystemExit(main())
