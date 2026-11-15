"""Tender review presentation and bounded deadline repair for linked tasks."""
from datetime import datetime,timezone
from .serviceit_tender_manager_timing import submission_time,moscow_time,manager_deadline
from .serviceit_stage34_signed_readonly_sidecar import owner_type
from .serviceit_kanban_presentation import SCORE_FIELD

DDL='''CREATE TABLE IF NOT EXISTS serviceit_tender_task_deadlines (
 task_id BIGINT PRIMARY KEY,card_id BIGINT NOT NULL,submission TEXT NOT NULL,
 expected TEXT NOT NULL,status TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT now());'''


def summary(notice,expected):
    try:deadline=moscow_time(notice['acceptanceEndDate'])
    except (ValueError,KeyError,TypeError):deadline='Часовой пояс или срок не подтверждён'
    score=expected.get(SCORE_FIELD,'не рассчитано')
    return '\n'.join(['РЕШЕНИЕ ОБ УЧАСТИИ',f'Совпадение с нашими услугами: {score}/100.',
        'Готовность к участию: НЕ ПОДТВЕРЖДЕНА. Высокое совпадение с услугами не означает допуск.',
        'Проверить ПКО, обязательные сертификаты, опыт аналогичных работ, ресурсы, обеспечение и условия оплаты по документам. Сопоставление с возможностями нашей компании должен подтвердить менеджер.',
        'Подача до: '+deadline,'Бюджет: '+(str(notice.get('price'))+' '+str(notice.get('currency') or '') if isinstance(notice.get('price'),(int,float)) and not isinstance(notice.get('price'),bool) and notice['price']>0 else 'не раскрыт в API; искать в документах. Нулевое значение не является бюджетом.')])


def api(client,base,method,args):
    if method not in ('tasks.task.list','tasks.task.get','tasks.task.update'):raise ValueError('TASK_METHOD_INVALID')
    if method=='tasks.task.get':args={**args,'select':['ID','TITLE','RESPONSIBLE_ID','UF_CRM_TASK','STATUS','DEADLINE','CREATED_DATE']}
    response=client.post(base+'/'+method+'.json',json=args,timeout=20,follow_redirects=False)
    response.raise_for_status();body=response.json()
    if 'error' in body or 'result' not in body:raise ValueError('TASK_RESPONSE_INVALID')
    return body['result']


def reconcile_created_task(conn,client,base,card,task_id,binding,end):
    """Reconcile a visible task after ambiguous creation; never create another."""
    row=conn.execute("SELECT r.reserved_at,b.expected FROM serviceit_vps_native_receipts r JOIN serviceit_bidzaar_inbox b USING(card_id) WHERE r.namespace='TASK_V1' AND r.status='UNCERTAIN' AND r.card_id=%s",(card,)).fetchone()
    if not row:return
    from datetime import timedelta
    from .serviceit_b2b_dossier_consistency_v37 import SUMMARY
    reserved,expected=row
    task=api(client,base,'tasks.task.get',{'taskId':task_id})['task']
    title=('Проработать закупку: '+str(expected.get(SUMMARY) or expected.get('title') or 'Закупка'))[:230]
    if task.get('title',task.get('TITLE'))!=title:return
    if task.get('ufCrmTask',task.get('UF_CRM_TASK'))!=[binding+'_'+str(card)]:return
    if str(task.get('responsibleId',task.get('RESPONSIBLE_ID')))!='605':return
    try:
        created=submission_time(task.get('createdDate',task.get('CREATED_DATE')))
        due=submission_time(task.get('deadline',task.get('DEADLINE')))
    except (ValueError,TypeError,AttributeError):return
    if not reserved-timedelta(seconds=1)<=created<=reserved+timedelta(minutes=10) or due>=end:return
    conn.execute("UPDATE serviceit_vps_native_receipts SET status='VERIFIED',updated_at=now() WHERE namespace='TASK_V1' AND status='UNCERTAIN' AND card_id=%s",(card,))
    print('EXT_TASK_CREATION_RECONCILED=1',flush=True)


