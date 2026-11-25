"""Exclude source-owned non-IT notices without deleting CRM history."""
import json,re
from .serviceit_bidzaar_source import qualified_groups
from .serviceit_enrichment_worker import Rest,normalized
from .serviceit_b2b_dossier_consistency_v37 import PREFIX
from .serviceit_kanban_presentation import SCORE_FIELD,BADGE_FIELD


def request(client,base,method,args):
    if method not in ('crm.status.list','crm.item.update','tasks.task.list','tasks.task.get','tasks.task.update','tasks.task.complete'):raise ValueError('SCOPE_METHOD_INVALID')
    r=client.post(base+'/'+method+'.json',json=args,timeout=20,follow_redirects=False);r.raise_for_status();body=r.json()
    if 'error' in body or 'result' not in body:raise ValueError('SCOPE_RESPONSE_INVALID')
    return body['result']


def failed_stage(rows):
    choices=[]
    for r in rows:
        sid=str(r.get('STATUS_ID',''));sem=str((r.get('EXTRA') or {}).get('SEMANTICS',r.get('SEMANTICS')) or '').lower()
        if re.fullmatch(r'DT1062_21:[A-Za-z0-9_]+',sid) and sem in ('f','failure','apology'):choices.append(r)
    if not choices:raise ValueError('SCOPE_REJECTION_STAGE_UNAVAILABLE')
    return sorted(choices,key=lambda r:(not bool(re.search('нецел|неподход|отклон',r.get('NAME',''),re.I)),int(r.get('SORT',0))))[0]['STATUS_ID']


def run(conn,client,base):
    rows=conn.execute("SELECT source_id,card_id,notice,expected,state FROM serviceit_bidzaar_inbox WHERE state IN ('VERIFIED','UPDATE_UNCERTAIN','EXCLUDED') AND card_id IS NOT NULL ORDER BY card_id").fetchall()
    rejected=[r for r in rows if not qualified_groups(r[2])]
    if not rejected:return
    stages=request(client,base,'crm.status.list',{'filter':{'ENTITY_ID':'DYNAMIC_1062_STAGE_21'}})
    target=failed_stage(stages);rest=Rest(base,client);count=0
    from .serviceit_stage34_signed_readonly_sidecar import owner_type
    binding=owner_type(client,base)
    for source,card,notice,expected,state in rejected:
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        if str(actual.get('categoryId'))!='21' or actual.get(PREFIX+'SIGNAL_ID')!=source:continue
        fields={'stageId':target,SCORE_FIELD:0,BADGE_FIELD:'ИСКЛЮЧЁН: предмет закупки не соответствует услугам Сервис ИТ',PREFIX+'SUGGESTED_SERVICE':'Не соответствует нашим направлениям',PREFIX+'AI_NEXT_ACTION':'Не прорабатывать: основная закупка непрофильная. Совпадение отдельных товарных позиций не является ИТ-потребностью.',PREFIX+'STOP_REASON':'Непрофильный предмет закупки'}
        if state!='EXCLUDED' or any(str(actual.get(k))!=str(v) for k,v in fields.items()):
            combined={**(expected or {}),**fields}
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='UPDATE_UNCERTAIN',expected=%s::jsonb,reason='NON_IT_SUBJECT' WHERE source_id=%s",(json.dumps(combined,ensure_ascii=False),source))
            request(client,base,'crm.item.update',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y','fields':fields})
            after=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
            if any(str(after.get(k))!=str(v) for k,v in fields.items()):raise ValueError('SCOPE_REJECTION_READBACK_FAILED')
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='EXCLUDED',reason='NON_IT_SUBJECT',updated_at=now() WHERE source_id=%s",(source,));count+=1
        if conn.execute("SELECT to_regclass('serviceit_extended_documents')").fetchone()[0]:
            conn.execute("UPDATE serviceit_extended_documents SET status='EXCLUDED',error='NON_IT_SUBJECT' WHERE card_id=%s AND status!='EXCLUDED'",(card,))
        # Finish only our exact linked automatic review task as rejected triage.
        if not conn.execute("SELECT 1 FROM serviceit_vps_native_receipts WHERE namespace='TASK_V1' AND card_id=%s",(card,)).fetchone():continue
        tasks=request(client,base,'tasks.task.list',{'filter':{'UF_CRM_TASK':binding+'_'+str(card)},'select':['ID','TITLE','RESPONSIBLE_ID','UF_CRM_TASK','STATUS'],'start':0})['tasks']
        for task in tasks:
            if task.get('ufCrmTask')!=[binding+'_'+str(card)] or str(task.get('responsibleId'))!='605' or str(task.get('status')) not in ('2','3'):continue
            if not str(task.get('title','')).startswith(('Проработать закупку:','Отклонено: непрофильная закупка — ')):continue
            tid=int(task['id'])
            request(client,base,'tasks.task.update',{'taskId':tid,'fields':{'TITLE':('Отклонено: непрофильная закупка — '+str(notice.get('name','')))[:230]}})
            request(client,base,'tasks.task.complete',{'taskId':tid})
            after=request(client,base,'tasks.task.get',{'taskId':tid,'select':['ID','STATUS']})['task']
            if str(after.get('status')) not in ('4','5'):raise ValueError('SCOPE_TASK_CLOSURE_UNVERIFIED')
        print('BIDZAAR_EXCLUDED_CARD='+str(card),flush=True)
    print('BIDZAAR_EXCLUDED_NON_IT='+str(count),flush=True)
