"""Read-only acceptance of Bidzaar queue and exact saved CRM payloads."""
import os
import re
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_enrichment_worker import Rest,normalized
import httpx
with db(dict(os.environ)) as conn,httpx.Client(trust_env=False,timeout=20) as client:
    rest=Rest(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK'),client)
    from leadpilot.serviceit_stage34_signed_readonly_sidecar import request,owner_type
    user=request(client,rest.base,'user.get',{'ID':605})['result']
    matches=[u for u in user if str(u.get('ID'))=='605']
    if len(matches)==1:
        u=matches[0]
        print('MANAGER_605_NAME='+str(u.get('NAME',''))+' '+str(u.get('LAST_NAME','')),flush=True)
        print('MANAGER_605_ACTIVE='+str(u.get('ACTIVE')),flush=True)
    binding=owner_type(client,rest.base)
    for card in (11,29,33,35):
        tasks=request(client,rest.base,'tasks.task.list',{'filter':{'UF_CRM_TASK':binding+'_'+str(card)},'select':['ID','RESPONSIBLE_ID','UF_CRM_TASK'],'start':0})
        items=tasks['result']['tasks']
        exact=[t for t in items if binding+'_'+str(card) in t.get('ufCrmTask',t.get('UF_CRM_TASK',[]))]
        print('MANAGER_CARD_'+str(card)+'_TASKS='+str(len(exact)),flush=True)
        print('MANAGER_CARD_'+str(card)+'_ASSIGNEES='+','.join(str(t.get('responsibleId',t.get('RESPONSIBLE_ID'))) for t in exact),flush=True)
    for state,count in conn.execute('SELECT state,count(*) FROM serviceit_bidzaar_inbox GROUP BY state').fetchall():
        if state.isupper():print('BIDZAAR_COUNT_'+state+'='+str(count),flush=True)
    rows=conn.execute("SELECT card_id,expected FROM serviceit_bidzaar_inbox WHERE state='VERIFIED' ORDER BY card_id DESC LIMIT 30").fetchall()
    ok=True
    for card,expected in rows:
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        verified=all(str(actual.get(k))==str(v) for k,v in expected.items() if k!='title')
        ok &= verified
        print('BIDZAAR_CARD_'+str(card)+'_READBACK='+('YES' if verified else 'NO'),flush=True)
        print('BIDZAAR_CARD_'+str(card)+'_MANAGER_V2='+('YES' if actual.get('UF_CRM_13_SVCIT_SCORE_VERSION')=='svc-bidzaar-manager-v2' else 'NO'),flush=True)
        print('BIDZAAR_CARD_'+str(card)+'_PRIORITY_SYMBOL='+('YES' if str(actual.get('UF_CRM_13_SVCIT_AI_SCORE_BADGE','')).startswith(('✅','⚠','❌')) else 'NO'),flush=True)
        limits=actual.get('UF_CRM_13_SVCIT_AI_LIMITATIONS','') or ''
        for label,pattern in [('DOCS_READ',r'Прочитано документов: (\d+)'),('DOCS_UNPROCESSED',r'Не обработано: (\d+)')]:
            match=re.search(pattern,limits)
            print('BIDZAAR_CARD_'+str(card)+'_'+label+'='+(match[1] if match else 'UNKNOWN'),flush=True)
        report=actual.get('UF_CRM_13_SVCIT_AI_FULL_REPORT','') or ''
        print('BIDZAAR_CARD_'+str(card)+'_QUOTED_SOURCES='+str(report.count('Источник:')),flush=True)
        status=actual.get('UF_CRM_13_SVCIT_AI_STATUS','')
        print('BIDZAAR_CARD_'+str(card)+'_ROUTERAI='+('YES' if 'RouterAI: цитаты проверены' in status else 'NO'),flush=True)
    print('BIDZAAR_CRM_WRITES=0',flush=True)
    if not ok:raise SystemExit(2)