def repair_deadlines(conn,client,base):
    conn.execute(DDL);binding=owner_type(client,base);updated=0
    stats={'OPEN_TENDERS':0,'LISTED':0,'LINKED':0,'OLGA':0,'ACTIVE':0,'ALREADY_BEFORE':0,'INVALID_ZONE':0}
    for card,notice in conn.execute("SELECT card_id,notice FROM serviceit_bidzaar_inbox WHERE state='VERIFIED' ORDER BY card_id").fetchall():
        raw=notice.get('acceptanceEndDate')
        try:
            end=submission_time(raw);plan=manager_deadline(raw,datetime.now(timezone.utc))
        except (ValueError,TypeError,AttributeError):continue
        if not plan['deadline']:continue
        stats['OPEN_TENDERS']+=1
        result=api(client,base,'tasks.task.list',{'filter':{'UF_CRM_TASK':binding+'_'+str(card)},'select':['ID','RESPONSIBLE_ID','UF_CRM_TASK','STATUS','DEADLINE'],'order':{'ID':'ASC'},'start':0})
        tasks=result.get('tasks') if isinstance(result,dict) else None
        if not isinstance(tasks,list):raise ValueError('TASK_LIST_INVALID')
        for offset in range(50,501,50):
            if len(tasks)<offset:break
            page=api(client,base,'tasks.task.list',{'filter':{'UF_CRM_TASK':binding+'_'+str(card)},'select':['ID','RESPONSIBLE_ID','UF_CRM_TASK','STATUS','DEADLINE'],'order':{'ID':'ASC'},'start':offset})
            more=page.get('tasks') if isinstance(page,dict) else None
            if not isinstance(more,list):raise ValueError('TASK_LIST_INVALID')
            tasks.extend(more)
        else:raise ValueError('TASK_LIST_PAGINATION_REQUIRED')
        stats['LISTED']+=len(tasks)
        for task in tasks:
            links=task.get('ufCrmTask',task.get('UF_CRM_TASK',[]))
            if not isinstance(links,list) or binding+'_'+str(card) not in links:continue
            stats['LINKED']+=1
            if str(task.get('responsibleId',task.get('RESPONSIBLE_ID')))!='605':continue
            stats['OLGA']+=1
            if str(task.get('status',task.get('STATUS'))) in ('4','5','6','7'):continue
            stats['ACTIVE']+=1
            tid=int(task.get('id',task.get('ID')))
            saved=conn.execute('SELECT submission,expected,status FROM serviceit_tender_task_deadlines WHERE task_id=%s',(tid,)).fetchone()
            current=task.get('deadline',task.get('DEADLINE'))
            if saved and saved[2]=='UNCERTAIN':
                if current:
                    observed=submission_time(current)
                    print('EXT_TASK_OBSERVED_UTC='+observed.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S'),flush=True)
                    print('EXT_TASK_REQUESTED_UTC='+submission_time(saved[1]).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S'),flush=True)
                    if datetime.now(timezone.utc)<observed<end:conn.execute("UPDATE serviceit_tender_task_deadlines SET status='VERIFIED',expected=%s WHERE task_id=%s",(current,tid))
                continue
            try:
                if current and submission_time(current)<end:
                    reconcile_created_task(conn,client,base,card,tid,binding,end)
                    stats['ALREADY_BEFORE']+=1;continue
            except (ValueError,TypeError):
                stats['INVALID_ZONE']+=1;continue
            latest=api(client,base,'tasks.task.get',{'taskId':tid})['task']
            latest_due=latest.get('deadline',latest.get('DEADLINE'))
            if bool(latest_due)!=bool(current):continue
            if current and submission_time(latest_due)!=submission_time(current):continue
            if binding+'_'+str(card) not in latest.get('ufCrmTask',latest.get('UF_CRM_TASK',[])):continue
            if str(latest.get('responsibleId',latest.get('RESPONSIBLE_ID')))!='605':continue
            if str(latest.get('status',latest.get('STATUS'))) in ('4','5','6','7'):continue
            due=plan['deadline']
            conn.execute("INSERT INTO serviceit_tender_task_deadlines(task_id,card_id,submission,expected,status) VALUES(%s,%s,%s,%s,'UNCERTAIN') ON CONFLICT(task_id) DO UPDATE SET submission=excluded.submission,expected=excluded.expected,status='UNCERTAIN',updated_at=now()",(tid,card,raw,due))
            api(client,base,'tasks.task.update',{'taskId':tid,'fields':{'DEADLINE':due}})
            actual=api(client,base,'tasks.task.get',{'taskId':tid})['task']
            observed=actual.get('deadline',actual.get('DEADLINE'))
            print('EXT_TASK_OBSERVED_UTC='+submission_time(observed).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S'),flush=True)
            print('EXT_TASK_REQUESTED_UTC='+submission_time(due).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S'),flush=True)
            if not datetime.now(timezone.utc)<submission_time(observed)<end:raise ValueError('TASK_DEADLINE_READBACK_FAILED')
            conn.execute('UPDATE serviceit_tender_task_deadlines SET expected=%s WHERE task_id=%s',(observed,tid))
            conn.execute("UPDATE serviceit_tender_task_deadlines SET status='VERIFIED',updated_at=now() WHERE task_id=%s",(tid,));updated+=1
            reconcile_created_task(conn,client,base,card,tid,binding,end)
    for key,value in stats.items():print('EXT_TASK_'+key+'='+str(value),flush=True)
    print('EXT_TASK_DEADLINES_UPDATED='+str(updated),flush=True)
